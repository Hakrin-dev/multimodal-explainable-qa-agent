"""Pipeline unit tests (no DB) + integration tests (need live PG)."""

from __future__ import annotations

import pytest

from app.core.llm import LLMService, MockProvider
from app.core.config import Settings
from app.core.llm import _LLMStore
from app.nl2sql.pipeline import NL2SQLPipeline, _guess_chart_hint

from app.db.session import test_connection

requires_db = pytest.mark.skipif(
    not test_connection(), reason="live PostgreSQL required (run quick_start.sh)"
)


# ------------------------------------------------------------------ units --

def test_parse_generation_fence():
    content = "【分析】查询曲目表按字节数排序\n【SQL】\n```sql\nSELECT name FROM track ORDER BY bytes DESC LIMIT 10\n```"
    parsed = NL2SQLPipeline._parse_generation(content)
    assert parsed["sql"].startswith("SELECT") and "LIMIT 10" in parsed["sql"]
    assert "字节数" in parsed["analysis"]


def test_parse_generation_none():
    content = "【分析】缺少时间范围，无法计算增长率\n【SQL】\n```sql\nNone\n```"
    parsed = NL2SQLPipeline._parse_generation(content)
    assert parsed["sql"] == "" and "时间范围" in parsed["analysis"]


def test_parse_generation_tolerant_no_fence():
    content = "【分析】x\n【SQL】SELECT 1;"
    parsed = NL2SQLPipeline._parse_generation(content)
    assert parsed["sql"].startswith("SELECT")


def test_chart_hint_heuristic():
    assert _guess_chart_hint(["name", "total"], [["a", 1], ["b", 2]]) == "bar"
    assert _guess_chart_hint(["month", "revenue"],
                             [["2024-01", 1], ["2024-02", 2]]) == "line"
    assert _guess_chart_hint(["name"], [["a"]]) is None


def _mock_llm(tmp_path, scripted):
    svc = LLMService(Settings(llm_provider="mock"), store=_LLMStore(tmp_path / "u.sqlite"))
    svc.register_mock(MockProvider(scripted=scripted))
    return svc


def test_pipeline_needs_clarification_without_db(monkeypatch, tmp_path):
    """Model outputs SQL: None -> needs_clarification, no DB touched."""
    from app.nl2sql import pipeline as pl

    # stub schema-dependent pieces to avoid DB
    class FakeTable:
        name = "track"
        class col:  # noqa: N801
            pass
        columns = []
        row_count = 1
    monkeypatch.setattr(pl.schema_meta, "load_table_meta", lambda: [])
    monkeypatch.setattr(pl.schema_meta, "build_schema_context", lambda t=None, **k: "")
    monkeypatch.setattr(pl.rewriter, "rewrite", lambda q: type("R", (), {
        "question": q, "rewrites": [], "changed": False})())

    p = NL2SQLPipeline(llm=_mock_llm(tmp_path, [
        "【分析】缺时间范围\n【SQL】\n```sql\nNone\n```",
    ]))
    r = p.run("增长率是多少？")
    assert r.status == "needs_clarification"
    assert r.trace["root"]["children"][0]["label"] == "rewrite"


# ------------------------------------------------------------ integration --

@requires_db
def test_pipeline_end_to_end_mock(tmp_path):
    """Full loop with a scripted mock SQL answer against the live Chinook DB."""
    p = NL2SQLPipeline(llm=_mock_llm(tmp_path, [
        "【分析】统计曲目总数\n【SQL】\n```sql\nSELECT COUNT(*) AS cnt FROM track\n```",
        "数据库中共有若干首曲目。",
    ]))
    r = p.run("一共有多少首曲目？")
    assert r.status == "ok"
    assert r.columns == ["cnt"]
    assert r.rows[0][0] > 3000  # Chinook has 3503 tracks
    assert r.chart_hint is None
    assert r.trace["root"]["children"][0]["label"] == "rewrite"
    labels = [c["label"] for c in r.trace["root"]["children"]]
    assert "sql_gen" in labels and "sql_validate" in labels and "sql_execute" in labels


@requires_db
def test_repair_loop_recovers(tmp_path):
    """First SQL invalid (unknown table) -> repair round fixes it."""
    p = NL2SQLPipeline(llm=_mock_llm(tmp_path, [
        "【分析】误写表名\n【SQL】\n```sql\nSELECT COUNT(*) FROM tracks\n```",
        "【分析】修正表名\n【SQL】\n```sql\nSELECT COUNT(*) AS cnt FROM track\n```",
        "共若干首曲目。",
    ]))
    r = p.run("多少曲目？")
    assert r.status == "ok" and r.repair_rounds == 1
    labels = [c["label"] for c in r.trace["root"]["children"]]
    assert "sql_repair" in labels


@requires_db
def test_rewriter_alias(tmp_path):
    from app.nl2sql.rewriter import load_terms, rewrite
    terms = load_terms()
    assert any(t["canonical"] == "Rock" for t in terms)
    r = rewrite("摇滚曲风有多少首歌", terms)
    assert "Rock" in r.question
    assert any(w["before"] == "摇滚" for w in r.rewrites)


# ------------------------------------------------------- top-n heuristic --

@pytest.mark.parametrize("q,n", [
    ("销量前十的曲目", 10), ("前 5 名客户", 5), ("消费最高的前3位", 3),
    ("Top 10 artists", 10), ("曲风种类", None), ("所有客户", None),
])
def test_topn_requirement(q, n):
    from app.nl2sql.pipeline import topn_requirement
    assert topn_requirement(q) == n


def test_topn_no_false_positive_on_time_span():
    from app.nl2sql.pipeline import topn_requirement
    assert topn_requirement("前一年的销售额是多少") is None
    assert topn_requirement("销量前十的曲目") == 10


def test_chart_hint_decimal_columns():
    """Regression (W2-D4): psycopg returns Decimal — chart heuristic must
    still recognize numeric columns (demo scenario 1 'no chart hint')."""
    from decimal import Decimal
    from app.nl2sql.pipeline import _guess_chart_hint
    assert _guess_chart_hint(["name", "total_quantity"],
                             [["The Trooper", Decimal("5")], ["Eruption", Decimal("4")]]) == "bar"
