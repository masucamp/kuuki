"""One meeting, one utterance at a time, as a stream of events.

Shared by the CLI replay (agent/pipeline.py) and the web UI (app/server.py), so both show
exactly the same decisions. Each step yields plain dicts:

    utterance -> probes -> [escalating -> judge] -> decision -> [kuuki ...]

and `finish()` yields the after-meeting follow-ups and a summary.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterator

from agent.llm_judge import LLMJudge
from agent.room_state import Meeting, RoomState, Utterance
from agent.writer import TASKS, Writer
from patterns.interjection_policy.decider import DeciderClient
from patterns.interjection_policy.policy import Decision, InterjectionPolicy

# USD per 1M tokens, Anthropic list price (Bedrock regional pricing may differ slightly)
PRICE = {"sonnet": (2.00, 10.00), "haiku": (0.10, 0.50)}


class MeetingEngine:
    def __init__(
        self,
        meeting: Meeting,
        decider: DeciderClient,
        policy: InterjectionPolicy,
        writer: Writer | None,
        judge: LLMJudge | None,
        english: bool = False,
    ) -> None:
        self.meeting = meeting
        self.room = RoomState(meeting)
        self.decider, self.policy, self.writer, self.judge = decider, policy, writer, judge
        self.english = english  # also produce English subtitles for Kuuki's lines (web UI)
        self.policy.reset()
        self.followups: list[dict] = []
        self.decider_ms: list[float] = []
        self.routes = {"silent": 0, "act": 0, "escalate": 0}
        self.llm_calls = 0
        self.cost = 0.0

    # ------------------------------------------------------------------ setup info

    def meeting_event(self) -> dict:
        m = self.meeting
        cfg = self.policy.cfg
        thresholds = {r["probe"]: r["threshold"] for r in cfg["moves"]}
        for r in cfg["moves"]:
            thresholds.update(r.get("requires", {}))
        thresholds[cfg["tatemae"]["gate_probe"]] = cfg["tatemae"]["gate_threshold"]
        thresholds[cfg["tatemae"]["probe"]] = cfg["tatemae"]["threshold"]
        return {
            "type": "meeting",
            "title": m.title,
            "agenda": m.agenda,
            "duration_min": m.duration_min,
            "facts": m.facts,
            "title_en": m.title_en,
            "agenda_en": m.agenda_en,
            "facts_en": m.facts_en,
            "participants": [vars(p) for p in m.participants.values()],
            "probes": [
                {"id": k, "threshold": thresholds.get(k), "gray": cfg.get("escalate", {}).get(k, {}).get("range")}
                for k in self.policy.questions
            ],
            "judge": self.judge.model if self.judge else None,
            "writer": self.writer.model if self.writer else None,
            "count": len(m.utterances),
        }

    # ------------------------------------------------------------------ per utterance

    def step(self, u: Utterance) -> Iterator[dict]:
        room = self.room
        room.add(u)
        i = len([x for x in room.history if x.speaker != "kuuki"]) - 1
        speaker = room.speaker(u)
        yield {"type": "utterance", "i": i, "minute": u.minute, "speaker": u.speaker, "text": u.text,
               "en": getattr(u, "en", None)}

        result = self.decider.ask(room.render(), self.policy.questions)
        self.decider_ms.append(result.server_ms)
        d = self.policy.decide(result.probs, recent_senior=room.recent_senior())
        yield {"type": "probes", "i": i, "probs": result.probs, "ms": result.server_ms}

        gray = self.policy.should_escalate(d, has_facts=room.has_facts) if self.judge else []
        route = "escalate" if gray else ("silent" if d.move == "stay_silent" and not d.tatemae else "act")
        judge_ms = None
        if gray:
            yield {"type": "escalating", "i": i, "gray": {g: result.probs[g] for g in gray}, "model": self.judge.model}
            d, j = self._escalate(d, gray)
            judge_ms = j.latency_ms
            self._spend(self.judge.model, j.input_tokens, j.output_tokens)
            yield {"type": "judge", "i": i, "model": self.judge.model, "ms": j.latency_ms, "move": d.move,
                   "delivery": d.delivery, "tatemae": d.tatemae, "reason": j.reason}
        self.routes[route] += 1

        yield {"type": "decision", "i": i, "route": route, "move": d.move, "delivery": d.delivery,
               "tatemae": d.tatemae, "reasons": d.reasons, "decider_ms": result.server_ms, "judge_ms": judge_ms,
               "cost_usd": self.cost}

        if d.move != "stay_silent" and self.writer:
            w = self.writer.write(room.render(include_ai=True), d.move, d.delivery, english=self.english)
            self._spend(self.writer.model, w.input_tokens, w.output_tokens)
            if d.delivery == "private_dm_later":
                item = {"kind": "dm", "to": speaker.name, "to_en": speaker.name_en, "text": w.text, "en": w.en, "move": d.move}
                self.followups.append(item)
                yield {"type": "kuuki", "i": i, "ms": w.latency_ms, "cost_usd": self.cost, **item}
            else:
                room.add_ai(w.text)
                yield {"type": "kuuki", "i": i, "kind": "room", "text": w.text, "en": w.en, "move": d.move,
                       "ms": w.latency_ms, "cost_usd": self.cost}
        if d.tatemae and self.writer:
            w = self.writer.air_translate(room.render(include_ai=True))
            self._spend(self.writer.model, w.input_tokens, w.output_tokens)
            air, follow, follow_en = _split_air(w.text)
            overseas = [p.name_en or p.name for p in self.meeting.participants.values() if p.overseas]
            yield {"type": "kuuki", "i": i, "kind": "air", "text": air, "to": overseas, "ms": w.latency_ms,
                   "cost_usd": self.cost}
            if follow:
                item = {"kind": "followup", "to": speaker.name, "to_en": speaker.name_en, "text": follow, "en": follow_en,
                        "move": "tatemae"}
                self.followups.append(item)
                yield {"type": "kuuki", "i": i, **item}

    def finish(self) -> Iterator[dict]:
        n = sum(self.routes.values())
        yield {
            "type": "done",
            "followups": self.followups,
            "decisions": n,
            "routes": self.routes,
            "decider_median_ms": statistics.median(self.decider_ms) if self.decider_ms else None,
            "llm_calls": self.llm_calls,
            "cost_usd": self.cost,
        }

    # ------------------------------------------------------------------ helpers

    def _escalate(self, d: Decision, gray: list[str]):
        """Let the LLM judge decide instead of the thresholds."""
        j = self.judge.judge(self.room.render(mark_senior=True, include_ai=True))
        move, delivery = j.move, j.delivery
        if move not in self.policy.cfg["delivery"]["default"]:
            move, delivery = "stay_silent", None
        elif (move, delivery) not in TASKS:
            delivery = self.policy.cfg["delivery"]["default"][move]  # unusable combination from the LLM
        reasons = [f"gray zone: {', '.join(gray)} -> {self.judge.model}: {j.reason}"]
        return Decision(move=move, delivery=delivery, tatemae=j.tatemae, probs=d.probs, reasons=reasons), j

    def _spend(self, model: str, tokens_in: int, tokens_out: int) -> None:
        pin, pout = PRICE[model]
        self.llm_calls += 1
        self.cost += (tokens_in * pin + tokens_out * pout) / 1e6


def _split_air(text: str) -> tuple[str, str | None, str | None]:
    """Writer output (AIR / FOLLOWUP_JA / FOLLOWUP_EN lines) -> (air translation, follow-up ja, follow-up en)."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        label, sep, rest = line.strip().lstrip("*").partition(":")
        if sep and label.strip("* ").upper() in ("AIR", "FOLLOWUP_JA", "FOLLOWUP_EN"):
            found[label.strip("* ").upper()] = rest.strip(" *")
    if "AIR" not in found:  # unlabeled answer: show it as the air translation
        return " ".join(text.split()), None, None
    return found["AIR"], found.get("FOLLOWUP_JA"), found.get("FOLLOWUP_EN")
