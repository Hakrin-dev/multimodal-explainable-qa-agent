"""Reproducible native/scanned/mixed PDF routing, without OCR or GPU."""

from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pymupdf
import pytest

from app.ingestion import chunker, pdf_ingest
from app.ingestion.complexity import (
    ComplexityAssessment,
    assess_pdf,
    route_parser,
)
from app.ingestion.parsers.mineru import MinerUError
from app.rag.pipeline import ingest_document


def make_pdf(path, *, pages=1, scan=False, mixed=False, table=False, rotation=0):
    with pymupdf.open() as source:
        page = source.new_page(width=300, height=400)
        page.insert_text((30, 50), "Annual leave is 15 days.")
        image = page.get_pixmap().tobytes("png")
    with pymupdf.open() as pdf:
        for index in range(pages):
            page = pdf.new_page(width=300, height=400)
            if scan or (mixed and index == pages - 1):
                page.insert_image(page.rect, stream=image)
            else:
                page.insert_text((30, 50), "Annual leave is 15 days.")
            if table:
                for x in (30, 130, 230):
                    page.draw_line((x, 100), (x, 180))
                for y in (100, 140, 180):
                    page.draw_line((30, y), (230, y))
                for x, y, text in (
                    (40, 125, "A"),
                    (140, 125, "B"),
                    (40, 165, "10"),
                    (140, 165, "20"),
                ):
                    page.insert_text((x, y), text)
            page.set_rotation(rotation)
        pdf.save(path)
    return path


@pytest.mark.parametrize(
    "level,parser",
    [(1, "pymupdf"), (2, "pymupdf"), (3, "mineru"), (4, "mineru"), (5, "mineru")],
)
def test_route_levels(level, parser):
    assert route_parser(level) == parser


@pytest.mark.parametrize("level", [0, 6, -1, True, 2.5, "3"])
def test_route_rejects_invalid_level(level):
    with pytest.raises(ValueError):
        route_parser(level)


@pytest.mark.parametrize(
    "pages,ratio,level", [(20, 0.3, 1), (21, 0.3, 2), (20, 0.30001, 2), (21, 0.31, 3)]
)
def test_rule_boundaries(pages, ratio, level):
    result = ComplexityAssessment(pages, (), (), ratio)
    assert result.level == level
    assert sum(result.contributions.values()) + 1 == level
    assert result.metadata()["complexity_details"]["warnings"] == []


def test_normal_table_and_long_pdf(tmp_path):
    normal = assess_pdf(make_pdf(tmp_path / "normal.pdf"))
    assert normal.level == 1 and normal.parser == "pymupdf"
    assert normal.textless_pages == normal.table_pages == ()
    table = assess_pdf(make_pdf(tmp_path / "table.pdf", table=True))
    assert table.table_pages == (1,) and table.level == 2
    long = assess_pdf(make_pdf(tmp_path / "long.pdf", pages=21, table=True))
    assert long.level == 3 and long.parser == "mineru"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_scan_and_mixed_pdf_are_not_silently_partial(tmp_path, rotation):
    path = make_pdf(tmp_path / "scan.pdf", scan=True, rotation=rotation)
    scan = assess_pdf(path)
    assert scan == assess_pdf(path)
    assert scan.textless_pages == (1,) and scan.image_ratio == pytest.approx(1)
    assert scan.level == 5 and scan.parser == "mineru"
    assert scan.metadata()["complexity_details"]["warnings"] == [
        "postprocessing_required"
    ]
    mixed = assess_pdf(make_pdf(tmp_path / "mixed.pdf", pages=2, mixed=True))
    assert mixed.textless_pages == (2,)
    assert mixed.image_ratio == pytest.approx(0.5) and mixed.level == 5


def test_overlapping_images_use_visible_union(tmp_path):
    path = tmp_path / "overlap.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=100, height=100)
        page.insert_text((10, 90), "Text", fontsize=8)
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
        image.clear_with(120)
        for _ in range(2):
            page.insert_image(pymupdf.Rect(0, 0, 50, 50), pixmap=image)
        pdf.save(path)
    result = assess_pdf(path)
    assert result.image_ratio == pytest.approx(0.25)
    assert result.level == 1


def test_blank_corrupt_and_encrypted_pdf(tmp_path):
    blank = tmp_path / "blank.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(blank)
        pdf.save(
            tmp_path / "encrypted.pdf",
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="user",
        )
    result = assess_pdf(blank)
    assert result.level == 4 and result.textless_pages == (1,)
    with pytest.raises(ValueError, match="unencrypted"):
        assess_pdf(tmp_path / "encrypted.pdf")
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a PDF")
    with pytest.raises(pymupdf.FileDataError):
        assess_pdf(corrupt)


def test_mineru_failure_stops_before_model_or_database(
    tmp_path,
    monkeypatch,
):
    from app.rag import pipeline

    model = Mock(side_effect=AssertionError("model must not load"))
    store = Mock(side_effect=AssertionError("DB must not open"))
    mineru = Mock(side_effect=MinerUError("simulated MinerU failure"))

    monkeypatch.setattr(pipeline, "get_embedding_service", model)
    monkeypatch.setattr(pipeline, "KBStore", store)
    monkeypatch.setattr(pipeline.mineru_parser, "parse_pdf", mineru)

    path = make_pdf(tmp_path / "scan.pdf", scan=True)

    with pytest.raises(MinerUError, match="simulated"):
        ingest_document(str(path), force=True)

    mineru.assert_called_once_with(str(path))
    model.assert_not_called()
    store.assert_not_called()


def test_ingest_preserves_frozen_ir_and_refreshes_metadata(tmp_path):
    path = make_pdf(tmp_path / "native.pdf")
    original = pdf_ingest.parse_pdf(path)
    chunker.chunk_doc(original)
    store, embedding = Mock(), Mock()
    store.doc_content_hash.return_value = original.content_hash()
    store.update_assessment.return_value = True
    doc, added = ingest_document(str(path), store=store, embedding=embedding)
    assert added == 0
    assert doc.blocks == original.blocks and doc.chunks == original.chunks
    assert doc.content_hash() == original.content_hash()
    assert "content_hash" not in doc.meta and doc.meta["complexity"] == 1
    assert doc.parser == "pymupdf"
    embedding.embed.assert_not_called()
    store.upsert_doc.assert_not_called()
    store.update_assessment.assert_called_once_with(doc)


def test_new_ingest_persists_assessment(tmp_path):
    path = make_pdf(tmp_path / "new.pdf")
    store, embedding = Mock(), Mock()
    store.doc_content_hash.return_value = None
    embedding.embed.return_value = [[1.0, 0.0]]
    doc, added = ingest_document(str(path), store=store, embedding=embedding)
    assert added == len(doc.chunks) == 1
    assert doc.meta["complexity_details"]["selected_parser"] == "pymupdf"
    store.upsert_doc.assert_called_once()
    assert store.upsert_doc.call_args.args[0] is doc


@pytest.mark.parametrize(
    "filename",
    ["employee_handbook", "sales_review_2025", "service_sop", "product_operations"],
)
def test_repository_normal_pdfs(filename):
    path = Path(__file__).resolve().parents[2] / "data/docs_raw" / f"{filename}.pdf"
    result = assess_pdf(path)
    assert result.level == 1 and result.parser == "pymupdf"


def test_assessment_database_refresh_preserves_chunks(
    tmp_path,
    monkeypatch,
):
    from app.db.session import get_conn, test_connection
    from app.rag.embedding import EmbeddingService
    from app.rag.store import KBStore

    if not test_connection():
        pytest.skip("live PostgreSQL required")
    path = make_pdf(tmp_path / f"test_complexity_{uuid4().hex}.pdf")
    embedding = EmbeddingService()
    store = KBStore(dim=embedding.dim)
    try:
        doc, added = ingest_document(str(path), store=store, embedding=embedding)
        assert added == 1
        with get_conn(readonly=False) as conn, conn.cursor() as cur:
            cur.execute("SELECT meta FROM kb_doc WHERE doc_id=%s", (doc.doc_id,))
            assert cur.fetchone()[0]["complexity"] == 1
            cur.execute(
                "UPDATE kb_doc SET meta=%s::jsonb WHERE doc_id=%s",
                ('{"external":true}', doc.doc_id),
            )
            cur.execute(
                "SELECT chunk_id, embedding::text FROM kb_chunk WHERE doc_id=%s",
                (doc.doc_id,),
            )
            before = cur.fetchall()
        no_embedding = Mock(side_effect=AssertionError("must reuse existing vectors"))
        doc2, added = ingest_document(str(path), store=store, embedding=no_embedding)
        assert added == 0 and doc2.content_hash() == doc.content_hash()
        no_embedding.embed.assert_not_called()
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT meta FROM kb_doc WHERE doc_id=%s", (doc.doc_id,))
            meta = cur.fetchone()[0]
            assert meta["external"] is True and meta["complexity"] == 1
            cur.execute(
                "SELECT chunk_id, embedding::text FROM kb_chunk WHERE doc_id=%s",
                (doc.doc_id,),
            )
            assert cur.fetchall() == before
        # A rejected replacement must preserve an already indexed document.
        path.unlink()
        make_pdf(path, scan=True)
        from app.rag import pipeline

        mineru = Mock(side_effect=MinerUError("simulated MinerU failure"))
        monkeypatch.setattr(pipeline.mineru_parser, "parse_pdf", mineru)

        with pytest.raises(MinerUError, match="simulated"):
            ingest_document(
                str(path),
                store=store,
                embedding=no_embedding,
                force=True,
            )
        assert store.doc_content_hash(doc.doc_id) == doc.content_hash()
    finally:
        with get_conn(readonly=False) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM kb_doc WHERE doc_id=%s", (path.stem,))
