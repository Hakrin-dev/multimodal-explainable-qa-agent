"""Formula extraction persistence and ingestion integration tests."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.db.session import test_connection as db_available
from app.formula import store as formula_store
from app.ingestion.ir import BlockIR, ChunkIR, DocIR, FormulaIR

requires_db = pytest.mark.skipif(
    not db_available(),
    reason="live PostgreSQL required",
)


def _formula(
    *,
    formula_id: str,
    doc_id: str,
    rate: float,
) -> FormulaIR:
    return FormulaIR(
        id=formula_id,
        doc_id=doc_id,
        page=1,
        breadcrumb=["测试文档", "计算规则"],
        name="测试公式",
        latex=f"{rate} * S",
        params={
            "S": {
                "desc": "测试金额",
                "unit": "元",
                "source": "user",
            }
        },
    )


@requires_db
def test_replace_for_doc_is_idempotent_and_removes_stale() -> None:
    doc_id = "_test_formula_replace"

    try:
        first = [
            _formula(
                formula_id=f"{doc_id}-f1",
                doc_id=doc_id,
                rate=0.03,
            ),
            _formula(
                formula_id=f"{doc_id}-f2",
                doc_id=doc_id,
                rate=0.02,
            ),
        ]
        formula_store.replace_for_doc(doc_id, first)

        rows = formula_store.list_for_doc(doc_id)
        assert [row.id for row in rows] == [
            f"{doc_id}-f1",
            f"{doc_id}-f2",
        ]

        replacement = [
            _formula(
                formula_id=f"{doc_id}-f1",
                doc_id=doc_id,
                rate=0.05,
            )
        ]
        formula_store.replace_for_doc(
            doc_id,
            replacement,
        )

        rows = formula_store.list_for_doc(doc_id)
        assert len(rows) == 1
        assert rows[0].latex == "0.05 * S"

        formula_store.replace_for_doc(doc_id, [])
        assert formula_store.list_for_doc(doc_id) == []
    finally:
        formula_store.replace_for_doc(doc_id, [])


@requires_db
def test_doc_id_mismatch_does_not_delete_existing_rows() -> None:
    doc_id = "_test_formula_mismatch"

    try:
        original = _formula(
            formula_id=f"{doc_id}-f1",
            doc_id=doc_id,
            rate=0.03,
        )
        formula_store.replace_for_doc(
            doc_id,
            [original],
        )

        mismatched = _formula(
            formula_id="_other_document-f1",
            doc_id="_other_document",
            rate=0.05,
        )

        with pytest.raises(ValueError):
            formula_store.replace_for_doc(
                doc_id,
                [mismatched],
            )

        rows = formula_store.list_for_doc(doc_id)
        assert len(rows) == 1
        assert rows[0].latex == "0.03 * S"
    finally:
        formula_store.replace_for_doc(doc_id, [])


class _FakeLLM:
    def __init__(self):
        self.calls = []
        self.settings = SimpleNamespace(
            formula_extract_enabled=True,
            formula_extract_max_blocks=40,
            formula_extract_max_formulas=20,
        )

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(
            content="""{
              "formulas": [{
                "block_id": "b1",
                "name": "销售提成",
                "latex": "0.03 * S",
                "params": {
                  "S": {
                    "desc": "当月个人销售额",
                    "unit": "元",
                    "source": "db"
                  }
                }
              }]
            }"""
        )


class _FakeEmbedding:
    def embed(self, texts):
        return np.ones(
            (len(texts), 4),
            dtype=np.float32,
        )


class _FakeStore:
    def __init__(self):
        self.saved_doc = None
        self.saved_embeddings = None

    def doc_content_hash(self, doc_id):
        return None

    def update_assessment(self, doc):
        raise AssertionError(
            "new document must not use update_assessment"
        )

    def upsert_doc(self, doc, chunk_embeddings):
        self.saved_doc = doc
        self.saved_embeddings = chunk_embeddings


def test_ingest_document_extracts_before_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import pipeline

    doc = DocIR(
        doc_id="formula_ingest_test",
        name="公式测试文档",
        source_path="mem://formula.pdf",
        pages=1,
        blocks=[
            BlockIR(
                id="h1",
                page=1,
                type="heading",
                text="提成制度",
                level=1,
            ),
            BlockIR(
                id="b1",
                page=1,
                type="paragraph",
                text="销售提成 = 当月个人销售额 × 3%。",
            ),
        ],
    )

    class _Assessment:
        parser = "pymupdf"

        @staticmethod
        def metadata():
            return {"complexity": 1}

    class _Quality:
        @staticmethod
        def metadata():
            return {"quality": {"score": 1.0}}

    def fake_chunk(document):
        document.chunks = [
            ChunkIR(
                id="c1",
                doc_id=document.doc_id,
                block_ids=["b1"],
                breadcrumb=[
                    document.name,
                    "提成制度",
                ],
                page_start=1,
                page_end=1,
                text=document.blocks[1].text,
            )
        ]
        return document.chunks

    monkeypatch.setattr(
        pipeline,
        "assess_quality",
        lambda path: _Quality(),
    )
    monkeypatch.setattr(
        pipeline,
        "assess_pdf",
        lambda path: _Assessment(),
    )
    monkeypatch.setattr(
        pipeline.pdf_ingest,
        "parse_pdf",
        lambda path: doc,
    )
    monkeypatch.setattr(
        pipeline,
        "recover_flat_headings",
        lambda document: 0,
    )
    monkeypatch.setattr(
        pipeline.chunker,
        "chunk_doc",
        fake_chunk,
    )

    llm = _FakeLLM()
    store = _FakeStore()

    result, new_chunks = pipeline.ingest_document(
        "formula.pdf",
        store=store,
        embedding=_FakeEmbedding(),
        llm=llm,
    )

    assert result is doc
    assert new_chunks == 1
    assert len(llm.calls) == 1
    assert len(doc.formulas) == 1
    assert doc.formulas[0].id == "formula_ingest_test-f1"
    assert (
        doc.meta["formula_extraction"]["replace_ready"]
        is True
    )
    assert store.saved_doc is doc
    assert store.saved_embeddings.shape == (1, 4)


def test_formula_extraction_defaults_are_safe() -> None:
    from app.core.config import Settings

    settings = Settings(_env_file=None)

    assert settings.formula_extract_enabled is False
    assert settings.formula_extract_max_blocks == 40
    assert settings.formula_extract_max_formulas == 20


def test_unchanged_document_skips_paid_formula_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import pipeline

    doc = DocIR(
        doc_id="unchanged_formula_doc",
        name="未变化文档",
        source_path="mem://unchanged.pdf",
        pages=1,
        blocks=[
            BlockIR(id="b1", page=1, type="paragraph",
                    text="销售提成 = 销售额 × 3%。"),
        ],
    )

    class _Assessment:
        parser = "pymupdf"

        @staticmethod
        def metadata():
            return {"complexity": 1}

    class _Quality:
        @staticmethod
        def metadata():
            return {"quality": {"score": 1.0}}

    def fake_chunk(document):
        document.chunks = [ChunkIR(
            id="c1", doc_id=document.doc_id, block_ids=["b1"],
            breadcrumb=[document.name], page_start=1, page_end=1,
            text=document.blocks[0].text,
        )]
        return document.chunks

    monkeypatch.setattr(pipeline, "assess_quality", lambda path: _Quality())
    monkeypatch.setattr(pipeline, "assess_pdf", lambda path: _Assessment())
    monkeypatch.setattr(pipeline.pdf_ingest, "parse_pdf", lambda path: doc)
    monkeypatch.setattr(pipeline.chunker, "chunk_doc", fake_chunk)

    class _UnchangedStore:
        def doc_content_hash(self, doc_id):
            return doc.content_hash()

        def update_assessment(self, document):
            return True

        def upsert_doc(self, document, chunk_embeddings):
            raise AssertionError("unchanged document must not be re-embedded")

    llm = _FakeLLM()
    result, new_chunks = pipeline.ingest_document(
        "unchanged.pdf", store=_UnchangedStore(),
        embedding=_FakeEmbedding(), llm=llm,
    )

    assert result is doc
    assert new_chunks == 0
    assert llm.calls == []