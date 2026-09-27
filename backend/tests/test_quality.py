"""Tests for lightweight PDF quality assessment."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pymupdf
import pytest
from app.ingestion.quality import _contains_traditional, assess_quality
from app.rag.pipeline import ingest_document
from PIL import Image, ImageDraw, ImageFilter


def _make_text_pdf(
    path: Path,
    *,
    rotation: int = 0,
    pages: int = 1,
) -> Path:
    document = pymupdf.open()

    for _ in range(pages):
        page = document.new_page(width=400, height=500)
        for row in range(12):
            page.insert_text(
                (40, 50 + row * 28),
                f"Quality assessment sample line {row}.",
                fontsize=12,
            )

        if rotation:
            page.set_rotation(rotation)

    document.save(path)
    document.close()
    return path


def _scan_image(*, blurred: bool = False) -> bytes:
    image = Image.new("L", (800, 1000), color=255)
    draw = ImageDraw.Draw(image)

    for y in range(60, 940, 55):
        draw.rectangle((60, y, 740, y + 8), fill=0)
        draw.text(
            (70, y + 12),
            "Scanned document quality sample",
            fill=0,
        )

    if blurred:
        image = image.filter(ImageFilter.GaussianBlur(radius=6))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _make_scan_pdf(path: Path, *, blurred: bool = False) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=400, height=500)
    page.insert_image(page.rect, stream=_scan_image(blurred=blurred))
    document.save(path)
    document.close()
    return path


def test_normal_pdf_quality_is_deterministic(tmp_path: Path) -> None:
    path = _make_text_pdf(tmp_path / "normal.pdf")

    first = assess_quality(path)
    second = assess_quality(path)

    assert first == second
    assert first.pages == 1
    assert first.orientation == 0
    assert first.has_text_layer is True
    assert first.textless_pages == ()
    assert first.rotated_pages == ()
    assert 0.0 <= first.clarity <= 1.0
    assert 0.0 <= first.score <= 1.0


def test_rotation_is_detected(tmp_path: Path) -> None:
    path = _make_text_pdf(tmp_path / "rotated.pdf", rotation=90)
    result = assess_quality(path)

    assert result.orientation == 90
    assert result.rotated_pages == (1,)
    assert "rotated_pages" in result.warnings
    assert result.page_reports[0]["orientation"] == 90


def test_scan_has_no_native_text_layer(tmp_path: Path) -> None:
    path = _make_scan_pdf(tmp_path / "scan.pdf")
    result = assess_quality(path)

    assert result.has_text_layer is False
    assert result.textless_pages == (1,)
    assert "textless_pages" in result.warnings
    assert result.page_reports[0]["has_text_layer"] is False


def test_blur_reduces_clarity(tmp_path: Path) -> None:
    sharp_path = _make_scan_pdf(tmp_path / "sharp.pdf")
    blurred_path = _make_scan_pdf(tmp_path / "blurred.pdf", blurred=True)

    sharp = assess_quality(sharp_path)
    blurred = assess_quality(blurred_path)

    assert sharp.clarity > blurred.clarity
    assert sharp.score > blurred.score


def test_traditional_character_signal() -> None:
    assert _contains_traditional("這是一份繁體文件")
    assert _contains_traditional("掃描檔案")
    assert _contains_traditional("銷售數據報告")

    assert not _contains_traditional("这是一份简体文件")
    assert not _contains_traditional("活动方案须明确")
    assert not _contains_traditional("扫描文件并记录在案")
    assert not _contains_traditional("English text only")


def test_metadata_contract(tmp_path: Path) -> None:
    path = _make_text_pdf(tmp_path / "metadata.pdf")
    result = assess_quality(path)
    metadata = result.metadata()

    assert set(metadata) == {"quality"}

    quality = metadata["quality"]
    assert quality["version"] == "rules-v1"
    assert quality["orientation"] == 0
    assert quality["has_text_layer"] is True
    assert isinstance(quality["page_reports"], list)
    assert isinstance(quality["warnings"], list)
    assert 0.0 <= quality["score"] <= 1.0


def test_corrupt_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"this is not a valid PDF")

    with pytest.raises(ValueError, match="unable to open PDF"):
        assess_quality(path)


def test_encrypted_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "encrypted.pdf"

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 50), "encrypted test")
    document.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    document.close()

    with pytest.raises(ValueError, match="encrypted PDF"):
        assess_quality(path)


def test_pipeline_attaches_quality_metadata(tmp_path: Path) -> None:
    path = _make_text_pdf(tmp_path / "pipeline_quality.pdf")

    store = Mock()
    store.doc_content_hash.return_value = None

    embedding = Mock()
    embedding.dim = 3
    embedding.embed.return_value = [[1.0, 0.0, 0.0]]

    doc, added = ingest_document(
        str(path),
        store=store,
        embedding=embedding,
    )

    assert added == len(doc.chunks)
    assert added > 0
    assert "quality" in doc.meta
    assert "complexity" in doc.meta
    assert doc.meta["quality"]["has_text_layer"] is True

    embedding.embed.assert_called_once()
    store.upsert_doc.assert_called_once()
