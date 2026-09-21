"""Intent classification (PLAN §4.1 step ①): one LLM call, structured output.

Fallback ladder (robustness): JSON parse -> one repair retry -> keyword
heuristic. Never raises; worst case returns DB_QUERY (most common intent).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..core.llm import LLMService, get_llm_service
from ..core.prompts import agent as prompts

VALID_INTENTS = {"CHAT", "DB_QUERY", "DOC_QUERY", "HYBRID", "AMBIGUOUS"}

_DOC_HINTS = re.compile(r"手册|制度|规定|规章|流程|SOP|文档|知识库|方法论|年假|提成|报销|禁语")
_DB_HINTS = re.compile(r"多少|哪些|统计|销量|销售额|排行|排名|[Tt]op|前\s*\d|数据库|客户|曲目|专辑|发票|订单|冠军|金额|总数|数量|增长率")


@dataclass
class IntentResult:
    intent: str
    confidence: float = 0.5
    slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    sub_tasks: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False

    @property
    def needs_clarification(self) -> bool:
        return self.intent == "AMBIGUOUS" and bool(self.missing_slots)


def _parse(content: str) -> dict | None:
    """Extract the first JSON object from a (possibly fenced) response."""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _heuristic(question: str) -> str:
    has_doc, has_db = bool(_DOC_HINTS.search(question)), bool(_DB_HINTS.search(question))
    if has_doc and has_db:
        return "HYBRID"
    if has_doc:
        return "DOC_QUERY"
    if has_db:
        return "DB_QUERY"
    return "CHAT"


class IntentClassifier:
    def __init__(self, llm: LLMService | None = None):
        self.llm = llm or get_llm_service()

    def classify(self, question: str, history: list[dict] | None = None) -> IntentResult:
        messages = prompts.build_intent_messages(question, history)
        resp = self.llm.chat(messages, temperature=0.0, max_tokens=300,
                             response_json=True, purpose="agent.intent")
        data = _parse(resp.content)

        if data is None or data.get("intent") not in VALID_INTENTS:
            # one repair retry with explicit feedback
            resp = self.llm.chat(
                messages + [{"role": "assistant", "content": resp.content},
                            {"role": "user", "content":
                             "上面的输出不是合法 JSON 或意图值非法。请重新输出严格 JSON。"}],
                temperature=0.0, max_tokens=300, response_json=True,
                purpose="agent.intent_repair")
            data = _parse(resp.content)

        if data is None or data.get("intent") not in VALID_INTENTS:
            intent = _heuristic(question)
            return IntentResult(intent=intent, confidence=0.3, fallback_used=True)

        return IntentResult(
            intent=data["intent"],
            confidence=float(data.get("confidence", 0.5) or 0.5),
            slots=data.get("slots") or {},
            missing_slots=data.get("missing_slots") or [],
            sub_tasks=data.get("sub_tasks") or [],
            raw=data,
        )
