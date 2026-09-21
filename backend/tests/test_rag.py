"""RAG tests: mock-embedding retrieval unit tests + DB-backed integration."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.llm import LLMService, MockProvider, _LLMStore
from app.db.session import test_connection

requires_db = pytest.mark.skipif(
    not test_connection(), reason="live PostgreSQL required (run quick_start.sh)"
)


def _local_model_present() -> bool:
    """Clean-state guard: integration tests need the local embedding model
    (B re-downloads it per onboarding doc §1; model dir is NOT in git)."""
    from app.core.config import resolve_repo_path, Settings
    try:
        return resolve_repo_path(Settings().embedding_model_path).exists()
    except Exception:
        return False


requires_embedding = pytest.mark.skipif(
    not _local_model_present(),
    reason="local embedding model not downloaded (see docs/onboarding/B_onboarding.md §1)",
)


# ------------------------------------------------------------------ units --

def test_mock_embedding_deterministic_and_token_sensitive():
    from app.rag.embedding import _hash_embedding
    import numpy as np
    a = _hash_embedding("员工手册规定年假 15 天")
    b = _hash_embedding("员工手册规定年假 15 天")
    c = _hash_embedding("完全不同的句子关于数据库查询")
    assert np.allclose(a, b)
    assert not np.allclose(a, c)


def test_bm25_ranking():
    from app.rag.retriever import _BM25Index, StoredChunk
    chunks = [
        StoredChunk(1, "d", "doc", 1, 1, ["doc", "年假"], "年假为 15 天"),
        StoredChunk(2, "d", "doc", 1, 1, ["doc", "提成"], "提成比例为 3%"),
    ]
    idx = _BM25Index.build(chunks)
    hits = idx.search("年假有几天")
    assert hits and hits[0][0] == 0  # 年假 chunk ranks first


def test_citation_shape():
    from app.rag.retriever import ChunkHit, StoredChunk
    chunk = StoredChunk(1, "employee_handbook", "Chinook 唱片员工手册", 2, 2,
                        ["Chinook 唱片员工手册", "假期制度", "年假"], "满五年的员工享有 15 天年假")
    hit = ChunkHit(chunk=chunk, score=0.031)
    cite = hit.citation()
    # contract: trace.md detail.citations[] keys
    assert set(cite) == {"doc", "doc_id", "page", "breadcrumb", "snippet", "score"}
    assert cite["page"] == 2 and "年假" in cite["breadcrumb"]


# ------------------------------------------------------------ integration --

@requires_db
@requires_embedding
def test_rag_pipeline_end_to_end_mock(tmp_path):
    """Real DB + real store + REAL local embedding (dim must match kb_chunk);
    mock LLM only. Ingests a synthetic doc via IR, runs, cleans up."""
    from app.rag.pipeline import RAGPipeline
    from app.ingestion.ir import BlockIR, DocIR
    from app.ingestion import chunker

    pipeline = RAGPipeline(llm=LLMService(Settings(llm_provider="mock"),
                                          _LLMStore(tmp_path / "u.sqlite")))

    doc = DocIR(doc_id="test_rag_doc", name="测试手册", source_path="mem://", pages=1,
                blocks=[
                    BlockIR(id="tb1", page=1, type="heading", text="假期制度", level=1),
                    BlockIR(id="tb2", page=1, type="paragraph",
                            text="入职满一年的员工每年享有 10 天年假，满五年 15 天。"),
                ])
    chunker.chunk_doc(doc)
    embd = pipeline.embedding.embed([c.text for c in doc.chunks])
    pipeline.store.upsert_doc(doc, embd)
    pipeline._loaded = False
    pipeline.ensure_loaded()

    pipeline.llm.register_mock(MockProvider(scripted=[
        "入职满一年每年 10 天年假，满五年 15 天[1]。",
    ]))
    r = pipeline.run("年假有几天？")
    assert r.status == "ok"
    assert "10" in r.answer
    assert r.citations and r.citations[0]["doc"] == "测试手册"
    labels = [c["label"] for c in r.trace["root"]["children"]]
    assert labels == ["rag_search", "rag_generate"]

    # cleanup test doc
    import psycopg
    with psycopg.connect(Settings().database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM kb_doc WHERE doc_id = 'test_rag_doc'")


@requires_db
@requires_embedding
def test_kb_chunk_store_roundtrip(tmp_path):
    from app.rag.embedding import EmbeddingService
    from app.rag.store import KBStore
    from app.ingestion.ir import BlockIR, ChunkIR, DocIR

    emb = EmbeddingService()  # local provider — dim must match the live table
    store = KBStore(dim=emb.dim)
    doc = DocIR(doc_id="store_test_doc", name="S", source_path="mem://", pages=1,
                blocks=[BlockIR(id="sb1", page=1, type="paragraph", text="内容")],
                chunks=[ChunkIR(id="sc1", doc_id="store_test_doc", block_ids=["sb1"],
                                breadcrumb=["S"], page_start=1, page_end=1, text="内容")])
    vec = emb.embed(["内容"])
    store.upsert_doc(doc, vec)
    assert store.doc_content_hash("store_test_doc") == doc.content_hash()
    chunks = store.all_chunks()
    mine = [c for c in chunks if c.doc_id == "store_test_doc"]
    assert len(mine) == 1 and mine[0].embedding is not None
    assert abs(float((mine[0].embedding @ vec[0])) - 1.0) < 1e-5  # vector survived roundtrip

    import psycopg
    with psycopg.connect(Settings().database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM kb_doc WHERE doc_id = 'store_test_doc'")
