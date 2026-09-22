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

import re

_DOC_ID_RE = re.compile(r"^[a-z0-9_-]+$", re.I)

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
    """SSE form per trace.md §4.2 — LIVE: the kernel runs in a worker thread,
    events are forwarded as they happen (real token streaming for CHAT/fuse)."""
    import json as _json
    import queue as _queue
    import threading as _threading

    import fastapi.responses as _fr
    from .agent.kernel import AgentKernel

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    q: _queue.Queue = _queue.Queue()

    def worker() -> None:
        try:
            r = AgentKernel().run(req.question, session_id=req.session_id,
                                   on_event=lambda e, d: q.put((e, d)))
            q.put(("__result__", r))
        except Exception as e:  # noqa: BLE001
            # contract trace.md §4.2 (C review): stable code + recoverable flag
            code = getattr(e, "code", None) or type(e).__name__
            q.put(("error", {"message": str(e)[:300], "code": code,
                             "recoverable": True}))
            q.put(("__result__", None))

    _threading.Thread(target=worker, daemon=True).start()

    def gen():
        result = None
        turn_end: dict | None = None
        while True:
            event, data = q.get()
            if event == "__result__":
                result = data
                break
            if event == "turn.end":
                turn_end = data          # contract: after answer.done
                continue
            yield sse(event, data)
        if result is not None:
            yield sse("answer.done", _turn_dict(result))
        if turn_end is not None:
            yield sse("turn.end", turn_end)

    return _fr.StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})


@app.get("/api/docs")
def list_docs() -> dict[str, Any]:
    """KB docs available for citation preview (front-end document picker)."""
    from pathlib import Path
    from .core.config import resolve_repo_path
    docs_dir = resolve_repo_path("data/docs_raw")
    if not docs_dir.is_dir():
        return {"docs": []}
    return {"docs": [
        {"doc_id": p.stem, "name": p.stem, "file": p.name, "size": p.stat().st_size}
        for p in sorted(docs_dir.glob("*.pdf"))
    ]}


@app.get("/api/docs/{doc_id}/pdf")
def get_doc_pdf(doc_id: str):
    """Serve a KB PDF for citation drill-down (path-traversal safe)."""
    from fastapi.responses import FileResponse
    from .core.config import resolve_repo_path
    if not _DOC_ID_RE.match(doc_id):
        raise HTTPException(400, "invalid doc id")
    path = resolve_repo_path("data/docs_raw") / f"{doc_id}.pdf"
    if not path.is_file():
        raise HTTPException(404, f"doc {doc_id!r} not found")
    return FileResponse(path, media_type="application/pdf",
                        filename=f"{doc_id}.pdf")


@app.get("/api/trace/{turn_id}")
def get_trace(turn_id: str) -> dict[str, Any]:
    """Full trace tree for a completed turn (contract trace.md §4.1).
    C's frontend uses this for replay / reconnect."""
    from .agent import persistence
    tree = persistence.load_trace(turn_id)
    if tree is None:
        raise HTTPException(404, f"turn {turn_id!r} not found")
    return tree


@app.get("/api/trace")
def list_traces(session_id: str | None = None, limit: int = 20) -> dict[str, Any]:
    from .agent import persistence
    return {"turns": persistence.list_turns(session_id, limit)}


@app.get("/api/usage")
def usage() -> dict[str, Any]:
    """Raw ledger summary + cache-economics stats block (C's dashboard source)."""
    from .core.usage import stats as usage_stats
    result = get_llm_service().usage_summary()
    try:
        result["stats"] = usage_stats()
    except Exception as e:  # noqa: BLE001 — dashboard must not break on ledger issues
        result["stats"] = {"error": str(e)[:200]}
    return result


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
