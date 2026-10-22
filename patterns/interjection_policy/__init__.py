"""Interjection policy: decide whether, how and with what move an AI teammate speaks up.

strands-decider answers typed probes about the conversation, a threshold table turns the
probabilities into a decision, and gray-zone cases escalate to an LLM (System 1 -> System 2).
"""

from .decider import DeciderClient, DeciderResult
from .handler import SILENT, InterjectionHandler, RoomView, Verdict
from .policy import Decision, InterjectionPolicy
from .prompts import MOVE_RULES
from .questions import PROBES

__all__ = [
    "MOVE_RULES",
    "PROBES",
    "SILENT",
    "DeciderClient",
    "DeciderResult",
    "Decision",
    "InterjectionHandler",
    "InterjectionPolicy",
    "RoomView",
    "Verdict",
]
