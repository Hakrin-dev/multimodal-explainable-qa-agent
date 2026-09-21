"""Agent kernel tests: routing, HYBRID planning, clarify suspend/resume,
degradation, event contract — all with stub tools + scripted mock LLM."""

from __future__ import annotations

import json

import pytest

from app.agent.kernel import AgentKernel, TurnResult
from app.agent.memory import SessionStore
from app.agent.tools import ToolRegistry, ToolResult, ToolSpec
from app.core.config import Settings
from app.core.llm import LLMService, MockProvider, _LLMStore


def _llm(tmp_path, scripted):
    svc = LLMService(Settings(llm_provider="mock"), _LLMStore(tmp_path / "u.sqlite"))
    svc.register_mock(MockProvider(scripted=scripted))
    return svc


def _intent(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _stub_registry(nl2sql_result=None, rag_result=None) -> ToolRegistry:
    reg = ToolRegistry()

    def nl2sql(question, trace, **_):
        r = nl2sql_result or ToolResult(ok=True, data={
            "sql": "SELECT 1", "columns": ["n"], "rows": [[42]], "row_count": 1,
            "summary": "共 42 条。", "status": "ok"})
        if isinstance(r, Exception):
            raise r
        return r

    def rag(question, trace, **_):
        r = rag_result or ToolResult(ok=True, data={
            "answer": "年假 15 天[1]。", "citations": [{"doc": "员工手册", "page": 2}]})
        if isinstance(r, Exception):
            raise r
        return r

    reg.register(ToolSpec("nl2sql", "stub nl2sql", nl2ql := nl2sql))
    reg.register(ToolSpec("rag_search", "stub rag", rag))
    reg.register(ToolSpec("db_lookup_entity", "stub lookup",
                          lambda term, **_: ToolResult(ok=True, data={})))
    reg.register(ToolSpec("formula_eval", "stub formula",
                          lambda **_: ToolResult(ok=False, degraded_reason="W4")))
    return reg


def _kernel(tmp_path, scripted, **tool_kwargs) -> AgentKernel:
    return AgentKernel(llm=_llm(tmp_path, scripted),
                       registry=_stub_registry(**tool_kwargs),
                       sessions=SessionStore())


# ---------------------------------------------------------------- routing --

def test_chat_flow(tmp_path):
    k = _kernel(tmp_path, [
        _intent({"intent": "CHAT", "confidence": 0.9}),
        "你好！我是智能助理。",
    ])
    events = []
    r = k.run("你好", on_event=lambda e, d: events.append(e))
    assert r.status == "ok" and r.intent == "CHAT"
    assert "智能助理" in r.answer
    assert events[0] == "turn.start" and events[-1] == "turn.end"
    assert "answer.delta" in events


def test_db_query_flow(tmp_path):
    k = _kernel(tmp_path, [
        _intent({"intent": "DB_QUERY", "confidence": 0.95}),
    ])
    r = k.run("销量前十的曲目")
    assert r.status == "ok" and r.intent == "DB_QUERY"
    assert r.data["sql"] == "SELECT 1" and "42" in r.answer
    labels = [c["label"] for c in r.trace["root"]["children"]]
    assert labels == ["intent", "nl2sql"]


def test_doc_query_flow_with_citations(tmp_path):
    k = _kernel(tmp_path, [
        _intent({"intent": "DOC_QUERY", "confidence": 0.9}),
    ])
    r = k.run("年假有几天")
    assert r.status == "ok"
    assert r.citations and r.citations[0]["doc"] == "员工手册"
    assert [c["label"] for c in r.trace["root"]["children"]] == ["intent", "rag_search"]


def test_degraded_tool_honest_answer(tmp_path):
    k = _kernel(tmp_path, [
        _intent({"intent": "DOC_QUERY", "confidence": 0.9}),
    ], rag_result=ToolResult(ok=False, degraded_reason="知识库未就绪（嵌入模型未部署）"))
    r = k.run("年假有几天")
    assert r.status == "degraded"
    assert "不可用" in r.answer and "编造" not in r.answer
    node = [c for c in r.trace["root"]["children"] if c["label"] == "rag_search"][0]
    assert node["status"] == "degraded"


# ----------------------------------------------------------------- hybrid --

def test_hybrid_flow(tmp_path):
    k = _kernel(tmp_path, [
        _intent({"intent": "HYBRID", "confidence": 0.85}),
        _intent({"sub_tasks": [
            {"tool": "nl2sql", "question": "2025 年销售冠军是谁"},
            {"tool": "rag_search", "question": "{上一步结果} 的工作方法论"},
        ]}),
        "2025 年销售冠军是 Jane Peacock。方法论：复购驱动[2025 销售团队年度总结]。",
    ])
    r = k.run("2025 年销售冠军是谁？总结他的方法论")
    assert r.status == "ok"
    labels = [c["label"] for c in r.trace["root"]["children"]]
    assert labels == ["intent", "plan", "subtask_1:nl2sql", "subtask_2:rag_search", "fuse"]
    # {上一步结果} was substituted with the previous tool's summary
    sub_q2 = [c for c in r.trace["root"]["children"]
              if c["label"] == "subtask_2:rag_search"][0]["input"]
    assert "42" in sub_q2  # stub nl2sql summary contains 42
    assert "方法论" in r.answer


def test_hybrid_plan_failure_degrades(tmp_path):
    k = _kernel(tmp_path, [
        _intent({"intent": "HYBRID", "confidence": 0.85}),
        "规划失败：无法分解",  # planner returns non-JSON
    ])
    r = k.run("跨源问题")
    assert r.status == "degraded" and "规划失败" in r.answer


# ---------------------------------------------------------------- clarify --

def test_clarify_suspend_and_resume(tmp_path):
    k = _kernel(tmp_path, [
        # turn 1: ambiguous
        _intent({"intent": "AMBIGUOUS", "confidence": 0.8,
                 "missing_slots": ["对比时间范围"],
                 "options": {"对比时间范围": ["2024 vs 2023", "2025 vs 2024"]}}),
        # turn 2: history-driven resolution
        _intent({"intent": "DB_QUERY", "confidence": 0.95}),
    ])
    events = []
    r1 = k.run("销售额增长率是多少", session_id="s1",
               on_event=lambda e, d: events.append((e, d)))
    assert r1.status == "clarify"
    assert "clarify.request" in [e for e, _ in events]
    payload = [d for e, d in events if e == "clarify.request"][0]
    assert payload["missing_slots"] == ["对比时间范围"]

    # suspended state stored
    sess = k.sessions.get("s1")
    assert sess.pending_clarify is not None

    # turn 2: user answers the clarification → full intent → executes
    r2 = k.run("对比 2024 和 2023", session_id="s1")
    assert r2.status == "ok" and r2.intent == "DB_QUERY"
    assert k.sessions.get("s1").pending_clarify is None
    # clarify exchange is in history for future turns
    hist = k.sessions.history("s1")
    assert any("增长率" in m["content"] for m in hist)


# ------------------------------------------------------- intent robustness --

def test_intent_json_repair_then_fallback(tmp_path):
    # first two responses invalid, third valid JSON via repair; then a fully
    # broken case falls back to keyword heuristic
    k = _kernel(tmp_path, [
        "不是JSON", _intent({"intent": "DB_QUERY"}),
    ])
    ir = k.classifier.classify("销量前十的曲目")
    assert ir.intent == "DB_QUERY" and not ir.fallback_used

    k2 = _kernel(tmp_path, ["nope", "still not json"])
    ir2 = k2.classifier.classify("员工手册里的年假规定是什么")
    assert ir2.fallback_used and ir2.intent == "DOC_QUERY"


def test_event_contract_shapes(tmp_path):
    k = _kernel(tmp_path, [
        _intent({"intent": "CHAT", "confidence": 0.9}),
        "好的。",
    ])
    events: list[tuple[str, dict]] = []
    k.run("你好", on_event=lambda e, d: events.append((e, d)))
    kinds = [e for e, _ in events]
    # contract (trace.md §4.2): turn.start ... trace.node ... answer.delta turn.end
    assert kinds[0] == "turn.start" and kinds[-1] == "turn.end"
    assert "trace.node" in kinds and "answer.delta" in kinds
    # every trace.node payload is a flat TraceNode dict with contract keys
    node = [d for e, d in events if e == "trace.node"][0]
    assert {"id", "type", "label", "status"} <= set(node)


def test_intent_heuristic_keywords():
    from app.agent.intent import _heuristic
    assert _heuristic("员工手册里的年假规定是什么") == "DOC_QUERY"
    assert _heuristic("2025 年销售冠军是谁？总结他的方法论") == "HYBRID"
    assert _heuristic("销量前十的曲目") == "DB_QUERY"
    assert _heuristic("你好呀") == "CHAT"
