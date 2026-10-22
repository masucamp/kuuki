"""Kuuki demo UI: replays a meeting script and streams every decision over a WebSocket.

    strands-decider serve <checkpoint> --port 8000          # System 1
    uv run --env-file .env uvicorn app.server:app --port 8765
    open http://127.0.0.1:8765

Protocol (client -> server):
    {"cmd": "start", "scenario": "demo_meeting_ja.yaml", "mode": "hybrid" | "decider", "auto": true, "gap": 2.5}
    {"cmd": "next"}                      # play the next utterance (step mode)
    {"cmd": "auto", "on": true | false}  # toggle autoplay
Server -> client: the engine events (agent/engine.py), plus {"type": "error", "message"}.

Voices: when app/static/audio/<scenario>/manifest.json exists (app/make_audio.py), each
utterance event carries its audio and the server waits while it plays before reading the
room, as a live system would. Kuuki's in-room lines are spoken with Amazon Polly on the fly
({"type": "audio", "who": "kuuki", "src", "ms"}), cached under app/static/audio/live/.

Laptop GPUs drop their clocks after about a second idle: on the demo meeting strands-decider
took ~345 ms back to back but ~600 ms after a 2.6 s pause between autoplayed utterances. A tiny
one-question ping was not enough load (~450 ms), so while waiting for the next utterance the
server re-sends the current meeting state every 0.5 s (~350 ms), matching the back-to-back
conditions of the evaluation. Set KUUKI_KEEP_WARM=0 to turn it off.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent.engine import MeetingEngine
from agent.llm_judge import LLMJudge
from agent.room_state import Meeting
from agent.voice import KUUKI_VOICE, Speaker
from agent.writer import Writer
from patterns.interjection_policy.decider import DeciderClient
from patterns.interjection_policy.policy import InterjectionPolicy

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).with_name("static")
SCENARIOS = ROOT / "scenarios"
AUDIO = STATIC / "audio"

app = FastAPI(title="Kuuki")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_clients: dict[str, object] = {}
KEEP_WARM = os.environ.get("KUUKI_KEEP_WARM", "1") != "0"


def _keep_warm(decider: DeciderClient, engine: MeetingEngine) -> None:
    try:
        decider.ask(engine.room.render(), engine.policy.questions)
    except OSError:
        pass  # the real request will report the error


def _writer() -> Writer:
    if "writer" not in _clients:
        _clients["writer"] = Writer("sonnet")
    return _clients["writer"]


def _speaker() -> Speaker:
    if "speaker" not in _clients:
        _clients["speaker"] = Speaker()
    return _clients["speaker"]


def _judge() -> LLMJudge:
    if "judge" not in _clients:
        _clients["judge"] = LLMJudge("sonnet")
    return _clients["judge"]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/scenarios")
def scenarios() -> list[str]:
    return sorted(p.name for p in SCENARIOS.glob("demo_*.yaml"))


class Session:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.auto = True
        self.gap = 2.5
        self.next_requested = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.decider = DeciderClient()
        self.engine: MeetingEngine | None = None
        self._last_warm = 0.0

    async def send(self, ev: dict) -> None:
        await self.ws.send_text(json.dumps(ev, ensure_ascii=False))

    async def _tick(self, start: float) -> float:
        """Sleep a little, ping strands-decider about every 0.5 s; return seconds since `start`."""
        await asyncio.sleep(0.1)
        if KEEP_WARM and self.engine and time.monotonic() - self._last_warm >= 0.5:
            self._last_warm = time.monotonic()
            await asyncio.to_thread(_keep_warm, self.decider, self.engine)
        return time.monotonic() - start

    async def hold(self, seconds: float) -> None:
        """Wait while audio plays, keeping strands-decider warm."""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            await self._tick(start)

    async def wait_turn(self) -> None:
        """Autoplay waits `gap` seconds (a `next` skips ahead); paused waits for `next`.
        Polls, so switching autoplay on or off mid-wait takes effect at once."""
        self.next_requested.clear()
        start, waited = time.monotonic(), 0.0
        while not self.next_requested.is_set():
            if self.auto and waited >= self.gap:
                return
            waited = await self._tick(start)

    async def speak_kuuki(self, ev: dict) -> None:
        try:
            path, ms = await asyncio.to_thread(_speaker().cached, ev["text"], *KUUKI_VOICE, AUDIO / "live")
        except (ClientError, BotoCoreError):
            return  # Kuuki's line stays on screen without a voice
        await self.send({"type": "audio", "i": ev["i"], "who": "kuuki", "src": f"/static/audio/live/{path.name}", "ms": ms})
        await self.hold(ms / 1000)

    async def run(self, scenario: str, mode: str) -> None:
        path = SCENARIOS / Path(scenario).name
        try:
            meeting = Meeting.load(path)
            engine = MeetingEngine(
                meeting, self.decider, InterjectionPolicy(), _writer(),
                _judge() if mode == "hybrid" else None, english=True,
            )
            self.engine = engine
            manifest_path = AUDIO / path.stem / "manifest.json"
            audio = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
            if audio:
                self.gap = min(self.gap, 0.8)  # the voices set the pace
            await self.send({**engine.meeting_event(), "voices": bool(audio)})
            for n, u in enumerate(meeting.utterances):
                if n:
                    await self.wait_turn()
                steps = engine.step(u)
                while (ev := await asyncio.to_thread(next, steps, None)) is not None:
                    clip = audio.get(str(n)) if ev["type"] == "utterance" else None
                    if clip:
                        ev["audio"] = {"src": clip["src"], "ms": clip["ms"]}
                    await self.send(ev)
                    if clip:
                        await self.hold(clip["ms"] / 1000)  # let the speaker finish before reading the room
                    if audio and ev["type"] == "kuuki" and ev["kind"] == "room":
                        await self.speak_kuuki(ev)
            for ev in engine.finish():
                await self.send(ev)
        except (ClientError, BotoCoreError) as e:
            await self.send({"type": "error", "message": f"Bedrock: {e}"})
        except OSError as e:  # strands-decider not reachable, scenario missing
            await self.send({"type": "error", "message": f"{type(e).__name__}: {e}"})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    s = Session(ws)
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            cmd = msg.get("cmd")
            if cmd == "start":
                if s.task:
                    s.task.cancel()
                s.auto = bool(msg.get("auto", True))
                s.gap = float(msg.get("gap", 2.5))
                s.task = asyncio.create_task(s.run(msg.get("scenario", "demo_meeting_ja.yaml"), msg.get("mode", "hybrid")))
            elif cmd == "next":
                s.next_requested.set()
            elif cmd == "auto":
                s.auto = bool(msg.get("on"))
    except WebSocketDisconnect:
        if s.task:
            s.task.cancel()
