"""W5 knowledge-base corpus packaging regression tests."""

from __future__ import annotations

from pathlib import Path

import pymupdf
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.ingestion import pdf_ingest
from scripts.gen_kb_docs import FONT, FONT_PATH, render
from scripts.kb_doc_content import ALL_DOCS


EXPECTED_DOC_IDS = {
    "employee_handbook",
    "sales_review_2025",
    "service_sop",
    "product_operations",
    "supplier_agreement",
    "marketing_campaign_review",
    "finance_policy",
    "copyright_policy",
    "customer_membership",
    "playlist_curation",
}


def _compact(text: str) -> str:
    return "".join(text.split())


def _register_font() -> None:
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont(
                FONT,
                FONT_PATH,
                subfontIndex=0,
            )
        )


def test_corpus_has_ten_valid_unique_documents() -> None:
    assert len(ALL_DOCS) == 10

    doc_ids = [doc["doc_id"] for doc in ALL_DOCS]
    assert set(doc_ids) == EXPECTED_DOC_IDS
    assert len(doc_ids) == len(set(doc_ids))

    for doc in ALL_DOCS:
        sections = doc["sections"]
        headings = [heading for _, heading in sections]

        assert doc["name"].strip()
        assert headings
        assert len(headings) == len(set(headings))
        assert all(level in {1, 2} for level, _ in sections)
        assert set(headings) == set(doc["body"])
        assert all(doc["body"][heading].strip() for heading in headings)

        for table_heading, rows in doc.get("tables", {}).items():
            assert table_heading in headings
            assert len(rows) >= 2
            width = len(rows[0])
            assert width >= 2
            assert all(len(row) == width for row in rows)


def test_all_documents_render_and_preserve_headings(
    tmp_path: Path,
) -> None:
    _register_font()

    for spec in ALL_DOCS:
        path = tmp_path / f"{spec['doc_id']}.pdf"
        render(spec, path)

        assert path.is_file()
        assert path.stat().st_size > 1000

        parsed = pdf_ingest.parse_pdf(path)

        actual_headings = {
            (block.level, block.text)
            for block in parsed.blocks
            if block.type == "heading"
        }
        expected_headings = {
            tuple(section)
            for section in spec["sections"]
        }

        assert expected_headings <= actual_headings

        with pymupdf.open(path) as document:
            assert len(document) >= 1
            text = "\n".join(
                page.get_text()
                for page in document
            )

        assert spec["name"] in text


def test_structured_tables_are_present_in_pdf_text(
    tmp_path: Path,
) -> None:
    _register_font()

    expected = {
        "supplier_agreement": [
            "供应商等级",
            "15 天",
            "30 天",
            "45 天",
        ],
        "finance_policy": [
            "单笔金额",
            "不超过 2000 元",
            "超过 10000 元",
        ],
        "customer_membership": [
            "会员等级",
            "达到 500 元",
            "达到 1500 元",
            "达到 5000 元",
        ],
    }

    docs = {
        doc["doc_id"]: doc
        for doc in ALL_DOCS
    }

    for doc_id, facts in expected.items():
        path = tmp_path / f"{doc_id}.pdf"
        render(docs[doc_id], path)

        with pymupdf.open(path) as document:
            text = "\n".join(
                page.get_text()
                for page in document
            )

        compact = _compact(text)

        for fact in facts:
            assert _compact(fact) in compact


def test_w5_retrieval_cases_cover_new_documents() -> None:
    import json

    case_path = (
        Path(__file__).resolve().parents[1]
        / "eval"
        / "cases"
        / "rag_corpus_w5.jsonl"
    )
    cases = [
        json.loads(line)
        for line in case_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    expected_docs = {
        "supplier_agreement",
        "marketing_campaign_review",
        "finance_policy",
        "copyright_policy",
        "customer_membership",
        "playlist_curation",
    }

    assert len(cases) == 6
    assert len({
        case["id"]
        for case in cases
    }) == 6
    assert {
        case["expected_doc_id"]
        for case in cases
    } == expected_docs
    assert all(
        case["expected_facts"]
        for case in cases
    )
