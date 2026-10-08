"""Generate scaled synthetic corpora for the W5 efficiency matrix (P1).

Synthetic docs carry a unique marker fact so retrieval recall stays measurable
at every scale. Layout mirrors the real corpus (H1/H2, 1~2 chunks per doc) so
ingestion cost scales linearly and comparably.

Usage: python scripts/gen_bench_corpus.py --count 1000 --out data/docs_bench/1000
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
from reportlab.platypus import Paragraph, SimpleDocTemplate

BACKEND = Path(__file__).resolve().parents[1]
FONT = "WQY"
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"

TOPICS = ["销售制度", "客户管理", "库存与补货", "营销活动", "财务报销", "产品运营",
          "售后服务", "供应链", "数据治理", "培训体系"]


def gen_one(path: Path, idx: int) -> None:
    topic = TOPICS[idx % len(TOPICS)]
    marker = f"编号 {idx:04d}"
    code = f"SOP-{idx:04d}"
    styles = {
        "h1": ParagraphStyle("h1", fontName=FONT, fontSize=14, leading=20, spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName=FONT, fontSize=12, leading=17, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=16.5,
                               firstLineIndent=21, spaceAfter=6),
    }
    story = [
        Paragraph(f"{topic}操作手册（{marker}）", styles["h1"]),
        Paragraph("适用范围", styles["h2"]),
        Paragraph(f"本手册适用于{topic}相关岗位，文档编号 {code}，由运营管理部维护。", styles["body"]),
        Paragraph("关键指标", styles["h2"]),
        Paragraph(f"月度回访覆盖率为 {60 + idx % 30}%，异常处理时限为 {1 + idx % 5} 个工作日。",
                  styles["body"]),
    ]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2.2 * cm, rightMargin=2.2 * cm,
                      topMargin=2.2 * cm, bottomMargin=2.0 * cm).build(story)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pdfmetrics.registerFont(TTFont(FONT, FONT_PATH, subfontIndex=0))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i in range(args.count):
        gen_one(out / f"bench_{i:04d}.pdf", i)
    print(f"✓ generated {args.count} docs → {out}")


if __name__ == "__main__":
    main()
