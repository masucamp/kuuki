"""Minimal client for a local `strands-decider serve` (POST /v1/systemone)."""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass


@dataclass
class DeciderResult:
    probs: dict[str, float]  # noul probability per question id
    answers: dict  # raw answers, kept for logging (confidence vs top probability)
    server_ms: float
    wall_ms: float


class DeciderClient:
    def __init__(self, url: str = "http://127.0.0.1:8000") -> None:
        self.url = url.rstrip("/")
        # Local server: never route through the corporate proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def ask(self, state: str, questions: dict[str, dict]) -> DeciderResult:
        body = json.dumps({"state": state, "questions": questions}, ensure_ascii=False).encode()
        req = urllib.request.Request(
            f"{self.url}/v1/systemone", data=body, headers={"Content-Type": "application/json"}
        )
        t0 = time.perf_counter()
        with self._opener.open(req) as res:
            payload = json.load(res)
        wall_ms = (time.perf_counter() - t0) * 1000
        answers = payload["answers"]
        probs = {k: a["noul"] for k, a in answers.items() if a["type"] == "noul"}
        return DeciderResult(probs=probs, answers=answers, server_ms=payload["latency_ms"], wall_ms=wall_ms)
