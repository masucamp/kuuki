"""Meeting scenario -> Decider state text.

The state holds the meeting header (agenda, time left, known facts), the participants
with their role and who has not spoken yet, and the last N utterances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Participant:
    id: str
    name: str
    role: str
    overseas: bool = False
    senior: bool = False  # senior to most of the room, or an important client
    voice: str | None = None  # Amazon Polly voice for the demo audio
    name_en: str | None = None  # English display name and role for the demo UI
    role_en: str | None = None
    engine: str = "neural"


@dataclass
class Utterance:
    speaker: str  # participant id
    text: str
    minute: int | None = None  # elapsed minutes, for "time left"
    expect: dict | None = None  # expected decision, used only by replay checks and eval
    en: str | None = None  # English subtitle for the demo UI


AI = Participant(id="kuuki", name="Kuuki（AI）", role="AIの同席者")


@dataclass
class Meeting:
    title: str
    agenda: str
    duration_min: int
    facts: list[str]
    participants: dict[str, Participant]
    utterances: list[Utterance]
    window: int = 6
    # English versions for the demo UI (the state for the models stays Japanese)
    title_en: str | None = None
    agenda_en: str | None = None
    facts_en: list[str] | None = None

    @classmethod
    def load(cls, path: Path) -> Meeting:
        return cls.from_dict(yaml.safe_load(path.read_text()))

    @classmethod
    def from_dict(cls, d: dict) -> Meeting:
        participants = {p["id"]: Participant(**p) for p in d["participants"]}
        utterances = [Utterance(**u) for u in d["utterances"]]
        return cls(
            title=d["title"],
            agenda=d["agenda"],
            duration_min=d["duration_min"],
            facts=d.get("facts", []),
            participants=participants,
            utterances=utterances,
            window=d.get("window", 6),
            title_en=d.get("title_en"),
            agenda_en=d.get("agenda_en"),
            facts_en=d.get("facts_en"),
        )


@dataclass
class RoomState:
    meeting: Meeting
    history: list[Utterance] = field(default_factory=list)
    silence_after: int = 3  # utterances before "has not spoken yet" is shown

    def add(self, u: Utterance) -> None:
        self.history.append(u)

    def add_ai(self, text: str) -> None:
        """Record what the AI said in the room, so later judgments know it already acted."""
        self.history.append(Utterance(speaker=AI.id, text=text, minute=self.history[-1].minute))

    @property
    def has_facts(self) -> bool:
        return bool(self.meeting.facts)

    @property
    def latest(self) -> Utterance:
        """Latest human utterance."""
        return next(u for u in reversed(self.history) if u.speaker != AI.id)

    def speaker(self, u: Utterance) -> Participant:
        return AI if u.speaker == AI.id else self.meeting.participants[u.speaker]

    def recent_senior(self, n: int = 3) -> list[bool]:
        """Seniority of the speakers of the last n utterances, latest first."""
        human = [u for u in self.history if u.speaker != AI.id]
        return [self.speaker(u).senior for u in reversed(human[-n:])]

    def render(self, mark_senior: bool = False, include_ai: bool = False) -> str:
        """`mark_senior` writes the known seniority into the text, and `include_ai` keeps the
        AI's own lines. Both are for LLMs: the Decider probes were tuned on human-only states
        and misread AI lines (a reply to the AI looks like a reply to a request)."""
        m = self.meeting
        history = self.history if include_ai else [u for u in self.history if u.speaker != AI.id]
        spoken = {u.speaker for u in history}
        lines = [f"会議: {m.title}", f"議題: {m.agenda}"]
        minute = next((u.minute for u in reversed(history) if u.minute is not None), None)
        if minute is not None:
            lines.append(f"時間: {m.duration_min}分の会議、残り{m.duration_min - minute}分")
        if m.facts:
            lines.append("前提となる事実:")
            lines += [f"- {f}" for f in m.facts]
        # Silence only means something once the discussion is under way.
        mark_silent = len(history) >= self.silence_after
        lines.append("参加者:")
        for p in m.participants.values():
            note = "（まだ一度も発言していない）" if mark_silent and p.id not in spoken else ""
            senior = "（上位者）" if mark_senior and p.senior else ""
            lines.append(f"- {p.name}: {p.role}{senior}{note}")
        lines.append("これまでの発言:")
        for u in history[-m.window : -1]:
            lines.append(f"{self.speaker(u).name}: {u.text}")
        lines.append("最新の発言（判定の対象）:")
        lines.append(f"{self.speaker(history[-1]).name}: {history[-1].text}")
        return "\n".join(lines)
