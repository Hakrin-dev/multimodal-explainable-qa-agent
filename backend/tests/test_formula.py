"""Formula engine tests (W4-D1, 中级 #6 最小闭环).

Pure-function tests (latex_to_sympy / dual_eval / coerce) need no LLM or DB.
Store + resolve tests use the live PG (kb_formula). The end-to-end run test
mocks the LLM and NL2SQLPipeline so it stays offline and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.tracing import NodeType, TraceCollector
from app.formula import store
from app.formula.engine import FormulaEngine, _coerce_number
from app.ingestion.ir import FormulaIR


# ----------------------------------------------------------------- pure fn

def test_latex_to_sympy_maps_max_min_abs():
    eng = FormulaEngine()
    expr = eng.latex_to_sympy("0.03 * S + 0.02 * max(S - 100000, 0)", ["S"])
    import sympy
    val = float(sympy.N(expr.subs(sympy.Symbol("S"), 150000)))
    assert val == pytest.approx(5500)


def test_dual_eval_channels_agree():
    eng = FormulaEngine()
    latex = "0.03 * S + 0.02 * max(S - 100000, 0)"
    for s in (150000, 80000, 100000, 250000):
        a, b = eng.dual_eval(latex, {"S": float(s)})
        assert a == pytest.approx(b, rel=1e-9)
    a, b = eng.dual_eval(latex, {"S": 150000.0})
    assert a == pytest.approx(5500) and b == pytest.approx(5500)


def test_forbidden_token_rejected():
    eng = FormulaEngine()
    with pytest.raises(ValueError):
        eng.latex_to_sympy("__import__('os')", [])
    with pytest.raises(ValueError):
        eng.latex_to_sympy("lambda x: x", ["x"])


def test_coerce_number_handles_decimal_and_strings():
    from decimal import Decimal
    assert _coerce_number(Decimal("123.45")) == pytest.approx(123.45)
    assert _coerce_number("¥12,345 元") == pytest.approx(12345)
    assert _coerce_number(None) is None
    assert _coerce_number("abc") is None


# ------------------------------------------------------------- store / db

@pytest.fixture(autouse=True)
def _seed_formula():
    store.upsert(FormulaIR(
        id="employee_handbook-f1", doc_id="employee_handbook", page=1,
        breadcrumb=["Chinook 唱片员工手册", "薪酬与提成", "销售提成计算办法"],
        name="销售提成", latex="0.03 * S + 0.02 * max(S - 100000, 0)",
        params={"S": {"desc": "销售支持专员当月个人销售额", "unit": "元", "source": "db"}},
    ))
    yield


def test_store_upsert_get_roundtrip():
    f = store.get("employee_handbook-f1")
    assert f is not None
    assert f.name == "销售提成"
    assert f.latex.startswith("0.03")
    assert f.params["S"]["source"] == "db"
    assert any(ff.doc_id == "employee_handbook" for ff in store.list_all())


def test_resolve_matches_keyword():
    eng = FormulaEngine()
    f = eng.resolve("按员工手册提成公式 Jane 提成多少")
    assert f is not None and f.id == "employee_handbook-f1"


# ---------------------------------------------------------- end-to-end run

class _MockLLM:
    """Returns canned param-query strings so the DB channel can be mocked too."""

    def __init__(self, param_query: str):
        self._param_query = param_query

    def chat(self, messages, **_):
        @dataclass
        class R:
            content: str
        return R(self._param_query)

    def chat_stream(self, *a, **k):
        return iter([])


def _patch_nl2sql(monkeypatch, sales_value):
    @dataclass
    class _NR:
        status: str = "ok"
        sql: str = "SELECT 1"
        summary: str = "mock"
        rows: list = None
        columns: list = None

        def __post_init__(self):
            self.rows = [[sales_value]]
            self.columns = ["total"]

    from app.nl2sql import pipeline as nlmod

    class _Pipe:
        def run(self, question, trace=None, parent=None, history=None):
            return _NR()

    monkeypatch.setattr(nlmod, "NL2SQLPipeline", _Pipe)


def test_run_end_to_end_db_param(monkeypatch):
    _patch_nl2sql(monkeypatch, 150000)  # Jane 销售额 15 万
    eng = FormulaEngine(llm=_MockLLM("Jane 名下客户的总消费"))
    trace = TraceCollector(question="按员工手册提成公式 Jane 提成多少")
    with trace.span("formula_eval", NodeType.TOOL_CALL) as parent:
        r = eng.run("按员工手册提成公式 Jane 提成多少", trace, parent=parent)
    assert r.ok
    assert r.value == pytest.approx(5500)   # 0.03*150000 + 0.02*50000
    assert any("代入公式" in s for s in r.steps)
    assert r.params["S"]["value"] == pytest.approx(150000)
    assert r.citations and r.citations[0]["doc_id"] == "_db"


def test_run_no_formula(monkeypatch):
    monkeypatch.setattr(store, "list_all", lambda: [])
    eng = FormulaEngine(llm=_MockLLM(""))
    trace = TraceCollector(question="计算某未知公式")
    r = eng.run("计算某未知公式", trace)
    assert not r.ok and "未登记" in r.degraded_reason


def test_run_dual_channel_mismatch(monkeypatch):
    """If the two evaluation channels diverge, the engine degrades honestly."""
    _patch_nl2sql(monkeypatch, 150000)
    eng = FormulaEngine(llm=_MockLLM("Jane 销售额"))
    real_dual = eng.dual_eval

    def _bad(latex, values):
        a, _ = real_dual(latex, values)
        return a, a + 1  # force divergence

    monkeypatch.setattr(eng, "dual_eval", _bad)
    trace = TraceCollector(question="提成")
    with trace.span("f", NodeType.TOOL_CALL) as parent:
        r = eng.run("按员工手册提成公式 Jane 提成多少", trace, parent=parent)
    assert not r.ok and "双通道校验不一致" in r.degraded_reason
