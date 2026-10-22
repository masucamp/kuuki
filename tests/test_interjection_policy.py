"""Threshold-table behaviour and the Strands handler, with synthetic probabilities (no model)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from strands.interventions import Deny, Proceed, Transform

from patterns.interjection_policy import PROBES, DeciderResult, InterjectionHandler, InterjectionPolicy

BASE = {k: 0.1 for k in PROBES}


def probs(**over: float) -> dict[str, float]:
    return {**BASE, **over}


@pytest.fixture
def policy() -> InterjectionPolicy:
    return InterjectionPolicy()


def test_quiet_room_stays_silent(policy):
    d = policy.decide(probs())
    assert (d.move, d.delivery, d.tatemae) == ("stay_silent", None, False)


def test_correct_a_peer_directly(policy):
    d = policy.decide(probs(has_error=0.6), recent_senior=[False])
    assert (d.move, d.delivery) == ("correct", "direct")


def test_correct_a_senior_privately_unless_very_sure(policy):
    assert policy.decide(probs(has_error=0.6), recent_senior=[True]).delivery == "private_dm_later"
    assert policy.decide(probs(has_error=0.9), recent_senior=[True]).delivery == "direct"


def test_clarify_looks_one_utterance_back_for_seniority(policy):
    # the senior gave the vague instruction, the junior replied "は、はい"
    d = policy.decide(probs(is_vague=0.7), recent_senior=[False, True])
    assert (d.move, d.delivery) == ("clarify", "private_dm_later")
    # a correction only concerns the latest speaker
    assert policy.decide(probs(has_error=0.6), recent_senior=[False, True]).delivery == "direct"


def test_invite_quiet_needs_an_open_question_and_not_small_talk(policy):
    assert policy.decide(probs(needs_silent_expert=0.8)).move == "stay_silent"
    assert policy.decide(probs(needs_silent_expert=0.8, open_question=0.9)).move == "invite_quiet"
    assert policy.decide(probs(needs_silent_expert=0.8, open_question=0.9, off_agenda=0.8)).move != "invite_quiet"


def test_refocus_needs_two_off_agenda_utterances_in_a_row(policy):
    assert policy.decide(probs(off_agenda=0.8)).move == "stay_silent"
    assert policy.decide(probs(off_agenda=0.8)).move == "refocus"
    assert policy.decide(probs(off_agenda=0.8)).move == "stay_silent"  # acted; count again


def test_refocus_streak_resets_between_conversations(policy):
    policy.decide(probs(off_agenda=0.8))
    policy.reset()
    assert policy.decide(probs(off_agenda=0.8)).move == "stay_silent"


def test_tatemae_only_for_replies(policy):
    assert policy.decide(probs(polite_refusal=0.9)).tatemae is False
    assert policy.decide(probs(polite_refusal=0.9, is_response_to_request=0.8)).tatemae is True


def test_escalate_only_uncertain_silence(policy):
    d = policy.decide(probs(is_vague=0.5))
    assert d.move == "stay_silent" and policy.should_escalate(d) == ["is_vague"]
    # a confident action is not second-guessed
    d = policy.decide(probs(is_vague=0.5, needs_silent_expert=0.8, open_question=0.9))
    assert d.move == "invite_quiet" and policy.should_escalate(d) == []
    # factual-error gray zone only matters when facts are known
    d = policy.decide(probs(has_error=0.2))
    assert policy.should_escalate(d, has_facts=False) == []
    assert policy.should_escalate(d, has_facts=True) == ["has_error"]


# --------------------------------------------------------------------------- handler


class FakeDecider:
    def __init__(self, p: dict[str, float]) -> None:
        self.p = p

    def ask(self, state, questions) -> DeciderResult:
        return DeciderResult(probs=self.p, answers={}, server_ms=1.0, wall_ms=1.0)


@dataclass
class FakeRoom:
    has_facts: bool = True
    seniors: list[bool] = field(default_factory=lambda: [False])

    def render(self) -> str:
        return "state"

    def recent_senior(self) -> list[bool]:
        return self.seniors


@dataclass
class FakeEvent:
    invocation_state: dict
    messages: list = field(default_factory=lambda: [{"role": "user", "content": [{"text": "room"}]}])


def run(p: dict[str, float]):
    handler = InterjectionHandler(decider=FakeDecider(p))
    event = FakeEvent({"room": FakeRoom()})
    return handler.before_invocation(event), event


def test_handler_denies_silence():
    action, event = run(probs())
    assert isinstance(action, Deny)
    assert event.invocation_state["interjection"].action == "silent"


def test_handler_appends_the_decision():
    action, event = run(probs(has_error=0.6))
    assert isinstance(action, Transform)
    action.apply(event)
    assert "move=correct" in event.messages[-1]["content"][-1]["text"]


def test_handler_escalates_gray_zone():
    action, event = run(probs(is_vague=0.5))
    action.apply(event)
    assert "System 1 was unsure" in event.messages[-1]["content"][-1]["text"]
    assert event.invocation_state["interjection"].action == "escalate"


def test_handler_without_room_proceeds():
    handler = InterjectionHandler(decider=FakeDecider(probs()))
    assert isinstance(handler.before_invocation(FakeEvent({})), Proceed)
