"""Opt-in retrieval optimization; one bounded call, fail closed to original query."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..core.llm import LLMResponse, LLMService
from ..core.prompts.rag import build_query_rewrite_messages

MAX_QUERY_CHARS = 2000
MAX_HYDE_CHARS = 2000
MAX_RESPONSE_CHARS = 16000
MAX_HISTORY_CHARS = 500


@dataclass
class QueryRewriteResult:
    original_query: str
    retrieval_query: str
    hyde_document: str = ""
    changed: bool = False
    attempted: bool = False
    degraded: bool = False
    reason: str | None = None
    hyde_enabled: bool = False

    def to_dict(self) -> dict:
        """Audit metadata deliberately excludes the full hypothetical document."""
        return {
            "original_query": self.original_query,
            "retrieval_query": self.retrieval_query,
            "changed": self.changed, "attempted": self.attempted,
            "degraded": self.degraded, "reason": self.reason,
            "hyde_enabled": self.hyde_enabled,
            "hyde_length": len(self.hyde_document),
            "hyde_preview": self.hyde_document[:160],
        }


def rewrite_query(
    llm: LLMService, question: str, history: list[dict] | None = None,
) -> tuple[QueryRewriteResult, list[LLMResponse]]:
    settings = llm.settings
    result = QueryRewriteResult(original_query=question, retrieval_query=question)
    if not settings.rag_query_rewrite_enabled:
        result.reason = "disabled"
        return result, []

    result.attempted = True
    result.hyde_enabled = settings.rag_hyde_enabled
    limit = settings.rag_query_rewrite_history_limit
    bounded_history = []
    for item in (history or [])[-limit:] if limit else []:
        if not isinstance(item, dict):
            continue
        if item.get("role") not in ("user", "assistant"):
            continue
        if not isinstance(item.get("content"), str):
            continue
        bounded_history.append({
            "role": item["role"], "content": item["content"][:MAX_HISTORY_CHARS],
        })
    calls = []
    try:
        response = llm.chat(
            build_query_rewrite_messages(question, bounded_history, result.hyde_enabled),
            temperature=0.0, max_tokens=1000, response_json=True,
            purpose="rag.query_rewrite",
        )
        calls.append(response)
    except Exception as exc:
        result.degraded = True
        result.reason = f"llm_error:{type(exc).__name__}"
        return result, calls

    try:
        if not isinstance(response.content, str):
            raise ValueError("invalid_json")
        if len(response.content) > MAX_RESPONSE_CHARS:
            raise ValueError("response_too_long")
        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, TypeError, RecursionError):
            raise ValueError("invalid_json") from None
        if not isinstance(data, dict):
            raise ValueError("invalid_schema")
        query = data.get("retrieval_query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("missing_query")
        if len(query) > MAX_QUERY_CHARS:
            raise ValueError("query_too_long")
        if not isinstance(data.get("changed"), bool):
            raise ValueError("invalid_changed")
        hyde = data.get("hyde_document")
        if not isinstance(hyde, str):
            raise ValueError("invalid_hyde")
        if result.hyde_enabled and len(hyde) > MAX_HYDE_CHARS:
            raise ValueError("hyde_too_long")
        result.retrieval_query = query.strip()
        result.changed = result.retrieval_query != question
        result.hyde_document = hyde.strip() if result.hyde_enabled else ""
    except ValueError as exc:
        result.degraded = True
        result.reason = str(exc)
    return result, calls
