"""RAG faithfulness self-check tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.core.config import Settings
from app.core.llm import LLMService, MockProvider, _LLMStore
from app.rag.factcheck import check_answer


CHUNKS = [
    {
        "idx": 1,
        "doc": "员工手册",
        "page": 1,
        "breadcrumb": "假期制度",
        "text": "入职满一年的员工每年享有 10 天年假。",
    }
]


def _service(tmp_path, scripted: list[str]) -> tuple[LLMService, MockProvider]:
    settings = Settings(
        llm_provider="mock",
        rag_factcheck_enabled=True,
        rag_factcheck_max_rounds=2,
    )
    service = LLMService(
        settings,
        _LLMStore(tmp_path / "usage.sqlite"),
    )
    provider = MockProvider(scripted=scripted)
    service.register_mock(provider)
    return service, provider


def test_factcheck_accepts_supported_answer(tmp_path):
    service, provider = _service(
        tmp_path,
        [
            json.dumps(
                {
                    "faithful": True,
                    "unsupported_sentences": [],
                    "revised_answer": "员工每年享有 10 天年假[1]。",
                },
                ensure_ascii=False,
            )
        ],
    )

    outcome, calls = check_answer(
        service,
        question="年假有几天？",
        answer="员工每年享有 10 天年假[1]。",
        chunks=CHUNKS,
    )

    assert outcome.faithful is True
    assert outcome.rewritten is False
    assert outcome.attempts == 1
    assert outcome.degraded is False
    assert len(calls) == 1
    assert provider._i == 1


def test_factcheck_rewrites_then_verifies(tmp_path):
    revised = "员工每年享有 10 天年假[1]。"
    service, provider = _service(
        tmp_path,
        [
            json.dumps(
                {
                    "faithful": False,
                    "unsupported_sentences": ["员工另有 5 天奖励假。"],
                    "revised_answer": revised,
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "faithful": True,
                    "unsupported_sentences": [],
                    "revised_answer": revised,
                },
                ensure_ascii=False,
            ),
        ],
    )

    outcome, calls = check_answer(
        service,
        question="年假有几天？",
        answer="员工有 10 天年假和 5 天奖励假[1]。",
        chunks=CHUNKS,
    )

    assert outcome.answer == revised
    assert outcome.faithful is True
    assert outcome.rewritten is True
    assert outcome.attempts == 2
    assert outcome.unsupported_sentences == ["员工另有 5 天奖励假。"]
    assert len(calls) == 2
    assert provider._i == 2


def test_factcheck_invalid_json_degrades_without_changing_answer(tmp_path):
    original = "员工每年享有 10 天年假[1]。"
    service, _ = _service(tmp_path, ["not-json"])

    outcome, calls = check_answer(
        service,
        question="年假有几天？",
        answer=original,
        chunks=CHUNKS,
    )

    assert outcome.answer == original
    assert outcome.faithful is False
    assert outcome.degraded is True
    assert outcome.reason == "invalid_json"
    assert len(calls) == 1


def test_factcheck_stops_when_revision_missing(tmp_path):
    original = "员工另有 5 天奖励假。"
    service, _ = _service(
        tmp_path,
        [
            json.dumps(
                {
                    "faithful": False,
                    "unsupported_sentences": [original],
                    "revised_answer": "",
                },
                ensure_ascii=False,
            )
        ],
    )

    outcome, _ = check_answer(
        service,
        question="年假有几天？",
        answer=original,
        chunks=CHUNKS,
    )

    assert outcome.answer == original
    assert outcome.degraded is True
    assert outcome.reason == "missing_revision"
    assert outcome.attempts == 1


def test_factcheck_has_strict_two_round_limit(tmp_path):
    service, provider = _service(
        tmp_path,
        [
            json.dumps(
                {
                    "faithful": False,
                    "unsupported_sentences": ["错误一"],
                    "revised_answer": "第一次重写[1]。",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "faithful": False,
                    "unsupported_sentences": ["错误二"],
                    "revised_answer": "第二次重写[1]。",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "faithful": True,
                    "unsupported_sentences": [],
                    "revised_answer": "不应执行",
                },
                ensure_ascii=False,
            ),
        ],
    )

    outcome, calls = check_answer(
        service,
        question="测试问题",
        answer="原答案",
        chunks=CHUNKS,
        max_rounds=2,
    )

    assert outcome.answer == "第二次重写[1]。"
    assert outcome.faithful is False
    assert outcome.degraded is True
    assert outcome.reason == "max_rounds_exhausted"
    assert outcome.attempts == 2
    assert len(calls) == 2
    assert provider._i == 2


def test_factcheck_prompt_keeps_dynamic_content_out_of_system():
    from app.core.prompts.rag import (
        RAG_FACTCHECK_STATIC,
        build_factcheck_messages,
    )

    messages = build_factcheck_messages(
        question="年假有几天？",
        answer="年假为 10 天[1]。",
        chunks=CHUNKS,
    )

    assert messages[0] == {
        "role": "system",
        "content": RAG_FACTCHECK_STATIC,
    }
    assert "年假有几天" not in messages[0]["content"]
    assert "年假有几天" in messages[1]["content"]
    assert "年假为 10 天" in messages[1]["content"]


def test_pipeline_factcheck_trace_and_rewrite(tmp_path):
    from app.rag.pipeline import RAGPipeline

    settings = Settings(
        llm_provider="mock",
        rag_factcheck_enabled=True,
        rag_factcheck_max_rounds=2,
    )
    service = LLMService(
        settings,
        _LLMStore(tmp_path / "pipeline.sqlite"),
    )

    revised = "员工每年享有 10 天年假[1]。"
    service.register_mock(
        MockProvider(
            scripted=[
                "员工有 10 天年假和 5 天奖励假[1]。",
                json.dumps(
                    {
                        "faithful": False,
                        "unsupported_sentences": ["员工有 5 天奖励假。"],
                        "revised_answer": revised,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "faithful": True,
                        "unsupported_sentences": [],
                        "revised_answer": revised,
                    },
                    ensure_ascii=False,
                ),
            ]
        )
    )

    chunk = SimpleNamespace(
        doc_name="员工手册",
        doc_id="employee_handbook",
        page_start=1,
        breadcrumb=["员工手册", "假期制度"],
        text="入职满一年的员工每年享有 10 天年假。",
    )

    class FakeHit:
        score = 0.03
        vector_score = 0.8
        bm25_score = 2.0
        rrf_score = 0.03
        rerank_score = None

        def __init__(self):
            self.chunk = chunk

        def citation(self):
            return {
                "doc": chunk.doc_name,
                "doc_id": chunk.doc_id,
                "page": chunk.page_start,
                "breadcrumb": " > ".join(chunk.breadcrumb),
                "snippet": chunk.text,
                "score": self.score,
            }

    class FakeRetriever:
        def search(self, question, top_k=6):
            return [FakeHit()]

    pipeline = object.__new__(RAGPipeline)
    pipeline.llm = service
    pipeline.retriever = FakeRetriever()
    pipeline.ensure_loaded = lambda: 1

    result = pipeline.run("年假有几天？")

    assert result.initial_answer == (
        "员工有 10 天年假和 5 天奖励假[1]。"
    )
    assert result.answer == revised
    assert result.faithfulness["faithful"] is True
    assert result.faithfulness["rewritten"] is True
    assert result.faithfulness["attempts"] == 2

    children = result.trace["root"]["children"]
    assert [child["label"] for child in children] == [
        "rag_search",
        "rag_generate",
        "rag_factcheck",
    ]

    factcheck_node = children[-1]
    assert factcheck_node["status"] == "ok"
    assert len(factcheck_node["children"]) == 2
    assert all(
        child["detail"]["purpose"] == "rag.factcheck"
        for child in factcheck_node["children"]
    )


def test_factcheck_llm_error_preserves_answer(tmp_path):
    original = "员工每年享有 10 天年假[1]。"
    settings = Settings(
        llm_provider="mock",
        rag_factcheck_enabled=True,
        rag_factcheck_max_rounds=2,
    )
    service = LLMService(
        settings,
        _LLMStore(tmp_path / "error.sqlite"),
    )

    def fail(messages, params):
        raise RuntimeError("simulated provider failure")

    service.register_mock(MockProvider(handler=fail))

    outcome, calls = check_answer(
        service,
        question="年假有几天？",
        answer=original,
        chunks=CHUNKS,
    )

    assert outcome.answer == original
    assert outcome.checked is True
    assert outcome.faithful is False
    assert outcome.degraded is True
    assert outcome.reason == "llm_error:RuntimeError"
    assert outcome.attempts == 1
    assert calls == []
