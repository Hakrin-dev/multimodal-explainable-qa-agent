"""RAG reranker tests without loading real model weights."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from pydantic import ValidationError

from app.core.config import Settings
from app.rag.reranker import RerankerError, RerankerService
from app.rag.retriever import HybridRetriever
from app.rag.store import StoredChunk


class FakeStore:
    def __init__(self, chunks):
        self._chunks = chunks

    def all_chunks(self):
        return self._chunks


class FakeEmbedding:
    def embed_query(self, query):
        return np.asarray([1.0, 0.0], dtype=np.float32)


class ReverseReranker:
    def score(self, query, documents):
        return [float(index) for index in range(len(documents))]


class FailingReranker:
    def score(self, query, documents):
        raise RuntimeError("synthetic reranker failure")


def make_chunks():
    chunks = [
        StoredChunk(
            1,
            "doc-a",
            "A",
            1,
            1,
            ["A"],
            "库存预警规则",
        ),
        StoredChunk(
            2,
            "doc-b",
            "B",
            1,
            1,
            ["B"],
            "员工年假制度",
        ),
        StoredChunk(
            3,
            "doc-c",
            "C",
            1,
            1,
            ["C"],
            "客户退款流程",
        ),
    ]
    chunks[0].embedding = np.asarray([1.0, 0.0], dtype=np.float32)
    chunks[1].embedding = np.asarray([0.8, 0.2], dtype=np.float32)
    chunks[2].embedding = np.asarray([0.6, 0.4], dtype=np.float32)
    return chunks


def make_settings(**overrides):
    values = {
        "_env_file": None,
        "embedding_provider": "mock",
        "rerank_enabled": False,
        "rerank_candidate_k": 20,
    }
    values.update(overrides)
    return Settings(**values)


def test_reranker_changes_order_and_preserves_rrf():
    retriever = HybridRetriever(
        FakeStore(make_chunks()),
        embedding=FakeEmbedding(),
        reranker=ReverseReranker(),
        settings=make_settings(),
    )
    retriever.reload()

    hits = retriever.search("库存", top_k=3)

    assert [hit.chunk.doc_id for hit in hits] == [
        "doc-c",
        "doc-b",
        "doc-a",
    ]
    assert [hit.rerank_score for hit in hits] == [2.0, 1.0, 0.0]
    assert all(hit.score == hit.rrf_score for hit in hits)
    assert all(hit.rrf_score > 0 for hit in hits)


def test_reranker_failure_falls_back_to_rrf(caplog):
    retriever = HybridRetriever(
        FakeStore(make_chunks()),
        embedding=FakeEmbedding(),
        reranker=FailingReranker(),
        settings=make_settings(),
    )
    retriever.reload()

    with caplog.at_level(logging.WARNING):
        hits = retriever.search("库存", top_k=3)

    assert hits
    assert all(hit.rerank_score is None for hit in hits)
    assert all(hit.score == hit.rrf_score for hit in hits)
    assert "falling back to RRF" in caplog.text


def test_citation_contract_remains_six_fields():
    retriever = HybridRetriever(
        FakeStore(make_chunks()),
        embedding=FakeEmbedding(),
        reranker=ReverseReranker(),
        settings=make_settings(),
    )
    retriever.reload()

    citation = retriever.search("库存", top_k=1)[0].citation()

    assert set(citation) == {
        "doc",
        "doc_id",
        "page",
        "breadcrumb",
        "snippet",
        "score",
    }


def test_reranker_service_batches_and_returns_logits(monkeypatch):
    settings = make_settings(
        rerank_provider="local",
        rerank_device="cpu",
        rerank_batch_size=2,
        rerank_max_length=256,
    )
    service = RerankerService(settings)

    class FakeTokenizer:
        def __init__(self):
            self.batch_sizes = []
            self.max_lengths = []

        def __call__(
            self,
            pairs,
            *,
            padding,
            truncation,
            max_length,
            return_tensors,
        ):
            self.batch_sizes.append(len(pairs))
            self.max_lengths.append(max_length)
            return {
                "input_ids": torch.arange(
                    len(pairs),
                    dtype=torch.float32,
                ).reshape(-1, 1)
            }

    class FakeModel:
        def __call__(self, **inputs):
            return SimpleNamespace(logits=inputs["input_ids"] + 0.5)

    tokenizer = FakeTokenizer()
    monkeypatch.setattr(
        service,
        "_load_local",
        lambda: (tokenizer, FakeModel()),
    )

    scores = service.score("问题", ["甲", "乙", "丙"])

    assert scores == [0.5, 1.5, 0.5]
    assert tokenizer.batch_sizes == [2, 1]
    assert tokenizer.max_lengths == [256, 256]


def test_unknown_reranker_provider_is_rejected():
    service = RerankerService(
        make_settings(rerank_provider="unsupported")
    )

    with pytest.raises(RerankerError, match="unknown rerank provider"):
        service.score("问题", ["文档"])


def test_reranker_defaults_and_validation():
    settings = Settings(_env_file=None)

    assert settings.rerank_enabled is False
    assert settings.rerank_provider == "local"
    assert settings.rerank_batch_size == 8
    assert settings.rerank_candidate_k == 20
    assert settings.rerank_max_length == 512

    with pytest.raises(ValidationError):
        Settings(_env_file=None, rerank_candidate_k=0)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, rerank_batch_size=129)
