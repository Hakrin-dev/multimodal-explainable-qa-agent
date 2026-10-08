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
    required: list[dict] = field(default_factory=list)  # [{name, satisfied_by, synonyms}]
    options: dict[str, list[str]] = field(default_factory=dict)

    def missing_slots(self, question: str) -> list[dict]:
        return [s for s in self.required
                if not re.search(s["satisfied_by"], question)]

    def canonicalize(self, label: str) -> str | None:
        """Map a free-form LLM slot label onto this rule's canonical slot name.

        Exact synonym match first, then substring containment either way.
        Returns None when the label belongs to no known slot (kept as-is).
        """
        label = label.strip()
        if not label:
            return None
        for s in self.required:
            if label == s["name"] or label in s.get("synonyms", []):
                return s["name"]
        for s in self.required:
            names = [s["name"], *s.get("synonyms", [])]
            for n in names:
                if label in n or n in label:
                    return s["name"]
        return None


@dataclass
class MatrixVerdict:
    triggered: bool = False
    intent_hint: str = ""
    missing: list[str] = field(default_factory=list)
    options: dict[str, list[str]] = field(default_factory=dict)
    rule: "SlotRule | None" = None       # matched rule (slot vocabulary owner)


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

    def synonyms_of(self, name: str) -> set[str]:
        """All labels known to denote the canonical slot `name` (across rules).

        Vocabulary authority for consumers (e.g. eval runners matching
        语义等价 rather than literal strings)."""
        out: set[str] = {name}
        for rule in self.rules:
            for s in rule.required:
                if s["name"] == name:
                    out.update(s.get("synonyms", []))
        return out

    def equivalents(self) -> list[set[str]]:
        """Synonym families across all rules (for semantic matching)."""
        families: dict[str, set[str]] = {}
        for rule in self.rules:
            for s in rule.required:
                fam = families.setdefault(s["name"], {s["name"]})
                fam.update(s.get("synonyms", []))
        # merge families that overlap (e.g. 时间范围 ⊂ 对比时间范围)
        merged: list[set[str]] = []
        for fam in families.values():
            for m in merged:
                hard = {x for x in m if len(x) >= 2}
                if hard & fam or any(a in b or b in a for a in m for b in fam):
                    m |= fam
                    break
            else:
                merged.append(set(fam))
        return merged

    def check(self, question: str) -> MatrixVerdict:
        """First matching rule wins; returns missing (unsatisfied) slots."""
        for rule in self.rules:
            if rule.pattern.search(question):
                missing = rule.missing_slots(question)
                options = {s["name"]: rule.options.get(s["name"], [])
                           for s in missing if rule.options.get(s["name"])}
                return MatrixVerdict(
                    triggered=True, intent_hint=rule.intent_hint,
                    missing=[s["name"] for s in missing], options=options,
                    rule=rule)
        return MatrixVerdict()
