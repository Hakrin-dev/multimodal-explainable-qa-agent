"""PDF parsing → DocIR (W1: PyMuPDF direct extraction, native PDFs only).

Heading detection v0 (简化版 #7 的地基): font-size statistics per document —
size ≥ body_median × 1.30 → H1, ≥ × 1.10 → H2. W2 upgrade path: signal
fusion (font size + numbering patterns + TOC cross-check + LLM assist) for
broken-TOC documents (#7 完整版, B).
"""

from __future__ import annotations

import re
from pathlib import Path
from statistics import median

import pymupdf

from .ir import BlockIR, DocIR

_NUM_PREFIX = re.compile(r"^\d+(\.\d+)*[、.\s]")


def _block_text(block: dict) -> str:
    return "".join(span["text"] for line in block["lines"] for span in line["spans"]).strip()


def _block_font_size(block: dict) -> float:
    sizes = [span["size"] for line in block["lines"] for span in line["spans"]]
    return median(sizes) if sizes else 0.0


def parse_pdf(path: str | Path, doc_id: str | None = None,
              name: str | None = None) -> DocIR:
    path = Path(path)
    doc_id = doc_id or path.stem

    with pymupdf.open(path) as pdf:
        # human-readable title from metadata (fall back to stem)
        title = (pdf.metadata or {}).get("title") or ""
        name = name or title.strip() or path.stem
        raw: list[BlockIR] = []
        sizes: list[float] = []
        for pno, page in enumerate(pdf, start=1):
            for b in page.get_text("dict")["blocks"]:
                if b.get("type") != 0:  # text blocks only (v0)
                    continue
                text = _block_text(b)
                if not text:
                    continue
                fs = _block_font_size(b)
                sizes.append(fs)
                raw.append(BlockIR(
                    id=f"{doc_id}-b{len(raw) + 1}", page=pno, type="paragraph",
                    text=text, bbox=list(b["bbox"]), font_size=round(fs, 2),
                ))
        pages = pdf.page_count

    body_size = median(sizes) if sizes else 10.5
    for blk in raw:
        if blk.font_size and blk.font_size >= body_size * 1.30:
            blk.type, blk.level = "heading", 1
        elif blk.font_size and blk.font_size >= body_size * 1.10:
            blk.type, blk.level = "heading", 2
        else:
            blk.type = "paragraph"

    # document title: the largest-font block (level 0 — not a section heading)
    if raw:
        title = max(raw, key=lambda b: (b.font_size, -b.page))
        if title.type == "heading":
            title.level = 0
            title.meta["is_title"] = True

    # title = first biggest heading-ish block (page 1, largest size)
    doc = DocIR(doc_id=doc_id, name=name, source_path=str(path), pages=pages,
                parser="pymupdf",
                meta={"body_font_size": round(body_size, 2), "n_blocks": len(raw)})
    doc.blocks = raw
    return doc


def has_text_layer(path: str | Path) -> bool:
    """质量评估 #9 的第一个检测器：有无文本层（扫描件判定）。"""
    with pymupdf.open(path) as pdf:
        for page in pdf:
            if page.get_text().strip():
                return True
    return False
