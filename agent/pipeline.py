"""Replay a meeting script in the terminal: Decider batch per utterance -> policy -> Bedrock only when acting.

When the thresholds say "stay silent" but a probe lands in its gray zone (thresholds.yaml
`escalate`), the decision is handed to an LLM judge instead (System 1 -> System 2).
The web UI (app/) runs the same engine.

    uv run --env-file .env python -m agent.pipeline scenarios/demo_meeting_ja.yaml \\
        [--writer sonnet|haiku|none] [--judge sonnet|haiku|none]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from agent.engine import MeetingEngine
from agent.llm_judge import LLMJudge
from agent.room_state import Meeting
from agent.writer import Writer
from patterns.interjection_policy.decider import DeciderClient
from patterns.interjection_policy.policy import InterjectionPolicy

DELIVERY_LABEL = {"direct": "in the room", "cushioned": "in the room, softly", "private_dm_later": "private note after the meeting (nemawashi)"}


def show(ev: dict, names: dict[str, str]) -> None:
    t = ev["type"]
    if t == "utterance":
        print(f"\n[{ev['minute']:>2} min] {names[ev['speaker']]}: {ev['text']}" + (f"\n          {ev['en']}" if ev.get("en") else ""))
    elif t == "probes":
        top = sorted(ev["probs"].items(), key=lambda kv: -kv[1])[:3]
        print(f"  decider {ev['ms']:.0f}ms  " + "  ".join(f"{k}={p:.2f}" for k, p in top))
    elif t == "judge":
        print(f"  unsure -> {ev['model']} decided in {ev['ms']:.0f}ms")
    elif t == "decision":
        if ev["move"] == "stay_silent" and not ev["tatemae"]:
            print("  -> stay silent")
        elif ev["move"] != "stay_silent":
            print(f"  -> {ev['move']} / {DELIVERY_LABEL[ev['delivery']]}   ({'; '.join(ev['reasons'])})")
        if ev["tatemae"]:
            print(f"  -> likely a polite no ({'; '.join(ev['reasons'])})")
    elif t == "kuuki":
        label = {"room": "Kuuki", "dm": f"Kuuki (private note to {ev.get('to')})", "air": "Air translation",
                 "followup": "Follow-up"}[ev["kind"]]
        ms = f"   [{ev['ms']:.0f}ms]" if ev.get("ms") else ""
        print(f"  {label}: {ev['text']}{ms}")
    elif t == "done":
        if ev["followups"]:
            print("\n===== After the meeting =====")
            for f in ev["followups"]:
                print(f"- {f['to']} ({f['kind']}): {f['text']}")
        r = ev["routes"]
        print(f"\n===== Summary =====\n{ev['decisions']} utterances: {r['silent']} silent, {r['act']} acted by System 1,"
              f" {r['escalate']} handed to the LLM / strands-decider median {ev['decider_median_ms']:.0f}ms"
              f" / {ev['llm_calls']} LLM calls (~${ev['cost_usd']:.4f})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--writer", choices=["sonnet", "haiku", "none"], default="sonnet")
    ap.add_argument("--judge", choices=["sonnet", "haiku", "none"], default="sonnet",
                    help="LLM that decides when a probe is in its gray zone (none = Decider only)")
    ap.add_argument("--decider-url", default="http://127.0.0.1:8000")
    ap.add_argument("--log", type=Path, default=Path("logs/replay.jsonl"))
    args = ap.parse_args()

    meeting = Meeting.load(args.scenario)
    engine = MeetingEngine(
        meeting,
        DeciderClient(args.decider_url),
        InterjectionPolicy(),
        None if args.writer == "none" else Writer(args.writer),
        None if args.judge == "none" else LLMJudge(args.judge),
    )
    names = {p.id: p.name_en or p.name for p in meeting.participants.values()}
    args.log.parent.mkdir(exist_ok=True)
    try:
        with args.log.open("w") as log:
            for u in meeting.utterances:
                for ev in engine.step(u):
                    show(ev, names)
                    log.write(json.dumps(ev, ensure_ascii=False) + "\n")
            for ev in engine.finish():
                show(ev, names)
                log.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except (ClientError, BotoCoreError) as e:
        print(f"Bedrock error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
