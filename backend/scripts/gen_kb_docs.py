"""Render KB docs to PDF (reportlab + WenQuanYi Zen Hei).

Usage: python scripts/gen_kb_docs.py [--out ../data/docs_raw]

The generator is deterministic (same content -> same layout), so ingestion
tests can rely on stable page/breadcrumb structure. B extends this in W2
with the 3 deliberately-broken docs (目录丢失 / 页面颠倒扫描 / 模糊+繁体).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from scripts.kb_doc_content import ALL_DOCS  # noqa: E402

FONT = "WQY"
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
# matches pdf_ingest heading thresholds (body < H2 < H1 by size)
SIZE_BODY, SIZE_H2, SIZE_H1, SIZE_TITLE = 10.5, 12.0, 14.0, 18.0


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle("title", fontName=FONT, fontSize=SIZE_TITLE,
                                leading=26, spaceAfter=18, alignment=1),
        "h1": ParagraphStyle("h1", fontName=FONT, fontSize=SIZE_H1,
                             leading=20, spaceBefore=14, spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName=FONT, fontSize=SIZE_H2,
                             leading=17, spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=SIZE_BODY,
                               leading=16.5, firstLineIndent=21, spaceAfter=6),
    }


def render(doc: dict, out_path: Path) -> None:
    styles = _styles()
    story = [Paragraph(doc["name"], styles["title"])]
    for level, heading in doc["sections"]:
        story.append(Paragraph(heading, styles[f"h{level}"]))
        story.append(Paragraph(doc["body"][heading], styles["body"]))
    SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=2.2 * cm, rightMargin=2.2 * cm,
        topMargin=2.2 * cm, bottomMargin=2.0 * cm,
        title=doc["name"],
    ).build(story)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BACKEND.parent / "data/docs_raw"))
    args = ap.parse_args()

    pdfmetrics.registerFont(TTFont(FONT, FONT_PATH, subfontIndex=0))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for doc in ALL_DOCS:
        p = out / f"{doc['doc_id']}.pdf"
        render(doc, p)
        print(f"  {p.name:<28} {p.stat().st_size:>7} bytes")
    print(f"✓ {len(ALL_DOCS)} docs rendered to {out}")


if __name__ == "__main__":
    main()
