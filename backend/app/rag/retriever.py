"""Hybrid RAG retrieval: dense + BM25, fused by RRF.

W3 optionally reranks the RRF candidate pool with bge-reranker-v2-m3.
The public search interface and six-field Citation contract remain stable.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import jieba
import numpy as np

from ..core.config import Settings, get_settings
from ..ingestion.ir import DocIR  # noqa: F401  (docs hint)
from .embedding import EmbeddingService, get_embedding_service
from .reranker import RerankerService
from .store import KBStore, StoredChunk

RRF_K = 60
CANDIDATE_K = 20
logger = logging.getLogger(__name__)


@dataclass
class ChunkHit:
    chunk: StoredChunk
    score: float
    vector_score: float = 0.0
    bm25_score: float = 0.0
    rrf_score: float = 0.0
    rerank_score: float | None = None

    def citation(self) -> dict:
        return {
            "doc": self.chunk.doc_name,
            "doc_id": self.chunk.doc_id,
            "page": self.chunk.page_start,
            "breadcrumb": " > ".join(self.chunk.breadcrumb),
            "snippet": self.chunk.text[:120],
            "score": round(self.score, 4),
        }


@dataclass
class _BM25Index:
    """In-memory BM25 over jieba tokens (fine for ≤ 10^4 chunks)."""
    docs: list[list[str]]
    df: dict[str, int] = field(default_factory=dict)
    avgdl: float = 0.0
    n: int = 0

    @classmethod
    def build(cls, chunks: list[StoredChunk]) -> "_BM25Index":
        docs = [list(jieba.cut_for_search(c.text)) for c in chunks]
        idx = cls(docs=docs, n=len(docs))
        df: dict[str, int] = {}
        for toks in docs:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        idx.df = df
        idx.avgdl = sum(len(d) for d in docs) / max(len(docs), 1)
        return idx

    def search(self, query: str, top_k: int = CANDIDATE_K) -> list[tuple[int, float]]:
        q_tokens = [t for t in jieba.cut_for_search(query) if t.strip()]
        if not q_tokens or not self.n:
            return []
        scores: list[float] = []
        k1, b = 1.5, 0.75
        for toks in self.docs:
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for q in q_tokens:
                if q not in tf:
                    continue
                idf = math.log(1 + (self.n - self.df.get(q, 0) + 0.5)
                               / (self.df.get(q, 0) + 0.5))
                s += idf * tf[q] * (k1 + 1) / (tf[q] + k1 * (1 - b + b * len(toks) / self.avgdl))
            scores.append(s)
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])[:top_k]
        return [(i, s) for i, s in ranked if s > 0]


class HybridRetriever:
    def __init__(
        self,
        store: KBStore,
        embedding: EmbeddingService | None = None,
        reranker: RerankerService | None = None,
        settings: Settings | None = None,
    ):
        self.store = store
        self.embedding = embedding or get_embedding_service()
        self.settings = settings or get_settings()
        self.reranker = reranker
        if self.reranker is None and self.settings.rerank_enabled:
            self.reranker = RerankerService(self.settings)
        self.chunks: list[StoredChunk] = []
        self.matrix: np.ndarray | None = None
        self.bm25: _BM25Index | None = None

    def reload(self) -> int:
        """(Re)load chunks from PG into memory. Call after ingest."""
        self.chunks = self.store.all_chunks()
        if self.chunks:
            self.matrix = np.stack([c.embedding for c in self.chunks])
            self.bm25 = _BM25Index.build(self.chunks)
        else:
            self.matrix, self.bm25 = None, None
        return len(self.chunks)

    def search(
        self,
        query: str,
        top_k: int = 6,
        doc_filter: str | None = None,
        dense_query: str | None = None,
    ) -> list[ChunkHit]:
        if not self.chunks or self.matrix is None:
            return []

        q = self.embedding.embed_query(dense_query or query)
        candidate_k = max(
            CANDIDATE_K,
            top_k,
            self.settings.rerank_candidate_k,
        )

        # Dense candidates (cosine; stored embeddings are normalized).
        vec_scores = (self.matrix @ q).tolist()
        vec_ranked = sorted(
            (i for i, chunk in enumerate(self.chunks)
             if not doc_filter or chunk.doc_id == doc_filter),
            key=lambda index: -vec_scores[index],
        )[:candidate_k]

        # Sparse candidates.
        bm25_ranked = (
            self.bm25.search(
                query, top_k=len(self.chunks) if doc_filter else candidate_k,
            )
            if self.bm25
            else []
        )
        if doc_filter:
            bm25_ranked = [(i, score) for i, score in bm25_ranked
                           if self.chunks[i].doc_id == doc_filter][:candidate_k]
        bm25_scores = dict(bm25_ranked)

        # Reciprocal-rank fusion.
        rrf: dict[int, float] = {}
        for rank, index in enumerate(vec_ranked):
            rrf[index] = (
                rrf.get(index, 0.0)
                + 1.0 / (RRF_K + rank + 1)
            )
        for rank, (index, _score) in enumerate(bm25_ranked):
            rrf[index] = (
                rrf.get(index, 0.0)
                + 1.0 / (RRF_K + rank + 1)
            )

        fused = sorted(
            rrf.items(),
            key=lambda item: -item[1],
        )

        candidates: list[ChunkHit] = []
        for index, rrf_score in fused:
            chunk = self.chunks[index]
            if doc_filter and chunk.doc_id != doc_filter:
                continue

            candidates.append(
                ChunkHit(
                    chunk=chunk,
                    score=rrf_score,
                    vector_score=float(vec_scores[index]),
                    bm25_score=float(bm25_scores.get(index, 0.0)),
                    rrf_score=rrf_score,
                )
            )
            if len(candidates) >= candidate_k:
                break

        if self.reranker and candidates:
            try:
                rerank_scores = self.reranker.score(
                    query,
                    [hit.chunk.text for hit in candidates],
                )
                if len(rerank_scores) != len(candidates):
                    raise ValueError(
                        "reranker score count does not match candidates"
                    )

                for hit, rerank_score in zip(
                    candidates,
                    rerank_scores,
                ):
                    hit.rerank_score = float(rerank_score)

                candidates.sort(
                    key=lambda hit: (
                        -float(hit.rerank_score),
                        -hit.rrf_score,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "RAG reranker failed; falling back to RRF ranking: %s",
                    exc,
                )

        return candidates[:top_k]
