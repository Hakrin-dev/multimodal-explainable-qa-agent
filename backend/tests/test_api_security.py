"""W5 API boundary regression tests for C/B integration endpoints."""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import main


def test_chat_request_rejects_unbounded_or_unsafe_session_ids():
    assert main.ChatRequest(question="hello", session_id="c-demo_01:turn").session_id == "c-demo_01:turn"
    with pytest.raises(ValidationError):
        main.ChatRequest(question="hello", session_id="x/../../trace")
    with pytest.raises(ValidationError):
        main.ChatRequest(question="hello", session_id="x" * 129)


def test_rag_doc_filter_is_a_safe_document_id():
    assert main.RAGRequest(question="find", doc_filter="employee_handbook")
    with pytest.raises(ValidationError):
        main.RAGRequest(question="find", doc_filter="../../etc/passwd")
    with pytest.raises(ValidationError):
        main.RAGRequest(question="find", doc_filter="x" * 129)


def test_trace_listing_query_constraints_are_bounded():
    route = next(r for r in main.app.routes if getattr(r, "path", None) == "/api/trace")
    params = {p.name: p for p in route.dependant.query_params}
    assert params["limit"].field_info.ge == 1
    assert params["limit"].field_info.le == 100
    assert params["session_id"].field_info.max_length == 128


def test_repair_gate_returns_429_when_busy():
    assert main._repair_gate.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as exc:
            main.repair_document("doc", main.DocumentRepairRequest())
        assert exc.value.status_code == 429
    finally:
        main._repair_gate.release()
