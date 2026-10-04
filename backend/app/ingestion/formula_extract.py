"""LLM-assisted FormulaIR extraction from frozen DocIR blocks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..core.prompts.ingestion import build_formula_extract_messages
from ..formula.engine import FormulaEngine
from .ir import BlockIR, DocIR, FormulaIR

_CANDIDATE_RE = re.compile(
    r"(?:[=＝%％]|公式|计算|提成|折扣|比例|税率|费率|"
    r"总额|金额|单价|×|\*|÷)"
)
_PARAM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ALLOWED_SOURCES = {"db", "doc", "user"}


@dataclass
class FormulaExtractionResult:
    attempted: bool = True
    completed: bool = False
    candidates: int = 0
    extracted: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    @property
    def replace_ready(self) -> bool:
        return (
            self.completed
            and not self.rejected
            and not self.error
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "completed": self.completed,
            "replace_ready": self.replace_ready,
            "candidates": self.candidates,
            "extracted": self.extracted,
            "rejected": self.rejected,
            "error": self.error or None,
        }


def _extract_json(content: str) -> dict | None:
    match = re.search(r"\{.*\}", content or "", flags=re.DOTALL)
    if not match:
        return None

    try:
        value = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None

    return value if isinstance(value, dict) else None


def _block_breadcrumbs(doc: DocIR) -> dict[str, list[str]]:
    current: dict[int, str] = {}
    result: dict[str, list[str]] = {}

    for block in doc.blocks:
        if block.type == "heading":
            level = min(max(int(block.level or 1), 1), 4)
            for existing in list(current):
                if existing >= level:
                    current.pop(existing)
            current[level] = block.text.strip()

        breadcrumb = [doc.name]
        breadcrumb.extend(
            current[level]
            for level in sorted(current)
            if current[level]
        )
        result[block.id] = breadcrumb

    return result


def _candidate_blocks(
    doc: DocIR,
    *,
    max_blocks: int,
) -> list[BlockIR]:
    selected = []

    for block in doc.blocks:
        text = block.text.strip()
        if not text:
            continue

        # Headings provide breadcrumb context but are not formula evidence.
        if block.type == "heading":
            continue

        if block.type == "formula" or _CANDIDATE_RE.search(text):
            selected.append(block)

        if len(selected) >= max_blocks:
            break

    return selected


def _validate_params(value: object) -> dict[str, dict]:
    if not isinstance(value, dict):
        raise ValueError("params must be an object")

    result: dict[str, dict] = {}

    for raw_name, raw_meta in value.items():
        name = str(raw_name).strip()
        if not _PARAM_RE.fullmatch(name):
            raise ValueError(
                f"invalid parameter name: {name!r}"
            )
        if not isinstance(raw_meta, dict):
            raise ValueError(
                f"parameter {name} metadata must be an object"
            )

        desc = str(raw_meta.get("desc") or "").strip()
        unit = str(raw_meta.get("unit") or "").strip()
        source = str(raw_meta.get("source") or "").strip().lower()

        if not desc:
            raise ValueError(
                f"parameter {name} has no description"
            )
        if source not in _ALLOWED_SOURCES:
            raise ValueError(
                f"parameter {name} has invalid source {source!r}"
            )

        result[name] = {
            "desc": desc[:200],
            "unit": unit[:50],
            "source": source,
        }

    return result


def extract_formulas(
    doc: DocIR,
    *,
    llm,
    max_blocks: int = 40,
    max_formulas: int = 20,
) -> FormulaExtractionResult:
    """Populate ``doc.formulas`` without breaking normal ingestion."""

    summary = FormulaExtractionResult()
    doc.formulas = []

    candidates = _candidate_blocks(
        doc,
        max_blocks=max_blocks,
    )
    summary.candidates = len(candidates)

    if not candidates:
        summary.completed = True
        doc.meta["formula_extraction"] = summary.to_dict()
        return summary

    breadcrumbs = _block_breadcrumbs(doc)
    by_id = {block.id: block for block in candidates}
    order = {
        block.id: index
        for index, block in enumerate(candidates)
    }

    payload = [
        {
            "block_id": block.id,
            "page": block.page,
            "type": block.type,
            "breadcrumb": breadcrumbs.get(
                block.id,
                [doc.name],
            ),
            "text": block.text,
        }
        for block in candidates
    ]

    try:
        response = llm.chat(
            build_formula_extract_messages(
                doc_name=doc.name,
                candidates=payload,
            ),
            temperature=0.0,
            max_tokens=1200,
            response_json=True,
            purpose="rag.ingest",
        )
    except Exception as exc:  # graceful degradation
        summary.error = (
            f"{type(exc).__name__}: {exc}"
        )[:500]
        doc.meta["formula_extraction"] = summary.to_dict()
        return summary

    data = _extract_json(response.content)
    raw_formulas = (
        data.get("formulas")
        if isinstance(data, dict)
        else None
    )

    if not isinstance(raw_formulas, list):
        summary.error = "LLM output is not valid formula JSON"
        doc.meta["formula_extraction"] = summary.to_dict()
        return summary

    validator = FormulaEngine(llm=llm)
    accepted: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for index, raw in enumerate(
        raw_formulas[:max_formulas],
        start=1,
    ):
        try:
            if not isinstance(raw, dict):
                raise ValueError("formula item must be an object")

            block_id = str(
                raw.get("block_id") or ""
            ).strip()
            if block_id not in by_id:
                raise ValueError(
                    f"unknown block_id: {block_id!r}"
                )

            name = str(raw.get("name") or "").strip()
            latex = str(raw.get("latex") or "").strip()

            if not name:
                raise ValueError("formula name is empty")
            if not latex:
                raise ValueError("formula expression is empty")
            if len(latex) > 500:
                raise ValueError("formula expression is too long")

            params = _validate_params(raw.get("params"))
            expression = validator.latex_to_sympy(
                latex,
                list(params),
            )
            free_symbols = {
                str(symbol)
                for symbol in expression.free_symbols
            }

            if free_symbols != set(params):
                raise ValueError(
                    "expression variables do not exactly match params: "
                    f"{sorted(free_symbols)} != {sorted(params)}"
                )

            identity = (block_id, name, latex)
            if identity in seen:
                continue
            seen.add(identity)

            accepted.append(
                {
                    "block_id": block_id,
                    "name": name[:200],
                    "latex": latex,
                    "params": params,
                }
            )
        except Exception as exc:
            summary.rejected.append(
                {
                    "index": index,
                    "reason": str(exc)[:300],
                }
            )

    accepted.sort(
        key=lambda item: (
            order[item["block_id"]],
            item["name"],
            item["latex"],
        )
    )

    formulas = []
    for index, item in enumerate(accepted, start=1):
        block = by_id[item["block_id"]]
        formulas.append(
            FormulaIR(
                id=f"{doc.doc_id}-f{index}",
                doc_id=doc.doc_id,
                page=block.page,
                breadcrumb=breadcrumbs.get(
                    block.id,
                    [doc.name],
                ),
                name=item["name"],
                latex=item["latex"],
                params=item["params"],
            )
        )

    doc.formulas = formulas
    summary.extracted = len(formulas)
    summary.completed = True
    doc.meta["formula_extraction"] = summary.to_dict()
    return summary
