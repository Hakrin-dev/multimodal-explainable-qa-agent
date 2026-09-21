"""FastAPI entrypoint — W1 scope: health / nl2sql minimal loop / schema / usage.

W2 will add the agent orchestration endpoints (/api/chat SSE, /api/trace).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .core.config import get_settings
from .core.llm import get_llm_service
from .db import schema_meta
from .db.session import test_connection
from .nl2sql.pipeline import NL2SQLPipeline, NL2SQLResult

app = FastAPI(title="Multimodal Explainable QA Agent", version="0.1.0")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[dict] | None = None


class RAGRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    doc_filter: str | None = None


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str = "default"


@app.get("/api/health")
def health() -> dict[str, Any]:
    s = get_settings()
    return {
        "status": "ok",
        "db": test_connection(),
        "llm_provider": s.llm_provider,
        "llm_model": s.active_model,
    }


@app.post("/api/nl2sql")
def nl2sql_query(req: QueryRequest) -> dict[str, Any]:
    pipeline = NL2SQLPipeline()
    result: NL2SQLResult = pipeline.run(req.question, history=req.history)
    return _result_dict(result)


@app.get("/api/schema")
def get_schema() -> dict[str, Any]:
    tables = schema_meta.load_table_meta()
    return {"tables": [
        {"name": t.name, "row_count": t.row_count,
         "columns": [{"name": c.name, "type": c.type, "nullable": c.nullable}
                     for c in t.columns]}
        for t in tables
    ]}


@app.get("/api/terms")
def get_terms() -> dict[str, Any]:
    from .nl2sql import rewriter
    return {"terms": rewriter.load_terms()}


@app.post("/api/rag")
def rag_query(req: RAGRequest) -> dict[str, Any]:
    from .rag.pipeline import RAGPipeline
    pipeline = RAGPipeline()
    r = pipeline.run(req.question)
    return {
        "question": r.question, "answer": r.answer, "status": r.status,
        "citations": r.citations, "latency_ms": r.latency_ms, "trace": r.trace,
    }


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    """Orchestrated turn (JSON form). SSE form: POST /api/chat/stream."""
    from .agent.kernel import AgentKernel
    r = AgentKernel().run(req.question, session_id=req.session_id)
    return _turn_dict(r)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE form per trace.md §4.2: turn.start / trace.node / answer.delta /
    answer.done / clarify.request / turn.end / error."""
    import json as _json
    from fastapi.responses import StreamingResponse
    from .agent.kernel import AgentKernel

    def gen():
        events: list[tuple[str, dict]] = []

        def on_event(event: str, data: dict) -> None:
            events.append((event, data))

        try:
            result = AgentKernel().run(req.question, session_id=req.session_id,
                                       on_event=on_event)
        except Exception as e:  # noqa: BLE001
            events.append(("error", {"message": str(e)[:300]}))
            result = None

        for event, data in events:
            if event in ("answer.delta", "turn.end"):
                continue  # answer payload emitted below; turn.end must close the stream
            yield f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        if result is not None:
            yield ("event: answer.delta\ndata: " +
                   _json.dumps({"text": result.answer}, ensure_ascii=False) + "\n\n")
            yield ("event: answer.done\ndata: " +
                   _json.dumps(_turn_dict(result), ensure_ascii=False, default=str) + "\n\n")
        # contract order (trace.md §4.2): ... answer.done → turn.end
        turn_end = next((d for e, d in events if e == "turn.end"), {})
        yield ("event: turn.end\ndata: " +
               _json.dumps(turn_end, ensure_ascii=False) + "\n\n")

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/usage")
def usage() -> dict[str, Any]:
    return get_llm_service().usage_summary()


def _result_dict(r: NL2SQLResult) -> dict[str, Any]:
    return {
        "question": r.question,
        "rewritten": r.rewritten,
        "analysis": r.analysis,
        "sql": r.sql,
        "columns": r.columns,
        "rows": r.rows,
        "row_count": r.row_count,
        "truncated": r.truncated,
        "summary": r.summary,
        "chart_hint": r.chart_hint,
        "status": r.status,
        "errors": r.errors,
        "repair_rounds": r.repair_rounds,
        "latency_ms": r.latency_ms,
        "trace": r.trace,
    }


def _turn_dict(r) -> dict[str, Any]:
    return {
        "question": r.question,
        "answer": r.answer,
        "intent": r.intent,
        "status": r.status,
        "data": r.data,
        "citations": r.citations,
        "clarify": r.clarify,
        "cost_rmb": r.cost_rmb,
        "latency_ms": r.latency_ms,
        "trace": r.trace,
    }
