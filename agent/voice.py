"""Amazon Polly speech for the demo: meeting voices, Kuuki's voice and the video narration."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import boto3
import imageio_ffmpeg

KUUKI_VOICE = ("Takumi", "standard")  # a slightly synthetic voice, so the room can tell the AI apart
NARRATOR_VOICE = ("Ruth", "generative")


def mp3_duration_ms(path: Path) -> int:
    """Duration of an audio file, read from ffmpeg's header probe."""
    probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                           capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
    if not m:
        raise ValueError(f"no duration in ffmpeg output for {path}")
    h, mi, s = m.groups()
    return round((int(h) * 3600 + int(mi) * 60 + float(s)) * 1000)


class Speaker:
    def __init__(self, profile: str | None = None, region: str = "ap-northeast-1") -> None:
        self.polly = boto3.Session(profile_name=profile, region_name=region).client("polly")

    def to_file(self, text: str, voice: str, engine: str, path: Path) -> int:
        """Synthesize `text` to `path` (mp3). Returns the duration in ms."""
        res = self.polly.synthesize_speech(Text=text, VoiceId=voice, Engine=engine, OutputFormat="mp3")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(res["AudioStream"].read())
        return mp3_duration_ms(path)

    def cached(self, text: str, voice: str, engine: str, folder: Path) -> tuple[Path, int]:
        """Synthesize once per (text, voice, engine); later calls reuse the file."""
        key = hashlib.sha1(f"{voice}|{engine}|{text}".encode()).hexdigest()[:16]
        path = folder / f"{key}.mp3"
        if path.exists():
            return path, mp3_duration_ms(path)
        return path, self.to_file(text, voice, engine, path)
