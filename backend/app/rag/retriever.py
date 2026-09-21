"""Hybrid retrieval (W1 minimal): dense (pgvector-loaded) + BM25 (in-memory)
fused by RRF. W3 (B): bge-reranker-v2-m3 精排 + HNSW + filters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..ingestion.ir import DocIR  # noqa: F401  (docs hint)
from .embedding import EmbeddingService, get_embedding_service
from .store import KBStore, StoredChunk

import jieba

RRF_K = 60
CANDIDATE_K = 20


@dataclass
class ChunkHit:
    chunk: StoredChunk
    score: float
    vector_score: float = 0.0
    bm25_score: float = 0.0

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
    def __init__(self, store: KBStore, embedding: EmbeddingService | None = None):
        self.store = store
        self.embedding = embedding or get_embedding_service()
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

    def search(self, query: str, top_k: int = 6,
               doc_filter: str | None = None) -> list[ChunkHit]:
        if not self.chunks or self.matrix is None:
            return []
        q = self.embedding.embed_query(query)

        # dense candidates (cosine — embeddings are normalized)
        vec_scores = (self.matrix @ q).tolist()
        vec_ranked = sorted(range(len(self.chunks)), key=lambda i: -vec_scores[i])[:CANDIDATE_K]

        # sparse candidates
        bm25_ranked = self.bm25.search(query) if self.bm25 else []

        # RRF fusion
        rrf: dict[int, float] = {}
        for rank, i in enumerate(vec_ranked):
            rrf[i] = rrf.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
        for rank, (i, _s) in enumerate(bm25_ranked):
            rrf[i] = rrf.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)

        hits = sorted(rrf.items(), key=lambda x: -x[1])
        out: list[ChunkHit] = []
        for i, score in hits:
            c = self.chunks[i]
            if doc_filter and c.doc_id != doc_filter:
                continue
            out.append(ChunkHit(chunk=c, score=score,
                                vector_score=float(vec_scores[i]),
                                bm25_score=0.0))
            if len(out) >= top_k:
                break
        return out
