"""Strands integration: gate each agent invocation with the interjection policy.

The agent is an AI teammate that is invoked once per utterance in a conversation it is
listening to. Before the model is called, `InterjectionHandler.before_invocation`:

1. asks strands-decider the typed probes (one local batch, a few hundred ms),
2. turns the probabilities into a decision with the threshold table (policy.py), and
3. returns one of three typed actions:

   - Deny:      the policy says stay silent -> the invocation is cancelled, no model call
   - Transform: the policy is confident    -> the decided move is appended to the prompt,
                                              the model only writes the words
   - Transform: a probe is in its gray zone -> the model is told System 1 was unsure and
                                              decides itself (System 2), replying SILENT
                                              if no interjection is needed

The caller passes the conversation through `invocation_state["room"]` (see RoomView).

    agent = Agent(model=..., system_prompt=..., interventions=[InterjectionHandler()])
    result = agent(prompt, invocation_state={"room": room})
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from strands.interventions import Deny, InterventionHandler, Proceed, Transform

from .decider import DeciderClient
from .policy import Decision, InterjectionPolicy

SILENT = "SILENT"


class RoomView(Protocol):
    """What the handler needs to know about the conversation."""

    @property
    def has_facts(self) -> bool:
        """Whether reference facts are known (gates the factual-error escalation)."""
        ...

    def render(self) -> str:
        """Conversation state for strands-decider. Keep it to human utterances."""
        ...

    def recent_senior(self) -> list[bool]:
        """Whether the speakers of the recent utterances are senior, latest first."""
        ...


@dataclass
class Verdict:
    action: str  # silent | act | escalate
    decision: Decision
    gray: list[str] = field(default_factory=list)
    decider_ms: float = 0.0


def _directive(d: Decision) -> str:
    parts = [f"move={d.move}"]
    if d.delivery:
        parts.append(f"delivery={d.delivery}")
    if d.tatemae:
        parts.append("tatemae=true")
    return (
        "[interjection policy] System 1 decided: " + ", ".join(parts) + ". "
        "Carry out exactly this decision; do not choose a different move. Output only the words."
    )


def _escalation(gray: list[str], d: Decision) -> str:
    probes = ", ".join(f"{g}={d.probs[g]:.2f}" for g in gray)
    return (
        f"[interjection policy] System 1 was unsure ({probes}). Decide yourself, applying the rules "
        f"strictly to the latest utterance only. Reply with exactly {SILENT} unless a move clearly "
        f"applies, or the latest utterance is itself a reply that is a polite no. Usually the answer is {SILENT}."
    )


def _append_text(text: str):
    """Transform.apply that appends a text block to the last user message of the invocation."""

    def apply(event: Any) -> None:
        messages = event.messages or []
        for message in reversed(messages):
            if message.get("role") == "user":
                message["content"] = [*message.get("content", []), {"text": text}]
                return
        messages.append({"role": "user", "content": [{"text": text}]})
        event.messages = messages

    return apply


class InterjectionHandler(InterventionHandler):
    """Decide whether, how and with what move an AI teammate speaks, before any model call."""

    name = "interjection-policy"

    def __init__(
        self,
        decider: DeciderClient | None = None,
        policy: InterjectionPolicy | None = None,
        room_key: str = "room",
        escalate: bool = True,
    ) -> None:
        self.decider = decider or DeciderClient()
        self.policy = policy or InterjectionPolicy()
        self.room_key = room_key
        self.escalate = escalate
        self.log: list[Verdict] = []

    def reset(self) -> None:
        """Call at the start of each conversation (clears `consecutive` streaks and the log)."""
        self.policy.reset()
        self.log.clear()

    def before_invocation(self, event: Any, **kwargs: Any):
        room: RoomView | None = event.invocation_state.get(self.room_key)
        if room is None:
            return Proceed(reason=f"no {self.room_key!r} in invocation_state; policy not applied")

        result = self.decider.ask(room.render(), self.policy.questions)
        d = self.policy.decide(result.probs, recent_senior=room.recent_senior())
        gray = self.policy.should_escalate(d, has_facts=room.has_facts) if self.escalate else []

        if gray:
            verdict = Verdict("escalate", d, gray, result.server_ms)
            action = Transform(apply=_append_text(_escalation(gray, d)), reason="gray zone")
        elif d.move == "stay_silent" and not d.tatemae:
            verdict = Verdict("silent", d, [], result.server_ms)
            action = Deny(reason="interjection policy: stay silent")
        else:
            verdict = Verdict("act", d, [], result.server_ms)
            action = Transform(apply=_append_text(_directive(d)), reason="; ".join(d.reasons))

        self.log.append(verdict)
        event.invocation_state["interjection"] = verdict
        return action
