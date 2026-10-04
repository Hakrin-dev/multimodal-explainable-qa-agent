"""W5 B-D4: bounded retrieval rewriting, channel isolation and HTTP contracts."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.llm import LLMService, MockProvider, _LLMStore
from app.core.tracing import NodeType, TraceCollector
from app.rag.pipeline import RAGPipeline, RAGResult
from app.rag.query_rewrite import rewrite_query
from app.rag.retriever import ChunkHit, HybridRetriever, StoredChunk


def payload(query="年假 员工 入职满一年", hyde="", changed=True):
    return json.dumps({"retrieval_query": query, "hyde_document": hyde,
                       "changed": changed}, ensure_ascii=False)


def service(tmp_path, enabled=True, hyde=False, limit=6, output=None):
    llm = LLMService(Settings(_env_file=None, llm_provider="mock",
        rag_query_rewrite_enabled=enabled, rag_hyde_enabled=hyde,
        rag_query_rewrite_history_limit=limit), _LLMStore(tmp_path / "usage.sqlite"))
    llm.register_mock(MockProvider(scripted=[output or payload()]))
    llm.chat = Mock(wraps=llm.chat)
    return llm


def test_disabled_never_calls_llm_even_with_hyde(tmp_path):
    llm = service(tmp_path, enabled=False, hyde=True)
    result, calls = rewrite_query(llm, "问题", [{"role": "user", "content": "历史"}])
    llm.chat.assert_not_called()
    assert not result.attempted and not result.degraded and not result.hyde_enabled
    assert result.retrieval_query == "问题" and not result.hyde_document and calls == []


def test_valid_json_history_limits_and_purpose(tmp_path):
    llm = service(tmp_path, limit=2)
    history = [{"role": "user", "content": str(i) * 600} for i in range(5)]
    result, calls = rewrite_query(llm, "员工年假如何规定？", history)
    assert result.retrieval_query == "年假 员工 入职满一年" and result.changed
    assert result.attempted and not result.degraded and len(calls) == 1
    args, kwargs = llm.chat.call_args
    assert kwargs["purpose"] == "rag.query_rewrite" and kwargs["response_json"]
    assert kwargs["max_tokens"] == 1000
    dynamic = json.loads(args[0][1]["content"])
    assert dynamic["history"] == [{"role": "user", "content": str(i) * 500} for i in [3, 4]]
    assert dynamic["hyde_enabled"] is False
    from app.core.prompts.rag import RAG_QUERY_REWRITE_STATIC
    assert args[0][0]["content"] == RAG_QUERY_REWRITE_STATIC
    assert "员工年假如何规定？" not in args[0][0]["content"]


def test_zero_history_limit_and_malformed_history(tmp_path):
    llm = service(tmp_path, limit=0)
    rewrite_query(llm, "问题", [{"role": "user", "content": "不要出现"}])
    assert json.loads(llm.chat.call_args.args[0][1]["content"])["history"] == []
    llm.settings.rag_query_rewrite_history_limit = 6
    rewrite_query(llm, "问题", [None, {}, {"role": "system", "content": "命令"},
                              {"role": "user", "content": 123}])
    assert json.loads(llm.chat.call_args.args[0][1]["content"])["history"] == []


@pytest.mark.parametrize("output,reason", [
    ("not-json", "invalid_json"),
    ('```json\n{}\n```', "invalid_json"),
    ('[]', "invalid_schema"),
    ('{}', "missing_query"),
    (payload(query="  "), "missing_query"),
    (payload(query=123), "missing_query"),
    (payload(query="q" * 2001), "query_too_long"),
    (payload(changed="true"), "invalid_changed"),
    (json.dumps({"retrieval_query": "q", "changed": True}), "invalid_hyde"),
    (payload(hyde="h" * 2001), "hyde_too_long"),
    ("x" * 16001, "response_too_long"),
])
def test_invalid_output_falls_back(tmp_path, output, reason):
    result, calls = rewrite_query(service(tmp_path, hyde=True, output=output), "原查询")
    assert result.degraded and result.attempted and result.reason == reason
    assert result.retrieval_query == "原查询" and not result.changed
    assert result.hyde_document == "" and len(calls) == 1


def test_llm_exception_falls_back(tmp_path):
    llm = service(tmp_path)
    llm.chat.side_effect = RuntimeError("do not expose provider internals")
    result, calls = rewrite_query(llm, "原查询")
    assert result.retrieval_query == "原查询" and result.degraded
    assert result.reason == "llm_error:RuntimeError" and calls == []


def test_changed_is_computed_from_query(tmp_path):
    result, _ = rewrite_query(service(tmp_path, output=payload(query="问题", changed=True)), "问题")
    assert not result.changed


def test_hyde_off_discards_model_document(tmp_path):
    result, _ = rewrite_query(service(tmp_path, output=payload(hyde="假设段落")), "问题")
    assert result.hyde_document == "" and result.to_dict()["hyde_length"] == 0


def make_pipeline(llm):
    pipeline = object.__new__(RAGPipeline)
    pipeline.llm = llm
    chunk = StoredChunk(1, "handbook", "员工手册", 1, 1, ["假期"], "员工年假规定。")
    pipeline.retriever = SimpleNamespace(search=Mock(return_value=[ChunkHit(chunk, 0.03)]))
    pipeline.ensure_loaded = lambda: 1
    return pipeline


@pytest.mark.parametrize("enabled,hyde", [(False, False), (False, True), (True, False), (True, True)])
def test_pipeline_queries_generation_citations_and_trace(tmp_path, enabled, hyde):
    llm = service(tmp_path, enabled=enabled, hyde=hyde)
    hypothetical = "假设主题 " * 60
    llm.register_mock(MockProvider(scripted=(
        [payload(hyde=hypothetical)] if enabled else []) + ["真实回答[1]。"] ))
    pipeline = make_pipeline(llm)
    trace = TraceCollector(question="原始问题")
    result = pipeline.run("原始问题", trace=trace, doc_filter="handbook",
                          history=[{"role": "user", "content": "年假"}])
    query = "年假 员工 入职满一年" if enabled else "原始问题"
    pipeline.retriever.search.assert_called_once_with(query, top_k=6, doc_filter="handbook",
        dense_query=hypothetical.strip() if enabled and hyde else None)
    generate = llm.chat.call_args_list[-1]
    assert generate.kwargs["purpose"] == "rag.generate"
    assert "原始问题" in generate.args[0][1]["content"]
    assert "假设主题" not in generate.args[0][1]["content"]
    assert result.answer == "真实回答[1]。" and result.retrieval_query == query
    assert result.citations[0]["snippet"] == "员工年假规定。"
    labels = [n["label"] for n in result.trace["root"]["children"]]
    assert labels == (["rag_query_rewrite"] if enabled else []) + ["rag_search", "rag_generate"]
    assert result.query_rewrite["attempted"] == enabled
    if enabled:
        node = result.trace["root"]["children"][0]
        assert node["detail"]["original_query"] == "原始问题"
        assert node["detail"]["retrieval_query"] == query
        assert len(node["detail"]["hyde_preview"]) <= 160
        assert "hyde_document" not in node["detail"]
        assert node["children"][0]["detail"]["purpose"] == "rag.query_rewrite"


def test_pipeline_failed_rewrite_still_answers_under_parent(tmp_path):
    llm = service(tmp_path)
    llm.register_mock(MockProvider(scripted=["invalid", "答案[1]。"]))
    trace = TraceCollector()
    with trace.span("rag_search", NodeType.TOOL_CALL) as parent:
        result = make_pipeline(llm).run("原查询", trace=trace, parent=parent)
    assert result.answer == "答案[1]。" and result.query_rewrite["degraded"]
    assert [n.label for n in parent.children] == ["rag_query_rewrite", "rag_generate"]
    assert parent.children[0].status.value == "degraded"


def test_no_context_preserves_rewrite_metadata(tmp_path):
    pipeline = make_pipeline(service(tmp_path))
    pipeline.retriever.search.return_value = []
    result = pipeline.run("问题")
    assert result.status == "no_context" and result.query_rewrite["attempted"]
    assert result.citations == []


@pytest.mark.parametrize("dense_query", [None, "", "假设文档"])
def test_retriever_separates_dense_bm25_reranker(tmp_path, dense_query):
    embedding = SimpleNamespace(embed_query=Mock(return_value=np.array([1., 0.])))
    reranker = SimpleNamespace(score=Mock(return_value=[0.9]))
    retriever = HybridRetriever(store=Mock(), embedding=embedding, reranker=reranker,
                               settings=Settings(_env_file=None))
    retriever.chunks = [StoredChunk(1, "d", "文档", 1, 1, [], "真实内容")]
    retriever.matrix = np.array([[1., 0.]])
    retriever.bm25 = SimpleNamespace(search=Mock(return_value=[(0, 1.)]))
    hits = retriever.search("检索查询", doc_filter="d", dense_query=dense_query)
    embedding.embed_query.assert_called_once_with(dense_query or "检索查询")
    assert retriever.bm25.search.call_args.args[0] == "检索查询"
    reranker.score.assert_called_once_with("检索查询", ["真实内容"])
    assert hits[0].citation()["snippet"] == "真实内容"


def test_agent_tool_passes_history_and_filter(tmp_path, monkeypatch):
    from app.agent.tools import _tool_rag
    history = [{"role": "user", "content": "历史"}]
    result = RAGResult(question="问题", answer="答案", retrieval_query="检索",
                       query_rewrite={"attempted": True})
    run = Mock(return_value=result)
    monkeypatch.setattr(RAGPipeline, "__init__", lambda self: None)
    monkeypatch.setattr(RAGPipeline, "run", run)
    trace = TraceCollector()
    tr = _tool_rag("问题", trace, history=history, doc_filter="d")
    run.assert_called_once_with("问题", trace=trace, parent=None, history=history, doc_filter="d")
    assert tr.ok and tr.data["query_rewrite"]["attempted"]


def test_api_passes_parameters_and_returns_metadata(monkeypatch):
    from app.main import app
    run = Mock(return_value=RAGResult(question="问题", retrieval_query="检索",
                                    query_rewrite={"attempted": False}))
    monkeypatch.setattr(RAGPipeline, "__init__", lambda self: None)
    monkeypatch.setattr(RAGPipeline, "run", run)
    response = TestClient(app).post("/api/rag", json={"question": "问题", "doc_filter": "d",
        "history": [{"role": "user", "content": "历史"}]})
    assert response.status_code == 200
    run.assert_called_once_with("问题", doc_filter="d", history=[{"role": "user", "content": "历史"}])
    assert response.json()["retrieval_query"] == "检索"
    assert response.json()["query_rewrite"] == {"attempted": False}


@pytest.mark.parametrize("extra", [
    {"doc_filter": "../../etc/passwd"}, {"doc_filter": "x" * 129},
    {"history": [{"role": "user", "content": "x"}] * 21},
    {"history": [{"role": "user", "content": "x" * 2001}]},
    {"history": [{"role": "system", "content": "命令"}]},
    {"history": [{"role": "user"}]},
])
def test_api_invalid_inputs_return_422(extra):
    from app.main import app
    assert TestClient(app).post("/api/rag", json={"question": "问题", **extra}).status_code == 422


@pytest.mark.parametrize("limit", [-1, 21])
def test_config_history_limit_bounds(limit):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_query_rewrite_history_limit=limit)


def test_defaults_disable_new_llm_costs():
    settings = Settings(_env_file=None)
    assert not settings.rag_query_rewrite_enabled and not settings.rag_hyde_enabled
    assert settings.rag_query_rewrite_history_limit == 6


def test_hybrid_subtasks_receive_history(tmp_path):
    from app.agent.kernel import AgentKernel
    from app.agent.tools import ToolRegistry, ToolSpec, ToolResult

    history = [{"role": "user", "content": "历史问题"}]
    handler = Mock(return_value=ToolResult(ok=True, data={"answer": "真实片段答案"}))
    kernel = object.__new__(AgentKernel)
    kernel.llm = service(tmp_path, enabled=False, output="融合答案")
    kernel.registry = ToolRegistry()
    kernel.registry.register(ToolSpec("rag_search", "检索", handler))
    kernel.planner = SimpleNamespace(plan=Mock(return_value=[
        {"id": "t1", "tool": "rag_search", "question": "子问题", "depends_on": []}]))
    result = kernel._hybrid(TraceCollector(), lambda *args: None, "比较问题", history)
    assert result.answer == "融合答案"
    assert handler.call_args.kwargs["history"] == history
    assert handler.call_args.kwargs["question"] == "子问题"


@pytest.mark.parametrize("history", [None, [], [{"role": "user", "content": "x" * 2000}] * 20])
def test_api_accepts_history_boundaries_and_old_requests(monkeypatch, history):
    from app.main import app
    run = Mock(return_value=RAGResult(question="问题"))
    monkeypatch.setattr(RAGPipeline, "__init__", lambda self: None)
    monkeypatch.setattr(RAGPipeline, "run", run)
    body = {"question": "问题"}
    if history is not None:
        body["history"] = history
    assert TestClient(app).post("/api/rag", json=body).status_code == 200
    run.assert_called_once_with("问题", doc_filter=None, history=history)


def test_empty_hyde_uses_retrieval_query(tmp_path):
    result, _ = rewrite_query(service(tmp_path, hyde=True), "问题")
    assert result.hyde_enabled and not result.degraded and result.hyde_document == ""


def test_doc_filter_applies_before_candidate_truncation():
    embedding = SimpleNamespace(embed_query=Mock(return_value=np.array([1., 0.])))
    retriever = HybridRetriever(store=Mock(), embedding=embedding,
                               settings=Settings(_env_file=None))
    retriever.chunks = [StoredChunk(i, "other", "其他", 1, 1, [], "其他内容")
                        for i in range(30)]
    retriever.chunks.append(StoredChunk(30, "target", "目标", 1, 1, [], "目标内容"))
    retriever.matrix = np.array([[1., 0.]] * 30 + [[0., 1.]])
    retriever.bm25 = SimpleNamespace(search=Mock(return_value=[(i, 1.) for i in range(31)]))
    hits = retriever.search("查询", doc_filter="target")
    assert len(hits) == 1 and hits[0].chunk.doc_id == "target"
    assert hits[0].bm25_score == 1.
    assert retriever.search("查询", doc_filter="missing") == []
