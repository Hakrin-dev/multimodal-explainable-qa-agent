"""FastAPI entrypoint — W1 scope: health / nl2sql minimal loop / schema / usage.

W2 will add the agent orchestration endpoints (/api/chat SSE, /api/trace).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
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


class DocumentRepairRequest(BaseModel):
    correct_orientation: bool = True
    enhance_clarity: bool = True
    run_ocr: bool = True
    normalize_traditional: bool = True


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


@app.post("/api/docs/upload", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload and assess a PDF without indexing it."""
    from .ingestion import documents

    try:
        content = await file.read(
            documents.MAX_UPLOAD_BYTES + 1
        )
        return documents.save_upload(
            file.filename,
            content,
        )
    except documents.DocumentTooLargeError as exc:
        raise HTTPException(413, str(exc)) from exc
    except documents.DocumentValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await file.close()


@app.get("/api/docs/{doc_id}/quality")
def get_document_quality(doc_id: str) -> dict[str, Any]:
    """Return original/repaired quality and complexity reports."""
    from .ingestion import documents

    try:
        return documents.quality_report(doc_id)
    except documents.ManagedDocumentNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except documents.DocumentValidationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/docs/{doc_id}/repair")
def repair_document(
    doc_id: str,
    req: DocumentRepairRequest,
) -> dict[str, Any]:
    """Create a repaired copy and optionally run OCR normalization."""
    from .ingestion import documents

    try:
        return documents.repair_managed_document(
            doc_id,
            correct_orientation=req.correct_orientation,
            enhance_clarity=req.enhance_clarity,
            run_ocr=req.run_ocr,
            normalize_traditional=req.normalize_traditional,
        )
    except documents.ManagedDocumentNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except documents.DocumentValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except documents.DocumentManagementError as exc:
        raise HTTPException(422, str(exc)) from exc


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
def get_doc_pdf(
    doc_id: str,
    version: str = "original",
):
    """Serve an original or repaired PDF safely."""
    from fastapi.responses import FileResponse
    from .ingestion import documents

    try:
        path = documents.resolve_pdf(
            doc_id,
            version=version,
        )
    except documents.ManagedDocumentNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except documents.DocumentValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{doc_id}-{version}.pdf",
    )


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
        "rewritten": r.rewritten,
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
