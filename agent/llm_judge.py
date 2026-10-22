"""Baseline: let an LLM on Bedrock make the whole interjection decision in one call.

Same rules as the Decider policy (thresholds.yaml), written as instructions, so the
comparison is about who judges, not about different rules.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import boto3
from botocore.config import Config

from agent.writer import MODELS, THINKING_OFF
from patterns.interjection_policy.prompts import MOVE_RULES

SYSTEM = f"""You are the judgment module of an AI teammate in a Japanese business meeting.
Given the meeting state, decide what the AI should do right after the latest utterance.

{MOVE_RULES}

Answer with only one JSON object, no other text:
{{"move": "...", "delivery": "..." or null, "tatemae": true/false, "reason": "short"}}"""


@dataclass
class LLMJudgement:
    move: str
    delivery: str | None
    tatemae: bool
    reason: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    raw: str


class LLMJudge:
    def __init__(self, model: str = "haiku", profile: str | None = None, region: str = "ap-northeast-1") -> None:
        session = boto3.Session(profile_name=profile, region_name=region)
        self.client = session.client(
            "bedrock-runtime", config=Config(retries={"total_max_attempts": 3, "mode": "adaptive"}, read_timeout=30)
        )
        self.model = model

    def judge(self, state: str) -> LLMJudgement:
        t0 = time.perf_counter()
        res = self.client.converse(
            modelId=MODELS[self.model],
            system=[{"text": SYSTEM}],
            messages=[{"role": "user", "content": [{"text": f"<meeting>\n{state}\n</meeting>"}]}],
            inferenceConfig={"maxTokens": 200},
            additionalModelRequestFields=THINKING_OFF[self.model],
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        raw = "".join(c.get("text", "") for c in res["output"]["message"]["content"]).strip()
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else {}
        move = d.get("move", "stay_silent")
        return LLMJudgement(
            move=move,
            delivery=d.get("delivery") if move != "stay_silent" else None,
            tatemae=bool(d.get("tatemae", False)),
            reason=d.get("reason", "" if m else f"unparsable: {raw[:80]}"),
            latency_ms=latency_ms,
            input_tokens=res["usage"]["inputTokens"],
            output_tokens=res["usage"]["outputTokens"],
            raw=raw,
        )
