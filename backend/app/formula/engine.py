"""Formula computation engine (PLAN §4.4, 中级 #6).

Minimal closed loop (W4-D1):

    question ──► resolve (match kb_formula by name/doc keywords)
            ──► bind_params (db|doc|user three-channel, §4.4 6a)
                · db   : LLM emits a NL param query → NL2SQLPipeline → numeric
                · user : LLM extracts a literal from the question
                · doc  : RAG retriever fetches a numeric literal from kb_chunk
            ──► latex_to_sympy (parse_expr, max/min/abs mapped, symbols = params)
            ──► dual_eval (channel A: sympy.N(subs); channel B: restricted python eval)
            ──► steps → Trace (tool_call node nests param sub-queries + compute)

Cross-source type ② (Doc→DB 参数查询型): formula lives in a document, the
parameter value comes from the database. Demo scenario #7.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import sympy
from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from ..core.llm import LLMService, get_llm_service
from ..core.tracing import NodeStatus, NodeType, TraceCollector
from . import store

# Restrict what a registered latex may reference. parse_expr still parses
# arbitrary text, so we additionally deny tokens that could reach dunder or
# import machinery; the eval namespace below keeps __builtins__ empty.
_FORBIDDEN = re.compile(r"__|import|lambda|exec|eval|open|getattr|setattr|class|def\b")

# Function names allowed in registered latex, mapped to SymPy callables.
_SAFE_FUNCS = {
    "max": sympy.Max,
    "min": sympy.Min,
    "abs": sympy.Abs,
}

_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)


@dataclass
class FormulaResult:
    ok: bool
    value: float | None = None
    steps: list[str] = field(default_factory=list)
    formula: dict = field(default_factory=dict)   # {id, name, latex, doc_id, page, breadcrumb}
    params: dict[str, dict] = field(default_factory=dict)   # {name: {value, source, unit, query?}}
    citations: list[dict] = field(default_factory=list)
    degraded_reason: str = ""


_PARAM_QUERY_SYSTEM = (
    "你是参数提取助手。给定一个计算型问题和某个参数的语义描述，"
    "输出一个可直接问业务数据库的自然语言短问句。规则："
    "必须使用原问题中出现的具体人名（如 Jane、张三），不要用角色名（如销售支持专员）；"
    "去掉'当月/本月'等无法直接查询的时间词，改查该实体的累计总额；"
    "只含实体+指标，不含'提成/公式'等公式词。只输出问句本身，不要解释。"
)

_PARAM_EXTRACT_SYSTEM = (
    "你是参数提取助手。从给定问题中抽取指定参数的数值（含中文/阿拉伯数字），"
    "只输出纯数字（可含小数点和负号），不要单位或解释。若无法确定则输出 NONE。"
)

_ESTIMATE_SYSTEM = (
    "你是估算助手。根据问题描述与参数语义，给出该参数值的粗估数量级（一个数字），"
    "只输出数字，不要解释。"
)


class FormulaEngine:
    def __init__(self, llm: LLMService | None = None):
        self.llm = llm or get_llm_service()

    # ------------------------------------------------------------ resolve --

    def resolve(self, question: str):
        """Match the question against registered kb_formula entries.

        Keyword overlap on name + doc_id; falls back to the single registered
        formula when only one exists (common in the minimal closed loop).
        """
        formulas = store.list_all()
        if not formulas:
            return None
        q = question
        best, best_score = None, 0
        for f in formulas:
            score = 0
            for kw in (f.name, f.doc_id):
                if kw and kw in q:
                    score += len(kw)
            if score > best_score:
                best, best_score = f, score
        if best is None and len(formulas) == 1:
            best = formulas[0]
        return best

    # -------------------------------------------------------- bind params --

    def bind_params(self, formula, question: str, trace: TraceCollector,
                    parent) -> tuple[dict[str, float], list[dict], list[str]]:
        """Resolve every parameter per its declared source channel.

        Returns (values, citations, steps). db-sourced params nest an nl2sql
        tool_call span under `parent` (Trace fidelity for cross-source DAG).
        """
        values: dict[str, float] = {}
        citations: list[dict] = []
        steps: list[str] = []
        for pname, pmeta in formula.params.items():
            source = (pmeta or {}).get("source", "user")
            desc = (pmeta or {}).get("desc", pname)
            unit = (pmeta or {}).get("unit", "")
            if source == "db":
                value, cite, step = self._bind_db(pname, desc, question, trace, parent)
            elif source == "doc":
                value, cite, step = self._bind_doc(pname, desc, question)
            else:  # user
                value, cite, step = self._bind_user(pname, desc, question), [], ""
            if value is None:
                raise ValueError(f"参数 {pname}（{desc}）无法从{source}通道取值")
            values[pname] = float(value)
            citations.extend(cite)
            steps.append(step or f"{pname}（{desc}）= {value:g}{(' ' + unit) if unit else ''}")
        return values, citations, steps

    def _bind_db(self, pname, desc, question, trace, parent):
        sub_q = self._llm_param_query(question, pname, desc)
        with trace.span(f"param:{pname}:nl2sql", NodeType.TOOL_CALL,
                        input=sub_q, parent=parent) as node:
            from ..nl2sql.pipeline import NL2SQLPipeline
            r = NL2SQLPipeline().run(sub_q, trace=trace, parent=node)
            node.status = NodeStatus.OK if r.status == "ok" else NodeStatus.DEGRADED
            node.output = (r.summary or r.sql or "")[:160]
        if r.status != "ok" or not r.rows:
            raise ValueError(f"参数 {pname} 的 DB 查询未返回数据：{sub_q}")
        # Prefer the summary's bottom-line number (nl2sql phrases the
        # answer in prose) to avoid count-vs-sum misreads; the largest
        # numeric token in the summary is the magnitude we want.
        value = None
        for m in re.finditer(r"-?\d+(?:\.\d+)?", r.summary or ""):
            v = float(m.group(0))
            if value is None or v > value:
                value = v
        if value is None:
            value = _coerce_number(r.rows[0][0])
        if value is None:
            for cell in r.rows[0]:
                value = _coerce_number(cell)
                if value is not None:
                    break
        if value is None:
            raise ValueError(f"参数 {pname} 的 DB 查询结果无可转数值：{r.rows[0]}")
        cite = [{"doc": "业务数据库", "doc_id": "_db", "page": 0,
                 "breadcrumb": ["数据库查询"], "snippet": r.sql, "score": 1.0}]
        return value, cite, f"{pname}（{desc}）← DB 查询「{sub_q}」= {value:g}"

    def _bind_doc(self, pname, desc, question):
        from ..rag.retriever import HybridRetriever
        from ..rag.store import load_chunks
        retriever = HybridRetriever()
        retriever.ensure_loaded(chunks=load_chunks())
        hits = retriever.search(f"{desc} {question}", top_k=3)
        for h in hits:
            m = re.search(r"-?\d+(?:\.\d+)?", h["text"])
            if m:
                return float(m.group(0))
        return None

    def _bind_user(self, pname, desc, question):
        resp = self.llm.chat(
            [{"role": "system", "content": _PARAM_EXTRACT_SYSTEM},
             {"role": "user", "content": f"问题：{question}\n参数：{pname}（{desc}）"}],
            temperature=0.0, max_tokens=40, purpose="formula.param_extract")
        m = re.search(r"-?\d+(?:\.\d+)?", resp.content or "")
        return float(m.group(0)) if m else None

    def _llm_param_query(self, question, pname, desc):
        resp = self.llm.chat(
            [{"role": "system", "content": _PARAM_QUERY_SYSTEM},
             {"role": "user",
             "content": f"原问题：{question}\n参数：{pname}（{desc}）\n"
                        "输出查询该参数数值的数据库问句："}],
            temperature=0.0, max_tokens=80, purpose="formula.param_query")
        q = (resp.content or "").strip().splitlines()[0].strip().strip('"').strip("'")
        return q or f"{question}"

    # ----------------------------------------------------- latex → sympy --

    def latex_to_sympy(self, latex: str, param_names: list[str]):
        if _FORBIDDEN.search(latex):
            raise ValueError(f"latex contains a forbidden token: {latex!r}")
        local = dict(_SAFE_FUNCS)
        local.update({n: sympy.Symbol(n) for n in param_names})
        return parse_expr(latex, local_dict=local, transformations=_TRANSFORMS,
                          evaluate=True)

    # --------------------------------------------------------- dual eval --

    def dual_eval(self, latex: str, values: dict[str, float]) -> tuple[float, float]:
        """Channel A (SymPy symbolic) + Channel B (restricted python eval)."""
        expr = self.latex_to_sympy(latex, list(values.keys()))
        sym_subs = {sympy.Symbol(n): v for n, v in values.items()}
        channel_a = float(sympy.N(expr.subs(sym_subs)))

        # channel B: independent restricted-eval path
        ns: dict = {"max": max, "min": min, "abs": abs}
        ns.update(values)
        channel_b = float(eval(latex, {"__builtins__": {}}, ns))  # noqa: S307 - restricted ns
        return channel_a, channel_b

    # --------------------------------------------------------------- run --

    def run(self, question: str, trace: TraceCollector,
            parent=None) -> FormulaResult:
        formula = self.resolve(question)
        if formula is None:
            return FormulaResult(ok=False, degraded_reason="知识库中未登记匹配公式")

        with trace.span("formula:resolve", NodeType.STEP, input=question,
                        parent=parent) as n:
            trace.finish(n, output={"formula_id": formula.id, "name": formula.name})

        try:
            values, citations, steps = self.bind_params(
                formula, question, trace, parent)
        except ValueError as exc:
            return FormulaResult(ok=False, formula=_formula_dict(formula),
                                 degraded_reason=str(exc))

        with trace.span("formula:compute", NodeType.STEP,
                        input={"latex": formula.latex, "params": values},
                        parent=parent) as cn:
            a, b = self.dual_eval(formula.latex, values)
            ok = abs(a - b) <= max(1e-6, abs(a) * 1e-9)
            trace.finish(cn, output={"channel_sympy": a, "channel_eval": b,
                                     "consistent": ok},
                         status=NodeStatus.OK if ok else NodeStatus.DEGRADED)
        if not ok:
            return FormulaResult(ok=False, formula=_formula_dict(formula),
                                 params=_param_map(formula, values),
                                 degraded_reason=f"双通道校验不一致：SymPy={a} vs eval={b}")

        # human-readable compute trace
        expr_substituted = formula.latex
        for n, v in values.items():
            expr_substituted = expr_substituted.replace(n, f"{v:g}")
        steps.append(f"代入公式：{expr_substituted}")
        steps.append(f"= {a:g}")

        # optional magnitude sanity (zero-cost heuristic, not LLM)
        if a < 0:
            return FormulaResult(ok=False, formula=_formula_dict(formula),
                                 degraded_reason=f"计算结果为负（{a}），与业务语义不符")

        return FormulaResult(
            ok=True, value=a,
            steps=["公式：" + formula.name + "  " + formula.latex] + steps,
            formula=_formula_dict(formula),
            params=_param_map(formula, values),
            citations=citations,
        )

    # ----------------------------------------------------- estimate (LLM) --

    def estimate(self, question: str, desc: str) -> float | None:
        """LLM magnitude estimate — reserved for #6b 双通道校验的 LLM 估算档."""
        resp = self.llm.chat(
            [{"role": "system", "content": _ESTIMATE_SYSTEM},
             {"role": "user", "content": f"问题：{question}\n参数：{desc}"}],
            temperature=0.0, max_tokens=40, purpose="formula.estimate")
        m = re.search(r"-?\d+(?:\.\d+)?", resp.content or "")
        return float(m.group(0)) if m else None


# ----------------------------------------------------------------- helpers --

def _coerce_number(cell) -> float | None:
    if cell is None:
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    try:
        from decimal import Decimal
        if isinstance(cell, Decimal):
            return float(cell)
    except Exception:  # noqa: BLE001
        pass
    s = str(cell).replace(",", "").replace("¥", "").replace("元", "").strip()
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else None


def _formula_dict(f) -> dict:
    return {"id": f.id, "name": f.name, "latex": f.latex,
            "doc_id": f.doc_id, "page": f.page, "breadcrumb": list(f.breadcrumb)}


def _param_map(f, values: dict[str, float]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for n, v in values.items():
        meta = (f.params.get(n) or {})
        out[n] = {"value": v, "source": meta.get("source", "user"),
                  "unit": meta.get("unit", ""), "desc": meta.get("desc", n)}
    return out
