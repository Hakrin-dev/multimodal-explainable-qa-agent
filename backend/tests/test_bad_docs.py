"""Tests for the three deliberately defective W2 PDF fixtures."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from app.ingestion import pdf_ingest
from app.ingestion.complexity import assess_pdf
from app.ingestion.quality import assess_quality
from scripts.gen_bad_docs import generate_all


@pytest.fixture()
def bad_docs(tmp_path: Path) -> dict[str, Path]:
    paths = generate_all(tmp_path / "docs_bad")
    return {path.name: path for path in paths}


def test_generator_creates_three_valid_pdfs(
    bad_docs: dict[str, Path],
) -> None:
    assert set(bad_docs) == {
        "bad_missing_structure.pdf",
        "bad_rotated_scan.pdf",
        "bad_blurred_traditional.pdf",
    }

    for path in bad_docs.values():
        assert path.exists()
        assert path.stat().st_size > 1_000

        with pymupdf.open(path) as document:
            assert document.is_pdf
            assert document.page_count == 1


def test_missing_structure_is_native_but_has_no_section_hierarchy(
    bad_docs: dict[str, Path],
) -> None:
    path = bad_docs["bad_missing_structure.pdf"]

    quality = assess_quality(path)
    complexity = assess_pdf(path)
    document = pdf_ingest.parse_pdf(path)

    section_headings = [
        block
        for block in document.blocks
        if block.type == "heading" and block.level in {1, 2, 3, 4}
    ]

    assert quality.has_text_layer is True
    assert quality.textless_pages == ()
    assert quality.rotated_pages == ()
    assert complexity.parser == "pymupdf"
    assert section_headings == []


def test_rotated_scan_routes_to_mineru(
    bad_docs: dict[str, Path],
) -> None:
    path = bad_docs["bad_rotated_scan.pdf"]

    quality = assess_quality(path)
    complexity = assess_pdf(path)

    assert quality.has_text_layer is False
    assert quality.textless_pages == (1,)
    assert quality.orientation == 90
    assert quality.rotated_pages == (1,)
    assert "textless_pages" in quality.warnings
    assert "rotated_pages" in quality.warnings

    assert complexity.level == 5
    assert complexity.parser == "mineru"
    assert complexity.textless_pages == (1,)
    assert complexity.image_ratio > 0.95


def test_blurred_traditional_scan_routes_to_mineru(
    bad_docs: dict[str, Path],
) -> None:
    path = bad_docs["bad_blurred_traditional.pdf"]

    quality = assess_quality(path)
    complexity = assess_pdf(path)

    assert quality.has_text_layer is False
    assert quality.textless_pages == (1,)
    assert quality.clarity < 0.35
    assert "low_clarity" in quality.warnings

    # Traditional characters live inside the image. Without OCR, the
    # lightweight detector must not pretend it extracted them.
    assert quality.traditional_chars is False
    assert "traditional_text" not in quality.warnings

    assert complexity.level == 5
    assert complexity.parser == "mineru"
    assert complexity.textless_pages == (1,)
    assert complexity.image_ratio > 0.95


def test_quality_and_complexity_are_deterministic(
    bad_docs: dict[str, Path],
) -> None:
    for path in bad_docs.values():
        assert assess_quality(path) == assess_quality(path)
        assert assess_pdf(path) == assess_pdf(path)
