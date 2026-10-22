"""Turn Decider probe probabilities into an interjection decision with a threshold table."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .questions import PROBES

DEFAULT_THRESHOLDS = Path(__file__).with_name("thresholds.yaml")


@dataclass
class Decision:
    move: str  # one of the moves in thresholds.yaml, or "stay_silent"
    delivery: str | None  # direct | cushioned | private_dm_later
    tatemae: bool  # polite "no" detected -> air translation + follow-up
    probs: dict[str, float]
    reasons: list[str] = field(default_factory=list)

    @property
    def speaks_now(self) -> bool:
        return self.move != "stay_silent" and self.delivery != "private_dm_later"


class InterjectionPolicy:
    """Stateful across one meeting: `consecutive` rules count streaks. Call reset() per meeting."""

    def __init__(self, thresholds_path: Path = DEFAULT_THRESHOLDS) -> None:
        self.cfg = yaml.safe_load(thresholds_path.read_text())
        self._streak: dict[str, int] = defaultdict(int)

    @property
    def questions(self) -> dict[str, dict]:
        return PROBES

    def reset(self) -> None:
        self._streak.clear()

    def decide(self, probs: dict[str, float], recent_senior: list[bool] | None = None) -> Decision:
        """`recent_senior`: whether the speakers of recent utterances are senior, latest first."""
        reasons: list[str] = []
        recent_senior = recent_senior or []

        move, confidence = "stay_silent", 0.0
        for rule in self.cfg["moves"]:
            p = probs[rule["probe"]]
            met = (
                p >= rule["threshold"]
                and all(probs[k] >= v for k, v in rule.get("requires", {}).items())
                and not any(probs[k] >= v for k, v in rule.get("unless", {}).items())
            )
            self._streak[rule["move"]] = self._streak[rule["move"]] + 1 if met else 0
            if move != "stay_silent" or not met:
                continue  # keep updating streaks of lower-priority rules
            if self._streak[rule["move"]] < rule.get("consecutive", 1):
                reasons.append(f"{rule['probe']}={p:.2f} (streak {self._streak[rule['move']]}/{rule['consecutive']})")
                continue
            move, confidence = rule["move"], p
            reqs = "".join(f", {k}={probs[k]:.2f}" for k in rule.get("requires", {}))
            reasons.append(f"{rule['probe']}={p:.2f}{reqs} -> {move}")
            self._streak[rule["move"]] = 0  # acted on it; start counting again

        delivery = None
        if move != "stay_silent":
            delivery = self.cfg["delivery"]["default"][move]
            senior = self.cfg["delivery"]["senior"]
            lookback = senior["lookback"].get(move)
            target_senior = lookback is not None and any(recent_senior[: lookback + 1])
            if target_senior and confidence < senior["min_confidence_for_public"]:
                delivery = "private_dm_later"
                reasons.append(
                    f"senior speaker, confidence {confidence:.2f} < {senior['min_confidence_for_public']} -> private_dm_later"
                )

        t = self.cfg["tatemae"]
        tatemae = probs[t["gate_probe"]] >= t["gate_threshold"] and probs[t["probe"]] >= t["threshold"]
        if tatemae:
            reasons.append(f"{t['gate_probe']}={probs[t['gate_probe']]:.2f}, {t['probe']}={probs[t['probe']]:.2f} -> tatemae")

        return Decision(move=move, delivery=delivery, tatemae=tatemae, probs=probs, reasons=reasons)

    def should_escalate(self, d: Decision, has_facts: bool = True) -> list[str]:
        """Gray-zone probes worth a second opinion. A confident action by the thresholds is kept;
        only a "do nothing" decision that might be a miss is handed to the LLM."""
        if d.move != "stay_silent" or d.tatemae:
            return []
        return self.gray_zone(d.probs, has_facts)

    def gray_zone(self, probs: dict[str, float], has_facts: bool = True) -> list[str]:
        """Probes whose value falls in their escalation range: the threshold is not trustworthy here."""
        hits = []
        for probe, rule in self.cfg.get("escalate", {}).items():
            lo, hi = rule["range"]
            if not lo <= probs[probe] < hi:
                continue
            if rule.get("requires_facts") and not has_facts:
                continue
            if not all(probs[k] >= v for k, v in rule.get("requires", {}).items()):
                continue
            hits.append(probe)
        return hits
