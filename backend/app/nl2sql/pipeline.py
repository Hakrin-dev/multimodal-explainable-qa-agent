"""NL2SQL pipeline v0 — W1 minimal closed loop (single-table focus):

    question → ① rewrite (term linking) → ② schema context → ③ SQL generate
    (dual output: analysis draft + SQL) → ④ sqlglot validate → ⑤ execute
    → repair loop (≤3) → ⑥ summarize + chart hint

Every step records TraceNode(s). W3 adds schema linking compression,
join-path injection and empty-result attribution on this same skeleton.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.llm import LLMService, get_llm_service
from ..core.prompts import nl2sql as prompts
from ..core.tracing import NodeStatus, TraceCollector, NodeType
from ..db import schema_meta
from . import executor, rewriter
from .validator import SQLValidator

MAX_REPAIR_ROUNDS = 3

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}
_TOPN_PATTERNS = [
    re.compile(r"[前最]\s*([0-9]+|[一二两三四五六七八九十]+)\s*(个|名|首|位|条|张|家|款|大|的)"),
    re.compile(r"[Tt]op\s*([0-9]+)"),
]


def _parse_cn_num(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    # 十/十X/X十/X十Y (≤99)
    if s == "十":
        return 10
    if "十" in s:
        left, _, right = s.partition("十")
        tens = _CN_NUM.get(left, 1) if left else 1
        ones = _CN_NUM.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_NUM.get(s)


def topn_requirement(question: str) -> int | None:
    """Extract a Top-N requirement from the question (None if absent)."""
    for pat in _TOPN_PATTERNS:
        m = pat.search(question)
        if m:
            n = _parse_cn_num(m.group(1))
            if n and 1 <= n <= 100:
                return n
    return None

_SQL_FENCE = re.compile(r"```sql\s*(.+?)```", re.S | re.I)
_SQL_NONE = re.compile(r"^\s*(none|null|无)\s*$", re.I)


@dataclass
class NL2SQLResult:
    question: str
    rewritten: str = ""
    analysis: str = ""
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    summary: str = ""
    chart_hint: str | None = None
    status: str = "ok"  # ok | empty | error | needs_clarification
    errors: list[str] = field(default_factory=list)
    repair_rounds: int = 0
    trace: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


class NL2SQLPipeline:
    def __init__(self, llm: LLMService | None = None, max_rows: int | None = None):
        from ..core.config import get_settings
        self.settings = get_settings()
        self.llm = llm or get_llm_service()
        self.max_rows = max_rows or self.settings.sql_max_rows

    # ------------------------------------------------------------------ api

    def run(self, question: str, trace: TraceCollector | None = None,
            history: list[dict] | None = None,
            parent=None) -> NL2SQLResult:
        """parent: the kernel's tool_call node — pipeline steps nest under it
        (contract: tool_call 节点下挂流水线子树). Standalone calls pass None."""
        trace = trace or TraceCollector(question=question)
        t0 = time.monotonic()
        result = NL2SQLResult(question=question)

        def _p(default=None):
            return parent if parent is not None else default

        # ① rewrite (term linking)
        with trace.span("rewrite", NodeType.STEP, parent=_p(), input=question) as node:
            rw = rewriter.rewrite(question)
            result.rewritten = rw.question
            trace.finish(node, output=rw.question,
                         detail={"rewrites": rw.rewrites} if rw.rewrites else {})

        # ② schema context (W1: full 11-table DDL + samples; W3: compressed)
        with trace.span("schema_context", NodeType.STEP, parent=_p(), input=None) as node:
            tables = schema_meta.load_table_meta()
            schema_ctx = schema_meta.build_schema_context(tables, with_samples=True)
            schema_dict = {t.name: {c.name: c.type for c in t.columns} for t in tables}
            trace.finish(node, output=f"{len(tables)} tables loaded")

        # ③④⑤ generate / validate / execute with repair loop
        feedback: str | None = None
        last_sql = ""
        top_n = topn_requirement(result.rewritten or question)
        for round_no in range(1, MAX_REPAIR_ROUNDS + 1):
            gen = self._generate(trace, schema_ctx, result.rewritten or question,
                                 history, feedback, parent=_p())
            result.analysis = gen.get("analysis", result.analysis)
            last_sql = gen.get("sql", "")
            if not last_sql:
                # model declared the question unanswerable
                result.status = "needs_clarification"
                result.errors.append(gen.get("analysis", "模型未能生成 SQL"))
                result.trace = trace.to_dict()
                result.latency_ms = int((time.monotonic() - t0) * 1000)
                return result

            vres = self._validate(trace, last_sql, SQLValidator(schema_dict, self.max_rows),
                                  parent=_p())

            # TOP-N heuristic (mt-002 lesson): question demands 前N but SQL lacks
            # LIMIT — not a syntax error, only detectable against the question text
            if vres.ok and top_n and not re.search(r"\bLIMIT\b", vres.sql, re.I):
                vres = vres.__class__(ok=False, errors=[
                    f"问题要求前 {top_n} 个结果，但 SQL 缺少 LIMIT {top_n}，请修正"])
                with trace.span("sql_validate", NodeType.STEP, parent=_p(),
                                input=last_sql) as hnode:
                    trace.finish(hnode, status=NodeStatus.ERROR, detail={
                        "sql": last_sql, "valid": False, "heuristic": "topn-limit",
                        "errors": vres.errors})

            if not vres.ok:
                result.repair_rounds = round_no
                feedback = f"SQL: {last_sql}\n校验错误: {'; '.join(vres.errors)}"
                result.errors.extend(vres.errors)
                continue

            eres = self._execute(trace, vres.sql, parent=_p())
            if not eres.ok:
                result.repair_rounds = round_no
                feedback = f"SQL: {vres.sql}\n执行错误: {eres.error[:500]}"
                result.errors.append(eres.error[:300])
                continue

            # success
            result.sql = vres.sql
            result.columns, result.rows = eres.columns, eres.rows
            result.row_count, result.truncated = eres.row_count, eres.truncated
            result.status = "ok" if eres.row_count else "empty"
            break
        else:
            result.status = "error"
            result.sql = last_sql

        # ⑥ summarize
        if result.status == "ok":
            self._summarize(trace, result, parent=_p())

        result.trace = trace.to_dict()
        result.latency_ms = int((time.monotonic() - t0) * 1000)
        return result

    # ------------------------------------------------------------- internals

    def _generate(self, trace: TraceCollector, schema_ctx: str, question: str,
                  history: list[dict] | None, feedback: str | None,
                  parent=None) -> dict:
        with trace.span("sql_gen" if not feedback else "sql_repair",
                        NodeType.STEP, parent=parent, input=question) as node:
            messages = prompts.build_messages(schema_ctx, question, history, feedback)
            resp = self.llm.chat(messages, temperature=0.0,
                                 purpose="nl2sql.generate")
            with trace.span("llm", NodeType.LLM_CALL, parent=node) as llm_node:
                trace.finish(llm_node, output=resp.content[:400], detail={
                    "model": resp.model, "cost_rmb": resp.cost_rmb,
                    "tokens": {"prompt": resp.usage.prompt_tokens,
                               "completion": resp.usage.completion_tokens},
                })
            parsed = self._parse_generation(resp.content)
            trace.finish(node, output=parsed.get("sql", "")[:500],
                         detail={"analysis": parsed.get("analysis", ""),
                                 "repair": bool(feedback)})
            return parsed

    @staticmethod
    def _parse_generation(content: str) -> dict:
        analysis = ""
        m = re.search(r"【分析】\s*(.+?)(?=【SQL】|$)", content, re.S)
        if m:
            analysis = m.group(1).strip()
        sql = ""
        m = _SQL_FENCE.search(content)
        if m:
            sql = m.group(1).strip()
        else:
            # tolerate missing fences: take the first SELECT ... block
            m2 = re.search(r"(SELECT .+?)(?:;|$)", content, re.S | re.I)
            sql = m2.group(1).strip() if m2 else ""
        if _SQL_NONE.match(sql or ""):  # model explicitly declined
            sql = ""
        return {"analysis": analysis, "sql": sql}

    def _validate(self, trace: TraceCollector, sql: str, validator: SQLValidator,
                  parent=None):
        with trace.span("sql_validate", NodeType.STEP, parent=parent, input=sql) as node:
            vres = validator.validate(sql)
            trace.finish(node, output=vres.sql or None, status=(
                NodeStatus.OK if vres.ok else NodeStatus.ERROR),
                detail={"sql": sql, "valid": vres.ok, "errors": vres.errors})
            return vres

    def _execute(self, trace: TraceCollector, sql: str, parent=None):
        with trace.span("sql_execute", NodeType.STEP, parent=parent, input=sql) as node:
            eres = executor.execute_sql(sql, self.max_rows, self.settings.sql_timeout_ms)
            trace.finish(node, status=NodeStatus.OK if eres.ok else NodeStatus.ERROR,
                         detail={"row_count": eres.row_count,
                                 "truncated": eres.truncated,
                                 **({"error": eres.error[:300]} if not eres.ok else {})})
            return eres

    def _summarize(self, trace: TraceCollector, result: NL2SQLResult,
                   parent=None) -> None:
        with trace.span("summarize", NodeType.STEP, parent=parent, input=None) as node:
            messages = prompts.build_summarize_messages(
                result.question, result.sql, result.columns, result.rows)
            resp = self.llm.chat(messages, temperature=0.3, max_tokens=300,
                                 purpose="nl2sql.summarize")
            with trace.span("llm", NodeType.LLM_CALL, parent=node) as llm_node:
                trace.finish(llm_node, output=None, detail={
                    "model": resp.model, "cost_rmb": resp.cost_rmb,
                    "tokens": {"prompt": resp.usage.prompt_tokens,
                               "completion": resp.usage.completion_tokens},
                })
            result.summary = resp.content.strip()
            result.chart_hint = _guess_chart_hint(result.columns, result.rows)
            trace.finish(node, output=result.summary, detail={
                "chart_hint": result.chart_hint})


def _guess_chart_hint(columns: list[str], rows: list[list]) -> str | None:
    """Cheap heuristic chart suggestion (W4 upgrades to LLM struct output)."""
    if not rows or len(columns) < 2 or len(rows) < 2:
        return None
    numeric_cols = [i for i in range(1, len(columns))
                    if all(_is_num(r[i]) for r in rows[:10] if r[i] is not None)]
    if not numeric_cols:
        return None
    first = str(rows[0][0] or "")
    if re.match(r"\d{4}[-/]\d{1,2}", first):  # looks like a date series
        return "line"
    return "bar"


def _is_num(v: Any) -> bool:
    if isinstance(v, (int, float)):
        return True
    return isinstance(v, str) and bool(re.match(r"^-?[\d,.]+$", v))
