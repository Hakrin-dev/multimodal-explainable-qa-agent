"""Persistence integration tests (live PG): trace replay + session durability."""

from __future__ import annotations

import pytest

from app.core.tracing import NodeType, TraceCollector
from app.db.session import test_connection

requires_db = pytest.mark.skipif(
    not test_connection(), reason="live PostgreSQL required (run quick_start.sh)"
)


def _sample_trace() -> TraceCollector:
    tc = TraceCollector(question="测试问题")
    with tc.span("intent", NodeType.INTENT) as n:
        tc.finish(n, output={"intent": "DB_QUERY"})
    with tc.span("nl2sql", NodeType.TOOL_CALL) as tool:
        with tc.span("sql_gen", NodeType.STEP, parent=tool) as gen:
            tc.finish(gen, detail={"sql": "SELECT 1"})
        tc.finish(tool, output="ok")
    return tc


@requires_db
def test_turn_roundtrip():
    from app.agent import persistence
    tc = _sample_trace()
    ok = persistence.save_turn(tc, session_id="persistence-test",
                               summary={"intent": "DB_QUERY", "status": "ok"})
    assert ok

    loaded = persistence.load_trace(tc.turn_id)
    assert loaded is not None
    assert loaded["turn_id"] == tc.turn_id
    assert loaded["question"] == "测试问题"
    root = loaded["root"]
    labels = [c["label"] for c in root["children"]]
    assert labels == ["intent", "nl2sql"]
    # nested structure survived the flat round-trip
    tool = root["children"][1]
    assert tool["children"][0]["label"] == "sql_gen"
    assert tool["children"][0]["detail"]["sql"] == "SELECT 1"

    turns = persistence.list_turns(session_id="persistence-test")
    assert any(t["turn_id"] == tc.turn_id for t in turns)


@requires_db
def test_load_trace_missing():
    from app.agent import persistence
    assert persistence.load_trace("no-such-turn-xyz") is None


@requires_db
def test_session_durability_across_instances():
    """SessionStore write-through: a NEW store instance (simulating process
    restart) must recover messages + pending clarify from PG."""
    from app.agent import persistence
    from app.agent.memory import SessionStore

    sid = "durability-test"
    s1 = SessionStore(persist=True)
    s1.append(sid, "user", "销售额增长率是多少")
    s1.set_pending_clarify(sid, {"missing_slots": ["时间范围"]})

    s2 = SessionStore(persist=True)   # fresh instance = process restart
    sess = s2.get(sid)
    assert any("增长率" in m["content"] for m in sess.messages)
    assert sess.pending_clarify == {"missing_slots": ["时间范围"]}

    # cleanup
    import psycopg
    from app.core.config import Settings
    with psycopg.connect(Settings().database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM app_session WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM trace_turn WHERE session_id = 'persistence-test'")
        cur.execute("DELETE FROM trace_event WHERE session_id = 'persistence-test'")
