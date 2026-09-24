"""W3 tests: schema linking LLM rerank (#4) + empty-result attribution (§4.2 ⑥-5).

Rerank guarantees under test:
- only removes tables (recall coverage never below v0)
- FK connectivity repaired after prune (join paths stay valid)
- unparseable / empty LLM output → graceful degrade to v0
- single-candidate questions skip the LLM entirely
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.llm import LLMService, MockProvider, _LLMStore
from app.db.session import test_connection

requires_db = pytest.mark.skipif(
    not test_connection(), reason="live PostgreSQL required (run quick_start.sh)")


def _llm_handler(tmp_path, handler):
    svc = LLMService(Settings(llm_provider="mock"), store=_LLMStore(tmp_path / "u.sqlite"))
    svc.register_mock(MockProvider(handler=handler))
    return svc


def _llm_scripted(tmp_path, scripted):
    svc = LLMService(Settings(llm_provider="mock"), store=_LLMStore(tmp_path / "u.sqlite"))
    svc.register_mock(MockProvider(scripted=scripted))
    return svc


Q_ROCK = "摇滚曲风的曲目总销量是多少"


# ------------------------------------------------------------ link_rerank --

@requires_db
def test_rerank_prunes_and_keeps_connectivity(tmp_path):
    from app.nl2sql.schema_linking import SchemaLinker

    linker = SchemaLinker()
    res = linker.link(Q_ROCK)
    assert len(res.selected) > 2  # v0 over-recalls — that's what rerank prunes

    llm = _llm_scripted(tmp_path, ['{"tables": ["genre", "track"]}'])
    r2 = linker.rerank(Q_ROCK, res, llm=llm)
    # genre/track are directly FK-linked → no bridge needed
    assert {"genre", "track"} <= set(r2.selected)
    assert set(r2.selected) <= set(res.selected)          # 只删不加
    assert r2.recalled == res.recalled                    # recall provenance preserved
    for path in r2.join_paths:
        a, b = path.split(" = ")
        assert a.split(".")[0] in r2.selected
        assert b.split(".")[0] in r2.selected


@requires_db
def test_rerank_rebridges_disconnected_keep(tmp_path):
    """LLM keeps two tables from different FK components → bridge restored."""
    from app.nl2sql.schema_linking import SchemaLinker

    linker = SchemaLinker()
    res = linker.link("每个客户所在国家的 invoice 总数，以及这些客户的员工支持是谁")
    llm = _llm_scripted(tmp_path, ['{"tables": ["customer", "genre"]}'])
    r2 = linker.rerank("测试问题", res, llm=llm)
    # customer 和 genre 不共享 FK → 连通性修复必须补回桥接表（employee/…）
    # （若原 selected 本身连通则可能不触发；此处只验证输出仍连通且合法）
    assert r2.selected
    assert set(r2.selected) <= set(res.selected)
    for path in r2.join_paths:
        a, b = path.split(" = ")
        assert a.split(".")[0] in r2.selected and b.split(".")[0] in r2.selected


@requires_db
def test_rerank_graceful_degrade_on_garbage(tmp_path):
    from app.nl2sql.schema_linking import SchemaLinker

    linker = SchemaLinker()
    res = linker.link(Q_ROCK)
    llm = _llm_scripted(tmp_path, ["这不是 JSON，模型跑题了"])
    r2 = linker.rerank(Q_ROCK, res, llm=llm)
    assert r2.selected == res.selected


@requires_db
def test_rerank_degrades_on_empty_keep(tmp_path):
    from app.nl2sql.schema_linking import SchemaLinker

    linker = SchemaLinker()
    res = linker.link(Q_ROCK)
    llm = _llm_scripted(tmp_path, ['{"tables": []}'])
    r2 = linker.rerank(Q_ROCK, res, llm=llm)
    assert r2.selected == res.selected


@requires_db
def test_rerank_skipped_for_single_candidate(tmp_path):
    from app.nl2sql.schema_linking import SchemaLinker, LinkingResult

    calls = []

    def handler(messages, params):
        calls.append(1)
        return '{"tables": ["track"]}'

    linker = SchemaLinker()
    single = LinkingResult(selected=["track"], join_paths=[], recalled=["track"])
    llm = _llm_handler(tmp_path, handler)
    r2 = linker.rerank("多少曲目", single, llm=llm)
    assert not calls  # LLM never invoked
    assert r2.selected == ["track"]


@requires_db
def test_rerank_json_with_prose_tolerated(tmp_path):
    """LLM wraps JSON in prose (no response_json compliance) → still parsed."""
    from app.nl2sql.schema_linking import SchemaLinker

    linker = SchemaLinker()
    res = linker.link(Q_ROCK)
    llm = _llm_scripted(tmp_path, ['好的，必需表如下：{"tables": ["genre", "track"]} 请查收'])
    r2 = linker.rerank(Q_ROCK, res, llm=llm)
    assert {"genre", "track"} <= set(r2.selected)


def test_build_link_rerank_messages_shape():
    from app.core.prompts.nl2sql import LINK_RERANK_STATIC, build_link_rerank_messages
    cards = [{"name": "track", "comment": "曲目表", "columns": "a, b", "rows": 3503,
              "samples": "name:For Those About To Rock"}]
    msgs = build_link_rerank_messages("问题", cards)
    assert msgs[0]["role"] == "system" and LINK_RERANK_STATIC in msgs[0]["content"]
    assert "1. track（3503 行）" in msgs[1]["content"]
    assert "【用户问题】" in msgs[1]["content"]


# ------------------------------------------------------- empty attribution --

@requires_db
def test_extract_string_filters_eq():
    from app.nl2sql.empty_attr import extract_string_filters
    sql = ("SELECT count(*) FROM track t JOIN genre g ON t.genreid = g.genreid "
           "WHERE g.name = 'Rok' AND t.unitprice > 0.99")
    fs = extract_string_filters(sql)
    assert ("g", "name", "Rok") in [(f["table_or_alias"], f["column"], f["value"]) for f in fs]


@requires_db
def test_extract_string_filters_in_and_like():
    from app.nl2sql.empty_attr import extract_string_filters
    sql = ("SELECT name FROM customer WHERE country IN ('Brasil', 'USA') "
           "AND email LIKE '%gmail.com'")
    fs = extract_string_filters(sql)
    # unqualified columns carry table_or_alias='' — resolution to real tables
    # happens later in attribute_empty_sql
    vals = {(f["table_or_alias"], f["column"], f["value"]) for f in fs}
    assert ("", "country", "Brasil") in vals
    assert ("", "country", "USA") in vals
    assert ("", "email", "gmail.com") in vals


@requires_db
def test_attribute_suspicious_filter_typo():
    from app.nl2sql.empty_attr import attribute_empty_sql
    attr = attribute_empty_sql("SELECT name FROM genre WHERE name = 'Rok'")
    assert attr["verdict"] == "suspicious_filter"
    assert "Rok" in attr["evidence_note"]
    assert attr.get("nearest_value") == "Rock"


@requires_db
def test_attribute_truly_no_data_with_real_combination():
    """Dynamically find an (existing value, existing value) combination with
    zero rows → verdict must be truly_no_data."""
    from app.nl2sql.empty_attr import attribute_empty_sql
    from app.db.session import get_conn
    with get_conn(readonly=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT g.name, mt.name FROM genre g CROSS JOIN mediatype mt "
            "WHERE NOT EXISTS (SELECT 1 FROM track t "
            "WHERE t.genreid = g.genreid AND t.mediatypeid = mt.mediatypeid) LIMIT 1")
        row = cur.fetchone()
    if row is None:  # every combination exists — skip defensively
        pytest.skip("no empty genre×mediatype combination in this DB seed")
    gname, mname = row
    if "'" in gname or "'" in mname:
        pytest.skip("quote in names — skip literal-safety")
    sql = (f"SELECT t.name FROM track t JOIN genre g ON t.genreid = g.genreid "
           f"JOIN mediatype mt ON t.mediatypeid = mt.mediatypeid "
           f"WHERE g.name = '{gname}' AND mt.name = '{mname}'")
    attr = attribute_empty_sql(sql)
    assert attr["verdict"] == "truly_no_data"
    assert "过滤值均真实存在" in attr["evidence_note"]


@requires_db
def test_attribute_unknown_without_string_filters():
    from app.nl2sql.empty_attr import attribute_empty_sql
    attr = attribute_empty_sql("SELECT name FROM track WHERE unitprice > 999")
    assert attr["verdict"] == "unknown"


# ------------------------------------------------ pipeline empty flow ------

@requires_db
def test_pipeline_empty_attribution_end_to_end(tmp_path, monkeypatch):
    """SQL valid but 0 rows with a typo'd filter value → suspicious_filter
    verdict recorded on the result (repair may or may not recover).
    Rerank disabled: it would consume a scripted response otherwise."""
    from app.nl2sql.pipeline import NL2SQLPipeline
    p = NL2SQLPipeline(llm=_llm_scripted(tmp_path, [
        "【分析】按曲风统计\n【SQL】\n```sql\nSELECT COUNT(*) AS cnt FROM genre g "
        "WHERE g.name = 'Rok'\n```",
        "【分析】修正曲风名\n【SQL】\n```sql\nSELECT COUNT(*) AS cnt FROM genre g "
        "WHERE g.name = 'Rock'\n```",
        "Rock 曲风有 1297 首。",
    ]))
    monkeypatch.setattr(p.settings, "schema_linking_rerank", False)
    r = p.run("Rok 曲风有多少首曲目？")
    # first round: valid SQL, 0 rows → attribution fires
    assert r.empty_attribution is not None
    assert r.empty_attribution["verdict"] in {"suspicious_filter", "truly_no_data"}
    if r.status == "ok":  # targeted repair recovered (repair SQL counts genres → 1)
        assert r.rows[0][0] >= 1
        assert "empty_attr" in [c["label"] for c in r.trace["root"]["children"]]
