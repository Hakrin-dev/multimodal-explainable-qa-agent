"""Unit tests for Trace collector & contract shape."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.tracing import NodeStatus, NodeType, TraceCollector

CONTRACT = Path(__file__).resolve().parents[2] / "docs/contracts/trace_schema.json"


def test_tree_shape_and_timing():
    tc = TraceCollector(question="q")
    with tc.span("intent", NodeType.INTENT) as n:
        tc.finish(n, output={"intent": "DB_QUERY"})
    with tc.span("sql_gen", NodeType.STEP) as gen:
        with tc.span("llm", NodeType.LLM_CALL, parent=gen) as llm:
            tc.finish(llm, detail={"model": "mock", "cost_rmb": 0.0})
    d = tc.to_dict()
    root = d["root"]
    assert root["type"] == "turn" and len(root["children"]) == 2
    gen_child = root["children"][1]
    assert gen_child["children"][0]["type"] == "llm_call"
    assert gen_child["latency_ms"] >= gen_child["children"][0]["latency_ms"]
    assert d["trace_version"] == "0.1"


def test_error_status_recorded():
    tc = TraceCollector(question="q")
    try:
        with tc.span("boom", NodeType.STEP):
            raise ValueError("db down")
    except ValueError:
        pass
    flat = tc.flat_events()
    node = [n for n in flat if n["label"] == "boom"][0]
    assert node["status"] == "error" and "db down" in node["output"]


def test_conforms_to_contract_enums():
    """Runtime model must stay in sync with the frozen JSON Schema contract."""
    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
    types = set(schema["$defs"]["TraceNode"]["properties"]["type"]["enum"])
    statuses = set(schema["$defs"]["TraceNode"]["properties"]["status"]["enum"])
    assert {t.value for t in NodeType} == types
    assert {s.value for s in NodeStatus} == statuses
