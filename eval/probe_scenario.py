"""Tuning aid: probe probabilities for every utterance of a scenario, and policy vs expect.

    uv run python eval/probe_scenario.py scenarios/demo_meeting_ja.yaml [--state N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patterns.interjection_policy.decider import DeciderClient  # noqa: E402
from agent.room_state import Meeting, RoomState  # noqa: E402
from patterns.interjection_policy.policy import InterjectionPolicy  # noqa: E402

SHORT = {
    "has_error": "err",
    "is_vague": "vague",
    "off_agenda": "off",
    "needs_silent_expert": "silent",
    "is_response_to_request": "reply",
    "polite_refusal": "refuse",
    "open_question": "openq",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--state", type=int, help="print the rendered state for utterance index N")
    args = ap.parse_args()

    meeting = Meeting.load(args.scenario)
    policy = InterjectionPolicy()
    decider = DeciderClient()
    room = RoomState(meeting)

    print(f"{'#':>2} {'speaker':6s} " + " ".join(f"{s:>6s}" for s in SHORT.values()) + "  decision            expect")
    hit = total = 0
    for i, u in enumerate(meeting.utterances):
        room.add(u)
        state = room.render()
        if args.state == i:
            print(state)
        r = decider.ask(state, policy.questions)
        d = policy.decide(r.probs, recent_senior=room.recent_senior())
        got = d.move + (f"/{d.delivery}" if d.delivery else "") + (" +tatemae" if d.tatemae else "")
        mark = ""
        if u.expect:
            ok = all(
                (d.tatemae == v) if k == "tatemae" else (getattr(d, k) in (v if isinstance(v, list) else [v]))
                for k, v in u.expect.items()
            )
            hit += ok
            total += 1
            mark = ("OK " if ok else "NG ") + ",".join(f"{k}={v}" for k, v in u.expect.items())
        cells = " ".join(f"{r.probs[k]:6.2f}" for k in SHORT)
        print(f"{i:2d} {u.speaker:6s} {cells}  {got:19s} {mark}")
    print(f"-> {hit}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
