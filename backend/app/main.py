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
