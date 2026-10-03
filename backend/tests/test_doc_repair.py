"""W4 document repair and flat hierarchy recovery tests."""

from __future__ import annotations

from pathlib import Path

from app.ingestion import pdf_ingest
from app.ingestion.ir import BlockIR, DocIR
from app.ingestion.quality import assess_quality
from app.ingestion.repair import repair_pdf, simplify_doc_text
from app.ingestion.structure import recover_flat_headings
from scripts.gen_bad_docs import generate_all


def _fixtures(tmp_path: Path) -> dict[str, Path]:
    return {
        item.name: item
        for item in generate_all(tmp_path / "bad")
    }


def test_rotated_scan_is_normalized_without_overwrite(
    tmp_path: Path,
) -> None:
    docs = _fixtures(tmp_path)
    source = docs["bad_rotated_scan.pdf"]
    output = tmp_path / "repaired" / "rotated.pdf"
    original = source.read_bytes()

    result = repair_pdf(source, output)

    assert source.read_bytes() == original
    assert result.before.orientation == 90
    assert result.before.rotated_pages == (1,)
    assert result.after.orientation == 0
    assert result.after.rotated_pages == ()
    assert "orientation_normalized" in result.actions


def test_blurred_scan_clarity_improves(
    tmp_path: Path,
) -> None:
    docs = _fixtures(tmp_path)
    source = docs["bad_blurred_traditional.pdf"]
    output = tmp_path / "repaired" / "blurred.pdf"

    result = repair_pdf(source, output)

    assert result.before.clarity < 0.35
    assert result.after.clarity > result.before.clarity
    assert "clarity_enhanced" in result.actions


def test_healthy_native_pdf_keeps_text_layer(
    tmp_path: Path,
) -> None:
    docs = _fixtures(tmp_path)
    source = docs["bad_missing_structure.pdf"]
    output = tmp_path / "repaired" / "structure.pdf"

    before_bytes = source.read_bytes()
    result = repair_pdf(source, output)

    assert result.actions == ()
    assert result.before.has_text_layer is True
    assert result.after.has_text_layer is True
    assert output.read_bytes() == before_bytes


def test_traditional_ocr_text_is_normalized() -> None:
    doc = DocIR(
        doc_id="traditional",
        name="合作方結算說明",
        source_path="mem://traditional",
        pages=1,
        blocks=[
            BlockIR(
                id="b1",
                page=1,
                type="paragraph",
                text="財務人員完成對帳並歸檔。",
            )
        ],
    )

    changed = simplify_doc_text(doc)

    assert changed == 2
    assert doc.name == "合作方结算说明"
    assert doc.blocks[0].text == "财务人员完成对帐并归档。"
    assert (
        doc.meta["repair"]["traditional_to_simplified"]
        ["changed_items"]
        == 2
    )


def test_flat_native_document_recovers_title_and_h1(
    tmp_path: Path,
) -> None:
    docs = _fixtures(tmp_path)
    doc = pdf_ingest.parse_pdf(
        docs["bad_missing_structure.pdf"]
    )

    before = [
        block
        for block in doc.blocks
        if block.type == "heading"
        and 1 <= block.level <= 4
    ]
    assert before == []

    changed = recover_flat_headings(doc)

    headings = [
        block.text
        for block in doc.blocks
        if block.type == "heading"
        and block.level == 1
    ]

    assert changed >= 7
    assert headings == [
        "适用范围",
        "受理时限",
        "证据要求",
        "升级路径",
        "最终复核",
        "归档要求",
    ]
    assert doc.blocks[0].level == 0
    assert doc.blocks[0].meta["is_title"] is True


def test_existing_hierarchy_is_not_rewritten() -> None:
    doc = DocIR(
        doc_id="existing",
        name="现有文档",
        source_path="mem://existing",
        pages=1,
        blocks=[
            BlockIR(
                id="b1",
                page=1,
                type="heading",
                text="已有章节",
                level=1,
            ),
            BlockIR(
                id="b2",
                page=1,
                type="paragraph",
                text="已有正文。",
            ),
        ],
    )

    assert recover_flat_headings(doc) == 0
    assert doc.blocks[0].level == 1
    assert "structure_recovery" not in doc.meta
