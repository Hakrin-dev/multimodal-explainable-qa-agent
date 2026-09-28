"""Tool registry (PLAN §4.1 工具集) — what the kernel may schedule.

ToolSpec.handler 签名：handler(question, trace, parent=None, history=None, **kw)。
kernel 创建 tool_call span 并作为 parent 传入，流水线步骤嵌套其下
（契约：tool_call 节点下挂流水线子树）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.tracing import NodeStatus, NodeType, TraceCollector


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    degraded_reason: str = ""   # set when ok=False but system stayed honest


@dataclass
class ToolSpec:
    name: str
    description: str            # shown to the planner / intent prompt
    handler: Callable[..., ToolResult]


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    @property
    def inventory(self) -> str:
        """Semi-static prompt block: tool signatures for intent/planner."""
        return "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())


def default_registry() -> ToolRegistry:
    """Production registry wiring the real pipelines."""
    reg = ToolRegistry()
    reg.register(ToolSpec("nl2sql", "结构化问数：查询业务数据库（销量/客户/曲目/发票/员工等），"
                                  "返回 SQL、结果行与图表建议", _tool_nl2sql))
    reg.register(ToolSpec("rag_search", "知识库问答：检索公司文档（员工手册/销售总结/客服 SOP 等），"
                                        "返回带引用的答案", _tool_rag))
    reg.register(ToolSpec("db_lookup_entity", "术语反查：把业务别名映射到标准实体（如 帝都->北京）",
                           _tool_db_lookup_entity))
    reg.register(ToolSpec("formula_eval", "公式计算：按文档登记的公式代入参数计算（W4 提供）",
                           _tool_formula_eval))
    return reg


# ----------------------------------------------------------------- tools --

def _tool_nl2sql(question: str, trace: TraceCollector,
                 history: list[dict] | None = None, parent=None,
                 **_: Any) -> ToolResult:
    from ..nl2sql.pipeline import NL2SQLPipeline
    from ..nl2sql.pipeline import NL2SQLResult

    result: NL2SQLResult = NL2SQLPipeline().run(question, trace=trace,
                                                history=history, parent=parent)
    if result.status == "error":
        return ToolResult(ok=False, data={"errors": result.errors},
                          degraded_reason="SQL 生成失败（自修复后仍不可用）")
    # compact key for downstream placeholders (DAG dependency substitution):
    # injecting a full summary into a RAG query dilutes retrieval — prefer the
    # string cells of the first row (entity names), fall back to first sentence
    key = ""
    if result.rows:
        str_cells = [str(v).strip() for v in result.rows[0]
                     if isinstance(v, str) and v.strip()]
        key = " ".join(str_cells)[:60]
    if not key:
        key = (result.summary or "").split("。")[0].strip()[:60]
    return ToolResult(ok=True, data={
        "sql": result.sql, "columns": result.columns,
        "rows": result.rows, "row_count": result.row_count,
        "chart_hint": result.chart_hint, "summary": result.summary,
        "key": key,
        "analysis": result.analysis, "status": result.status,
        "missing": result.errors[:1] if result.status == "needs_clarification" else [],
    })


def _tool_rag(question: str, trace: TraceCollector, parent=None, **_: Any) -> ToolResult:
    from ..rag.pipeline import RAGPipeline

    try:
        r = RAGPipeline().run(question, trace=trace, parent=parent)
    except FileNotFoundError as e:
        # clean state: embedding model not downloaded — degrade honestly
        if parent is not None:
            from ..core.tracing import NodeStatus as _S
            parent.status = _S.DEGRADED
            parent.detail["degraded"] = True
            parent.output = str(e)[:200]
        else:
            with trace.span("rag_search", NodeType.TOOL_CALL, input=question) as node:
                from ..core.tracing import NodeStatus as _S
                trace.finish(node, status=_S.DEGRADED, output=str(e)[:200],
                             detail={"degraded": True})
        return ToolResult(ok=False, data={}, degraded_reason="知识库未就绪（嵌入模型未部署）")
    if r.status == "no_context":
        return ToolResult(ok=False, data={"answer": r.answer},
                          degraded_reason="知识库中未找到相关内容")
    return ToolResult(ok=True, data={
        "answer": r.answer, "citations": r.citations,
    })


def _tool_db_lookup_entity(term: str, **_: Any) -> ToolResult:
    from ..db.session import get_conn

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT canonical_name, binding, description FROM biz_term"
            " WHERE %s = ANY(aliases) OR canonical_name = %s LIMIT 3", (term, term))
        rows = cur.fetchall()
    if not rows:
        return ToolResult(ok=False, data={}, degraded_reason=f"术语库中无 {term!r}")
    return ToolResult(ok=True, data={
        "matches": [{"canonical": r[0], "binding": r[1], "description": r[2]} for r in rows]})


def _tool_formula_eval(formula_id: str, params: dict | None = None, **_: Any) -> ToolResult:
    # W4 公式引擎占位（FormulaIR 契约已冻结）
    return ToolResult(ok=False, data={}, degraded_reason="公式计算引擎 W4 上线")
