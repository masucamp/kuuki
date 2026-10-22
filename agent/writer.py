"""Generate the actual words with Bedrock Converse, only after the policy decided to act."""

from __future__ import annotations

import time
from dataclasses import dataclass

import boto3
from botocore.config import Config

MODELS = {
    "sonnet": "global.anthropic.claude-sonnet-5-5",
    "haiku": "jp.anthropic.claude-haiku-5-5",
}

# Thinking off: one short line does not need it, and on Haiku 5.5 a small maxTokens
# can be used up by thinking, leaving no text.
THINKING_OFF = {
    "sonnet": {"thinking": {"type": "between_tools"}},
    "haiku": {"thinking": {"type": "disabled"}},
}

SYSTEM = """You are Kuuki, an AI teammate sitting in a Japanese business meeting.
You read the room: a separate policy has already decided WHETHER you act and WHAT kind of move to make.
Your only job is to write the words for that move. Be brief, natural, and respectful of hierarchy.
Never mock anyone. Output only the words to say or send, with no preamble or quotes."""

DM_RULES = "At most 2 sentences and about 100 Japanese characters. Only about this one point. No greeting line and no signature."

ROOM_RULES = "Keep it to about 40 Japanese characters: it is said out loud in the meeting."

TASKS = {
    ("correct", "direct"): "Point out the factual error in the latest utterance in one short Japanese sentence, citing the stated fact. " + ROOM_RULES,
    ("correct", "cushioned"): "Gently point out the factual error in one short, polite Japanese sentence (e.g. start with 「念のためですが」), citing the stated fact. " + ROOM_RULES,
    ("correct", "private_dm_later"): "Write a very polite private Japanese chat message to the speaker, to send after the meeting, noting the discrepancy between the latest utterance and the stated fact. Do not blame. " + DM_RULES,
    ("clarify", "direct"): "Ask in one short Japanese sentence what the vague reference concretely means, on behalf of the listener who seems unsure. " + ROOM_RULES,
    ("clarify", "private_dm_later"): "Write a polite private Japanese chat message to the speaker, to send after the meeting, asking what the vague reference in the latest utterance concretely meant, so the team can follow. " + DM_RULES,
    ("invite_quiet", "cushioned"): "In one short polite Japanese sentence, invite the silent participant with relevant expertise to share their view. Name them. " + ROOM_RULES,
    ("refocus", "cushioned"): "In one short polite Japanese sentence, gently bring the discussion back to the agenda, mentioning the time left and what still needs to be decided. " + ROOM_RULES,
}

EN_SUBTITLE = "Then, on a new line starting with 'EN:', give a natural English translation for viewers."

TATEMAE_TASK = """The latest utterance is likely a polite Japanese "no" (tatemae).
Write exactly three lines, each starting with its label:
AIR: for overseas members, one plain English sentence explaining what the speaker most likely means, framed as cultural context, not as an accusation.
FOLLOWUP_JA: one short Japanese line describing what to confirm with the speaker after the meeting.
FOLLOWUP_EN: the same follow-up in English."""


@dataclass
class WriterResult:
    text: str
    model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    en: str | None = None  # English subtitle, when asked for


class Writer:
    def __init__(self, model: str = "sonnet", profile: str | None = None, region: str = "ap-northeast-1") -> None:
        session = boto3.Session(profile_name=profile, region_name=region)
        self.client = session.client(
            "bedrock-runtime", config=Config(retries={"total_max_attempts": 3, "mode": "adaptive"}, read_timeout=30)
        )
        self.model = model

    def write(self, state: str, move: str, delivery: str, english: bool = False) -> WriterResult:
        if not english:
            return self._call(state, TASKS[(move, delivery)])
        task = TASKS[(move, delivery)] + " " + EN_SUBTITLE
        res = self._call(state, task, max_tokens=300)
        ja, _, en = res.text.partition("EN:")
        res.text, res.en = ja.strip(), en.strip() or None
        return res

    def air_translate(self, state: str) -> WriterResult:
        return self._call(state, TATEMAE_TASK, max_tokens=300)

    def _call(self, state: str, task: str, max_tokens: int = 200) -> WriterResult:
        t0 = time.perf_counter()
        res = self.client.converse(
            modelId=MODELS[self.model],
            system=[{"text": SYSTEM}],
            messages=[{"role": "user", "content": [{"text": f"<meeting>\n{state}\n</meeting>\n\nTask: {task}"}]}],
            inferenceConfig={"maxTokens": max_tokens},
            additionalModelRequestFields=THINKING_OFF[self.model],
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        text = "".join(c.get("text", "") for c in res["output"]["message"]["content"]).strip()
        return WriterResult(
            text=text,
            model=MODELS[self.model],
            latency_ms=latency_ms,
            input_tokens=res["usage"]["inputTokens"],
            output_tokens=res["usage"]["outputTokens"],
        )
