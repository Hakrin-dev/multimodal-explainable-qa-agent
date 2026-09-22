"""RAG pipeline (W1 minimal closed loop):

    question → ① hybrid retrieve (dense+BM25, RRF) → ② cited generation
    → ③ (W3: 忠实度自检 rerank)

Trace: tool_call node `rag_search` (detail.citations[]) + llm_call child +
`rag_generate` step. Mirrors NL2SQLPipeline structure so the W2 orchestrator
treats both as interchangeable tools.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..core.llm import LLMService, get_llm_service
from ..core.prompts import rag as prompts
from ..core.tracing import NodeType, TraceCollector
from ..ingestion import chunker, pdf_ingest
from ..ingestion.ir import DocIR
from .embedding import EmbeddingService, get_embedding_service
from .retriever import ChunkHit, HybridRetriever
from .store import KBStore


@dataclass
class RAGResult:
    question: str
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    hits: list[dict] = field(default_factory=list)   # debug: full hit info
    status: str = "ok"  # ok | no_context | error
    trace: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


def ingest_document(path: str, store: KBStore | None = None,
                    embedding: EmbeddingService | None = None,
                    force: bool = False) -> tuple[DocIR, int]:
    """Parse → chunk → embed → store. Skips unchanged docs (content hash)."""
    embedding = embedding or get_embedding_service()
    store = store or KBStore(dim=embedding.dim)
    doc = pdf_ingest.parse_pdf(path)
    chunker.chunk_doc(doc)

    if not force and store.doc_content_hash(doc.doc_id) == doc.content_hash():
        return doc, 0  # unchanged, skip

    embeddings = embedding.embed([c.text for c in doc.chunks])
    store.upsert_doc(doc, chunk_embeddings=np.asarray(embeddings))
    return doc, len(doc.chunks)


class RAGPipeline:
    def __init__(self, llm: LLMService | None = None,
                 embedding: EmbeddingService | None = None):
        self.llm = llm or get_llm_service()
        self.embedding = embedding or get_embedding_service()
        self.store = KBStore(dim=self.embedding.dim)
        self.retriever = HybridRetriever(self.store, self.embedding)
        self._loaded = False

    def ensure_loaded(self) -> int:
        if not self._loaded:
            self.retriever.reload()
            self._loaded = True
        return len(self.retriever.chunks)

    # ------------------------------------------------------------------ api

    def run(self, question: str, trace: TraceCollector | None = None,
            top_k: int = 6, parent=None) -> RAGResult:
        """parent: kernel's tool_call node. When given, this pipeline's steps
        nest under it directly (no double rag_search wrapping)."""
        trace = trace or TraceCollector(question=question)
        t0 = time.monotonic()
        result = RAGResult(question=question)
        self.ensure_loaded()

        # ① retrieve — own tool_call span only when standalone (no double wrap)
        node = None
        if parent is None:
            cm = trace.span("rag_search", NodeType.TOOL_CALL, input=question)
        else:
            from contextlib import nullcontext
            cm = nullcontext(parent)
        with cm as n:
            node = n
            hits: list[ChunkHit] = self.retriever.search(question, top_k=top_k)
            result.citations = [h.citation() for h in hits]
            result.hits = [
                {"doc": h.chunk.doc_name, "page": h.chunk.page_start,
                 "score": h.score, "vector": round(h.vector_score, 4),
                 "text": h.chunk.text[:200]}
                for h in hits
            ]
            if parent is None:   # kernel finishes its own node later
                trace.finish(node, output=f"{len(hits)} chunks",
                             detail={"citations": result.citations[:8],
                                     "hit_count": len(hits)})
            else:                # enrich the kernel's node with citations
                node.detail.setdefault("citations", result.citations[:8])
                node.detail["hit_count"] = len(hits)

        if not hits:
            result.status = "no_context"
            result.answer = "知识库中未找到相关信息。"
            result.trace = trace.to_dict()
            result.latency_ms = int((time.monotonic() - t0) * 1000)
            return result

        # ② cited generation
        with trace.span("rag_generate", NodeType.STEP, parent=parent) as node:
            chunks_payload = [
                {"idx": i + 1, "doc": h.chunk.doc_name, "page": h.chunk.page_start,
                 "breadcrumb": " > ".join(h.chunk.breadcrumb), "text": h.chunk.text}
                for i, h in enumerate(hits)
            ]
            messages = prompts.build_rag_messages(question, chunks_payload)
            resp = self.llm.chat(messages, temperature=0.2, max_tokens=500,
                                 purpose="rag.generate")
            with trace.span("llm", NodeType.LLM_CALL, parent=node) as llm_node:
                trace.finish(llm_node, detail={
                    "model": resp.model, "cost_rmb": resp.cost_rmb,
                    "tokens": {"prompt": resp.usage.prompt_tokens,
                               "completion": resp.usage.completion_tokens},
                })
            result.answer = resp.content.strip()
            trace.finish(node, output=result.answer[:300])

        # ③ 忠实度自检 — W3 (B)：逐句引用支撑校验，不支撑则收敛重写

        result.trace = trace.to_dict()
        result.latency_ms = int((time.monotonic() - t0) * 1000)
        return result
