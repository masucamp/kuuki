"""Pre-generate the meeting voices of a scenario with Amazon Polly.

    uv run --env-file .env python -m app.make_audio scenarios/demo_meeting_ja.yaml

Writes app/static/audio/<scenario>/NN.mp3 and manifest.json ({index: {src, ms, key}}).
Each participant needs a `voice` (and optional `engine`) in the scenario. Lines whose text
and voice did not change are skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from agent.room_state import Meeting
from agent.voice import Speaker, mp3_duration_ms

STATIC = Path(__file__).with_name("static")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()

    meeting = Meeting.load(args.scenario)
    folder = STATIC / "audio" / args.scenario.stem
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    speaker = Speaker(args.profile)

    made = chars = 0
    for i, u in enumerate(meeting.utterances):
        p = meeting.participants[u.speaker]
        if not p.voice:
            print(f"{i:02d} {p.name}: no voice set, skipped")
            continue
        key = hashlib.sha1(f"{p.voice}|{p.engine}|{u.text}".encode()).hexdigest()[:16]
        path = folder / f"{i:02d}.mp3"
        old = manifest.get(str(i))
        if old and old.get("key") == key and path.exists():
            ms = mp3_duration_ms(path)
        else:
            ms = speaker.to_file(u.text, p.voice, p.engine, path)
            made += 1
            chars += len(u.text)
        manifest[str(i)] = {"src": f"/static/audio/{args.scenario.stem}/{path.name}", "ms": ms, "key": key}
        print(f"{i:02d} {p.name} ({p.voice}) {ms / 1000:.1f}s")

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"generated {made} files ({chars} characters)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
