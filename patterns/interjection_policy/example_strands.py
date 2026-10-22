"""Replay a meeting script through a Strands agent gated by InterjectionHandler.

    strands-decider serve <checkpoint> --port 8000
    uv run --env-file .env python -m patterns.interjection_policy.example_strands scenarios/demo_meeting_ja.yaml

The agent is invoked once per utterance. Most invocations are cancelled by the handler
before any model call; the rest arrive with the decided move, or with "System 1 was unsure".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3
from strands import Agent
from strands.models import BedrockModel

from agent.room_state import Meeting, RoomState
from patterns.interjection_policy.handler import SILENT, InterjectionHandler
from patterns.interjection_policy.prompts import MOVE_RULES

SYSTEM_PROMPT = f"""You are Kuuki, an AI teammate listening to a Japanese business meeting.
You are invoked after each utterance. An interjection policy runs before you and appends its
decision to the message. If it decided, carry that out. If it says System 1 was unsure,
decide yourself with these rules, and reply exactly {SILENT} when the move is stay_silent
and tatemae is false.

{MOVE_RULES}

How to write it. Start with one tag, then only the words:
- [room] direct / cushioned: one short polite Japanese sentence said in the meeting
- [dm] private_dm_later: a private Japanese message to that person for after the meeting,
  at most 2 sentences, no greeting or signature
- [air] tatemae: one plain sentence IN ENGLISH for overseas members explaining what the
  speaker most likely means, framed as cultural context
Never mock anyone."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--profile", default=None, help="AWS profile (default: AWS_PROFILE or the default chain)")
    ap.add_argument("--model-id", default="global.anthropic.claude-sonnet-5-5")
    args = ap.parse_args()

    model = BedrockModel(
        boto_session=boto3.Session(profile_name=args.profile, region_name="ap-northeast-1"),
        model_id=args.model_id,
        max_tokens=300,
        additional_request_fields={"thinking": {"type": "between_tools"}},
    )
    handler = InterjectionHandler()
    agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, interventions=[handler], callback_handler=None)

    meeting = Meeting.load(args.scenario)
    room = RoomState(meeting)
    handler.reset()
    for u in meeting.utterances:
        room.add(u)
        agent.messages.clear()  # each utterance is judged on the rendered room, not on chat history
        prompt = room.render(mark_senior=True, include_ai=True)
        result = agent(prompt, invocation_state={"room": room})
        v = handler.log[-1]

        print(f"\n[{u.minute:>2} min] {room.speaker(u).name_en or room.speaker(u).name}: {u.text}")
        label = {"silent": "stay silent (no model call)", "act": "decided by System 1", "escalate": "unsure -> the LLM decides"}[v.action]
        d = v.decision
        act = (f"{d.move}/{d.delivery}" if d.move != "stay_silent" else "") + (" +tatemae" if d.tatemae else "")
        detail = act.strip() if v.action == "act" else ", ".join(v.gray)
        print(f"  decider {v.decider_ms:.0f}ms  {label}  {detail}")
        text = str(result).strip()
        if v.action != "silent" and text and text != SILENT:
            print(f"  Kuuki {text}")
            if text.startswith("[room]"):
                room.add_ai(text.removeprefix("[room]").strip())

    counts = {k: sum(v.action == k for v in handler.log) for k in ("silent", "act", "escalate")}
    print(f"\n{len(handler.log)} utterances: {counts['silent']} silent, {counts['act']} decided by System 1, {counts['escalate']} handed to the LLM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
