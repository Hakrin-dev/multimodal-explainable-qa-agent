"""Ingestion tests: parse → heading detection → chunking → IR.

Uses the generated PDFs + kb_doc_content as ground truth (integration-lite:
needs data/docs_raw PDFs, no DB).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.ingestion import chunker, pdf_ingest  # noqa: E402
from app.ingestion.ir import BlockIR, ChunkIR, DocIR  # noqa: E402
from scripts.kb_doc_content import EMPLOYEE_HANDBOOK  # noqa: E402

PDF = BACKEND.parent / "data/docs_raw/employee_handbook.pdf"
requires_pdf = pytest.mark.skipif(not PDF.exists(), reason="run scripts/gen_kb_docs.py first")


def test_ir_roundtrip_and_hash():
    doc = DocIR(doc_id="t", name="T", source_path="x", pages=1,
                blocks=[BlockIR(id="t-b1", page=1, type="heading", text="H", level=1)],
                chunks=[ChunkIR(id="t-c1", doc_id="t", block_ids=["t-b1"],
                                breadcrumb=["T", "H"], page_start=1, page_end=1, text="H")])
    d = doc.to_dict()
    assert d["blocks"][0]["type"] == "heading"
    assert doc.content_hash() == doc.content_hash()
    doc2 = DocIR(doc_id="t", name="T", source_path="x", pages=1,
                 blocks=[BlockIR(id="t-b1", page=1, type="heading", text="H", level=1)])
    assert doc.content_hash() == doc2.content_hash()
    doc2.blocks[0].text = "H2"
    assert doc.content_hash() != doc2.content_hash()


def test_assemble_text_joining_rules():
    h = BlockIR(id="a", page=1, type="heading", text="标题", level=2)
    p1 = BlockIR(id="b", page=1, type="paragraph", text="句子没有结束")
    p2 = BlockIR(id="c", page=1, type="paragraph", text="5 天，事假期间不计薪。")
    p3 = BlockIR(id="d", page=1, type="paragraph", text="新句子。")
    text = chunker._assemble_text([h, p1, p2, p3])
    assert text == "标题\n句子没有结束5 天，事假期间不计薪。\n新句子。"


@requires_pdf
def test_heading_detection_matches_ground_truth():
    doc = pdf_ingest.parse_pdf(PDF)
    detected = [(b.level, b.text) for b in doc.blocks if b.type == "heading"]
    expected = [tuple(s) for s in EMPLOYEE_HANDBOOK["sections"]]
    missing = [e for e in expected if e not in detected]
    assert not missing, f"headings not detected: {missing}"
    # doc title detected separately as level 0
    assert (0, "Chinook 唱片员工手册") in detected
    assert len(detected) == len(expected) + 1  # + title


@requires_pdf
def test_chunk_breadcrumb_and_no_cross_h1():
    doc = pdf_ingest.parse_pdf(PDF)
    chunks = chunker.chunk_doc(doc)
    assert chunks, "no chunks produced"

    # breadcrumb depth == doc title + heading levels
    for c in chunks:
        assert c.breadcrumb[0] == doc.name
        assert 1 <= len(c.breadcrumb) <= 3

    # find the 年假 chunk and verify breadcrumb
    yj = [c for c in chunks if "年假" in c.breadcrumb[-1]]
    assert yj and yj[0].breadcrumb == ["Chinook 唱片员工手册", "假期制度", "年假"]

    # chunks must not cross H1 boundaries: every chunk contains ≤1 H1 heading block
    h1_ids = {b.id for b in doc.blocks if b.type == "heading" and b.level == 1}
    for c in chunks:
        assert len(h1_ids & set(c.block_ids)) <= 1

    # hard-wrapped facts stay joined (the smoke regression) — whitespace-insensitive
    facts = "".join(c.text for c in chunks)
    assert "".join("事假上限为 5 天".split()) in "".join(facts.split())


@requires_pdf
def test_parse_pdf_records_geometry():
    doc = pdf_ingest.parse_pdf(PDF)
    assert doc.pages >= 1
    assert all(b.page >= 1 for b in doc.blocks)
    assert all(len(b.bbox) == 4 for b in doc.blocks)
    assert doc.meta["n_blocks"] == len(doc.blocks)
