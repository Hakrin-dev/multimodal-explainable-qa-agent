"""RAG answer faithfulness self-check.

The checker verifies a generated answer only against the retrieved chunks.
Unsupported claims are removed through bounded convergence rewriting.
Failures degrade gracefully and never break the original RAG answer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any

from ..core.llm import LLMService
from ..core.prompts import rag as prompts


@dataclass
class FactcheckOutcome:
    answer: str
    checked: bool = False
    faithful: bool = False
    attempts: int = 0
    rewritten: bool = False
    unsupported_sentences: list[str] = field(default_factory=list)
    degraded: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("answer", None)
        return data


def _extract_json(content: str) -> dict[str, Any] | None:
    """Parse a JSON object, tolerating surrounding prose/code fences."""
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        return None

    try:
        value = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None

    return value if isinstance(value, dict) else None


def check_answer(
    llm: LLMService,
    *,
    question: str,
    answer: str,
    chunks: list[dict],
    max_rounds: int = 2,
) -> tuple[FactcheckOutcome, list[Any]]:
    """Check and optionally rewrite an answer, with at most two LLM calls."""
    max_rounds = max(1, min(int(max_rounds), 2))
    current = answer.strip()
    unsupported: list[str] = []
    calls: list[Any] = []
    rewritten = False

    for attempt in range(1, max_rounds + 1):
        messages = prompts.build_factcheck_messages(
            question=question,
            answer=current,
            chunks=chunks,
        )

        try:
            response = llm.chat(
                messages,
                temperature=0.0,
                max_tokens=700,
                response_json=True,
                purpose="rag.factcheck",
            )
        except Exception as exc:  # noqa: BLE001 - must not break RAG
            return (
                FactcheckOutcome(
                    answer=current,
                    checked=True,
                    faithful=False,
                    attempts=attempt,
                    rewritten=rewritten,
                    unsupported_sentences=unsupported,
                    degraded=True,
                    reason=f"llm_error:{type(exc).__name__}",
                ),
                calls,
            )

        calls.append(response)
        data = _extract_json(response.content)

        if data is None or not isinstance(data.get("faithful"), bool):
            return (
                FactcheckOutcome(
                    answer=current,
                    checked=True,
                    faithful=False,
                    attempts=attempt,
                    rewritten=rewritten,
                    unsupported_sentences=unsupported,
                    degraded=True,
                    reason="invalid_json",
                ),
                calls,
            )

        current_unsupported = data.get("unsupported_sentences", [])
        if isinstance(current_unsupported, list):
            for item in current_unsupported:
                if isinstance(item, str):
                    item = item.strip()
                    if item and item not in unsupported:
                        unsupported.append(item)

        if data["faithful"]:
            return (
                FactcheckOutcome(
                    answer=current,
                    checked=True,
                    faithful=True,
                    attempts=attempt,
                    rewritten=rewritten,
                    unsupported_sentences=unsupported,
                ),
                calls,
            )

        revised = data.get("revised_answer")
        if not isinstance(revised, str) or not revised.strip():
            return (
                FactcheckOutcome(
                    answer=current,
                    checked=True,
                    faithful=False,
                    attempts=attempt,
                    rewritten=rewritten,
                    unsupported_sentences=unsupported,
                    degraded=True,
                    reason="missing_revision",
                ),
                calls,
            )

        revised = revised.strip()
        if revised == current:
            return (
                FactcheckOutcome(
                    answer=current,
                    checked=True,
                    faithful=False,
                    attempts=attempt,
                    rewritten=rewritten,
                    unsupported_sentences=unsupported,
                    degraded=True,
                    reason="unchanged_revision",
                ),
                calls,
            )

        current = revised
        rewritten = True

    return (
        FactcheckOutcome(
            answer=current,
            checked=True,
            faithful=False,
            attempts=max_rounds,
            rewritten=rewritten,
            unsupported_sentences=unsupported,
            degraded=True,
            reason="max_rounds_exhausted",
        ),
        calls,
    )
