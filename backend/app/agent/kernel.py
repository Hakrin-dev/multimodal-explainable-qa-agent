"""Agent orchestration kernel v1 (PLAN §4.1, D3-A).

Event-driven turn execution with full Trace:

    question → intent → route →
        CHAT      → direct generate
        DB_QUERY  → nl2sql tool
        DOC_QUERY → rag_search tool
        HYBRID    → plan → sub-task chain → fuse
        AMBIGUOUS → clarify (suspend; history-driven resume next turn)
    → turn end (cost attributed)

Observers receive contract events (trace.md §4.2): turn.start / trace.node /
answer.delta / answer.done / clarify.request / turn.end / error.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.llm import LLMService, get_llm_service
from ..core.tracing import NodeStatus, NodeType, TraceCollector
from .intent import IntentClassifier, IntentResult
from .memory import SessionStore, get_session_store
from .planner import Planner, build_fuse_messages
from .rewrite import CoreferenceRewriter
from .slots import SlotMatrix
from .tools import ToolRegistry, ToolResult, default_registry

EventCallback = Callable[[str, dict], None]

CHAT_SYSTEM = "你是 Chinook 唱片公司的智能助理，用中文简洁回答（1~3 句）。"


@dataclass
class TurnResult:
    question: str
    answer: str = ""
    intent: str = ""
    status: str = "ok"            # ok | clarify | degraded | error
    rewritten: str = ""           # 指代消解后的自包含问题（多轮改写对照，前端展示）
    data: dict[str, Any] = field(default_factory=dict)     # tool payloads
    citations: list[dict] = field(default_factory=list)
    clarify: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    cost_rmb: float = 0.0
    latency_ms: int = 0


class AgentKernel:
    CLARIFY_TTL_SECONDS = 600   # stale clarifications don't shadow fresh turns
    def __init__(self, llm: LLMService | None = None,
                 registry: ToolRegistry | None = None,
                 sessions: SessionStore | None = None,
                 slot_matrix: SlotMatrix | None = None):
        self.llm = llm or get_llm_service()
        self.registry = registry or default_registry()
        self.sessions = sessions or get_session_store()
        self.classifier = IntentClassifier(self.llm)
        self.planner = Planner(self.llm)
        self.rewriter = CoreferenceRewriter(self.llm)
        self.slots = slot_matrix if slot_matrix is not None else SlotMatrix.load()

    # ------------------------------------------------------------------ api

    def run(self, question: str, session_id: str = "default",
            on_event: EventCallback | None = None) -> TurnResult:
        t0 = time.monotonic()
        trace = TraceCollector(question=question)
        emit = on_event or (lambda e, d: None)

        session = self.sessions.get(session_id)
        history = session.messages[-6:]
        pending = session.pending_clarify
        # clarify TTL: stale suspensions don't shadow fresh questions
        if pending and isinstance(pending, dict) and pending.get("_ts"):
            if time.time() - pending["_ts"] > self.CLARIFY_TTL_SECONDS:
                pending = None
                self.sessions.set_pending_clarify(session_id, None)

        emit("turn.start", {"turn_id": trace.turn_id, "question": question,
                             "session_id": session_id,
                             "resumed_clarify": bool(pending)})

        # ⓪ coreference rewrite (§4.6): follow-up → standalone question
        rw = self.rewriter.rewrite(question, history)
        if rw.changed:
            with trace.span("rewrite", NodeType.STEP, input=question) as node:
                trace.finish(node, output=rw.question,
                             detail={"rewrites": rw.rewrites, "method": "coreference"})
            emit("trace.node", _node_event(trace, node))
        effective_question = rw.question

        # ① intent (on the standalone question; history still available)
        with trace.span("intent", NodeType.INTENT, input=effective_question) as node:
            ir: IntentResult = self.classifier.classify(question, history)
            trace.finish(node, output={"intent": ir.intent,
                                       "confidence": ir.confidence,
                                       "fallback": ir.fallback_used},
                         detail={"slots": ir.slots,
                                 "missing_slots": ir.missing_slots})
        emit("trace.node", _node_event(trace, node))

        # ①' slot matrix (§4.7, deterministic): pattern rules may override an
        # over-confident DB_QUERY into clarify, or enrich clarify options
        verdict = self.slots.check(effective_question)
        if verdict.triggered and verdict.missing:
            if ir.intent in ("DB_QUERY", "HYBRID") and not ir.needs_clarification:
                ir = IntentResult(intent="AMBIGUOUS", confidence=0.9,
                                  missing_slots=verdict.missing,
                                  options=verdict.options,
                                  raw={"source": "slot_matrix", "hint": verdict.intent_hint})
                with trace.span("slot_check", NodeType.STEP,
                                input=effective_question) as snode:
                    trace.finish(snode, status=NodeStatus.OK, output={
                        "override": "DB_QUERY->AMBIGUOUS",
                        "missing_slots": verdict.missing},
                        detail={"rule": verdict.intent_hint})
                emit("trace.node", _node_event(trace, snode))
            elif ir.needs_clarification:
                for slot, opts in verdict.options.items():
                    if opts and not ir.options.get(slot):
                        ir.options[slot] = opts

        # ② route (downstream gets the standalone question)
        if ir.needs_clarification:
            result = self._clarify(trace, emit, effective_question, ir)
        elif ir.intent == "CHAT":
            result = self._chat(trace, emit, effective_question, history)
        elif ir.intent == "DB_QUERY":
            result = self._tool_turn(trace, emit, effective_question, "nl2sql", history)
        elif ir.intent == "DOC_QUERY":
            result = self._tool_turn(trace, emit, effective_question, "rag_search", history)
        elif ir.intent == "HYBRID":
            result = self._hybrid(trace, emit, effective_question)
        else:  # unknown — honest failure
            result = TurnResult(question=question, status="error",
                                answer="未能识别该请求的意图。")
            result.intent = ir.intent

        result.intent = ir.intent
        result.rewritten = effective_question if rw.changed else ""
        result.trace = trace.to_dict()
        result.latency_ms = int((time.monotonic() - t0) * 1000)
        result.cost_rmb = _turn_cost(trace)

        # durability: trace tree -> PG (best-effort; enables /api/trace/{id})
        if self.sessions.persist:
            from . import persistence
            persistence.save_turn(trace, session_id, summary={
                "intent": result.intent, "status": result.status,
                "latency_ms": result.latency_ms, "cost_rmb": result.cost_rmb,
                "answer": result.answer[:300]})

        # memory bookkeeping: suspend or clear clarify state, then log the turn
        if result.status == "clarify" and self._pending_store:
            self._pending_store["_ts"] = time.time()   # TTL anchor (§4.7)
            self.sessions.set_pending_clarify(session_id, self._pending_store)
            self._pending_store = None
        elif result.status != "clarify":
            self.sessions.set_pending_clarify(session_id, None)
        self.sessions.append(session_id, "user", question)
        self.sessions.append(session_id, "assistant", result.answer[:500])

        emit("turn.end", {"latency_ms": result.latency_ms, "cost_rmb": result.cost_rmb,
                          "status": result.status})
        return result

    # ------------------------------------------------------------- branches

    def _chat(self, trace, emit, question, history) -> TurnResult:
        messages = ([{"role": "system", "content": CHAT_SYSTEM}] + history[-4:]
                    + [{"role": "user", "content": question}])
        with trace.span("chat_generate", NodeType.STEP) as node:
            resp = None
            for chunk in self.llm.chat_stream(messages, temperature=0.5,
                                              max_tokens=300, purpose="agent.chat"):
                if chunk.delta:
                    emit("answer.delta", {"text": chunk.delta})
                if chunk.response is not None:
                    resp = chunk.response
            _llm_child(trace, node, resp)
            trace.finish(node, output=(resp.content if resp else "")[:200])
        emit("trace.node", _node_event(trace, node))
        return TurnResult(question=question,
                          answer=resp.content if resp else "")

    def _tool_turn(self, trace, emit, question, tool_name,
                   history: list[dict] | None = None) -> TurnResult:
        spec = self.registry.get(tool_name)
        with trace.span(tool_name, NodeType.TOOL_CALL, input=question) as node:
            tr: ToolResult = spec.handler(question=question, trace=trace,
                                          history=history, parent=node)
            if node.status == NodeStatus.PENDING:   # tool may have set it
                node.status = NodeStatus.OK if tr.ok else NodeStatus.DEGRADED
            node.output = (("ok" if tr.ok else tr.degraded_reason))[:200]
            node.detail["degraded"] = not tr.ok
        emit("trace.node", _node_event(trace, node))

        if not tr.ok:
            emit("answer.delta", {"text": tr.degraded_reason})
            return TurnResult(question=question, status="degraded",
                              answer=_degraded_answer(tool_name, tr),
                              data={"degraded_reason": tr.degraded_reason})

        # pipeline declined (missing info) — surface honestly, not as data
        if tr.data.get("status") == "needs_clarification":
            missing = tr.data.get("missing") or []
            answer = ("该查询缺少必要信息，无法生成结果："
                      + (missing[0] if missing else "请补充查询条件"))
            emit("answer.delta", {"text": answer})
            return TurnResult(question=question, status="degraded", answer=answer,
                              data={"status": "needs_clarification"})

        answer = tr.data.get("summary") or tr.data.get("answer") or ""
        citations = tr.data.get("citations", [])
        if tool_name == "nl2sql" and not answer:
            answer = _fallback_summary(tr.data)
        emit("answer.delta", {"text": answer})
        return TurnResult(question=question, answer=answer,
                          data={k: v for k, v in tr.data.items() if k != "summary"},
                          citations=citations)

    def _hybrid(self, trace, emit, question) -> TurnResult:
        """DAG execution (v1.2): topological waves — independent sub-tasks run
        concurrently (ThreadPoolExecutor), dependents wait on {tN.result}."""
        with trace.span("plan", NodeType.PLAN, input=question) as node:
            tasks = self.planner.plan(question, self.registry.inventory)
            edges = [{"from": d, "to": t["id"]}
                     for t in tasks for d in t["depends_on"]]
            trace.finish(node, output={"n_tasks": len(tasks), "n_edges": len(edges)},
                         detail={"sub_tasks": tasks, "edges": edges})
        emit("trace.node", _node_event(trace, node))

        if not tasks:
            answer = "该问题需要跨源检索，但任务规划失败，请换一种问法。"
            emit("answer.delta", {"text": answer})
            return TurnResult(question=question, status="degraded", answer=answer)

        results: dict[str, dict] = {}     # task_id -> {"tool","question","result"}
        remaining = {t["id"]: t for t in tasks}
        order = {t["id"]: i for i, t in enumerate(tasks, 1)}

        def substitute(q: str) -> str:
            def _ref(sr: dict) -> str:
                # compact entity-first reference (full summaries dilute retrieval)
                r = sr["result"]
                return str(r.get("key") or r.get("summary") or r.get("answer")
                           or r.get("error", ""))[:120]
            for tid, sr in results.items():
                q = q.replace(f"{{{tid}.result}}", _ref(sr))
            # legacy placeholder binds to the latest completed task
            if "{上一步结果}" in q and results:
                latest = max(results, key=lambda t: order.get(t, 0))
                q = q.replace("{上一步结果}", _ref(results[latest]))
            return q

        def run_task(task: dict) -> None:
            q = substitute(task["question"])
            spec = self.registry.get(task["tool"])
            label = f"subtask_{order[task['id']]}:{task['tool']}"
            with trace.span(label, NodeType.TOOL_CALL, input=q) as node:
                tr = spec.handler(question=q, trace=trace, parent=node)
                trace.finish(node,
                             status=NodeStatus.OK if tr.ok else NodeStatus.DEGRADED,
                             output=None,
                             detail={"degraded": not tr.ok, "task_id": task["id"]})
            emit("trace.node", _node_event(trace, node))
            results[task["id"]] = {
                "tool": task["tool"], "question": q,
                "result": tr.data if tr.ok else {"error": tr.degraded_reason},
            }

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:
            while remaining:
                # wave = tasks whose deps are all satisfied
                wave = [t for tid, t in remaining.items()
                        if all(d in results for d in t["depends_on"])]
                if not wave:   # cycle / broken deps — run rest sequentially
                    wave = list(remaining.values())
                futures = [pool.submit(run_task, t) for t in wave]
                for t in wave:
                    remaining.pop(t["id"])
                for f in futures:
                    f.result()
        sub_results = [results[t["id"]] for t in tasks]   # plan order preserved

        with trace.span("fuse", NodeType.FUSE) as node:
            messages = build_fuse_messages(question, sub_results)
            resp = None
            for chunk in self.llm.chat_stream(messages, temperature=0.3,
                                              max_tokens=500, purpose="agent.fuse"):
                if chunk.delta:
                    emit("answer.delta", {"text": chunk.delta})
                if chunk.response is not None:
                    resp = chunk.response
            _llm_child(trace, node, resp)
            trace.finish(node, output=(resp.content if resp else "")[:300])
        emit("trace.node", _node_event(trace, node))

        citations = [c for sr in sub_results
                     for c in (sr["result"].get("citations") or [])]
        return TurnResult(question=question,
                          answer=resp.content if resp else "",
                          data={"sub_results": [
                              {"tool": sr["tool"], "question": sr["question"]}
                              for sr in sub_results]},
                          citations=citations)

    def _clarify(self, trace, emit, question, ir: IntentResult) -> TurnResult:
        payload = {"missing_slots": ir.missing_slots, "options": ir.options,
                   "question": question}
        with trace.span("clarify", NodeType.CLARIFY, input=question) as node:
            trace.finish(node, output=payload, detail=payload)
        emit("trace.node", _node_event(trace, node))
        emit("clarify.request", payload)

        # suspend: store pending state; next turn the history drives resume
        self._pending_store = payload
        answer = _clarify_question(ir)
        emit("answer.delta", {"text": answer})
        return TurnResult(question=question, status="clarify", answer=answer,
                          clarify=payload)

    # clarify payload hand-off (run() applies it to the session after _clarify)
    _pending_store: dict | None = None


# ----------------------------------------------------------------- helpers --

def _llm_child(trace, parent, resp) -> None:
    with trace.span("llm", NodeType.LLM_CALL, parent=parent) as node:
        trace.finish(node, output=None, detail={
            "model": resp.model, "cost_rmb": resp.cost_rmb,
            "tokens": {"prompt": resp.usage.prompt_tokens,
                       "completion": resp.usage.completion_tokens}})


def _node_event(trace, node) -> dict:
    return node.to_dict(recursive=False)


def _turn_cost(trace) -> float:
    return round(sum(n.detail.get("cost_rmb", 0) for n in _walk_nodes(trace.root)
                     if n.type == NodeType.LLM_CALL), 6)


def _walk_nodes(node):
    yield node
    for c in node.children:
        yield from _walk_nodes(c)


def _degraded_answer(tool_name: str, tr: ToolResult) -> str:
    if tool_name == "rag_search":
        return ("知识库检索暂不可用：" + tr.degraded_reason +
                "。数据库相关问题我仍可回答。")
    return f"该查询暂不可用：{tr.degraded_reason}"


def _fallback_summary(data: dict) -> str:
    cols, rows = data.get("columns", []), data.get("rows", [])
    if not rows:
        return "查询未返回数据。"
    head = "、".join(str(v) for v in rows[0][:3])
    return f"查询返回 {data.get('row_count', len(rows))} 行，首行：{head}。"


def _clarify_question(ir: IntentResult) -> str:
    slots = "、".join(ir.missing_slots)
    q = f"这个问题还需要补充信息：{slots}。"
    if ir.options:
        first = next(iter(ir.options.values()))
        if isinstance(first, list) and first:
            q += "可选：" + " / ".join(str(x) for x in first[:5])
    return q
