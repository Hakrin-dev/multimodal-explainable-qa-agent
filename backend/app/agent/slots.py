"""Slot matrix (PLAN §4.7 智能语义校验与澄清 · 外置配置，非硬编码).

data/slot_matrix.json defines, per question pattern:
  - required slots (each with a `satisfied_by` regex — evidence the question
    already carries that element)
  - candidate options for clarification buttons

Role in the kernel (deterministic reinforcement of the LLM intent):
  1. LLM says DB_QUERY but the matrix finds unsatisfied required slots
     → override to AMBIGUOUS with matrix options (catches LLM misses)
  2. LLM says AMBIGUOUS → enrich options from the matrix (button quality)

Extending to new domains = edit the JSON, zero code (知识外置).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import resolve_repo_path

DEFAULT_PATH = "data/slot_matrix.json"


@dataclass
class SlotRule:
    pattern: re.Pattern
    intent_hint: str                     # e.g. 增长率/对比/趋势
    required: list[dict] = field(default_factory=list)  # [{name, satisfied_by}]
    options: dict[str, list[str]] = field(default_factory=dict)

    def missing_slots(self, question: str) -> list[dict]:
        return [s for s in self.required
                if not re.search(s["satisfied_by"], question)]


@dataclass
class MatrixVerdict:
    triggered: bool = False
    intent_hint: str = ""
    missing: list[str] = field(default_factory=list)
    options: dict[str, list[str]] = field(default_factory=dict)


class SlotMatrix:
    def __init__(self, rules: list[SlotRule]):
        self.rules = rules

    @classmethod
    def load(cls, path: str | None = None) -> "SlotMatrix":
        p = Path(path) if path else resolve_repo_path(DEFAULT_PATH)
        if not p.exists():
            return cls([])
        data = json.loads(p.read_text(encoding="utf-8"))
        rules = []
        for r in data.get("rules", []):
            rules.append(SlotRule(
                pattern=re.compile(r["pattern"]),
                intent_hint=r.get("hint", ""),
                required=r.get("required_slots", []),
                options=r.get("options", {}),
            ))
        return cls(rules)

    def check(self, question: str) -> MatrixVerdict:
        """First matching rule wins; returns missing (unsatisfied) slots."""
        for rule in self.rules:
            if rule.pattern.search(question):
                missing = rule.missing_slots(question)
                options = {s["name"]: rule.options.get(s["name"], [])
                           for s in missing if rule.options.get(s["name"])}
                return MatrixVerdict(
                    triggered=True, intent_hint=rule.intent_hint,
                    missing=[s["name"] for s in missing], options=options)
        return MatrixVerdict()
