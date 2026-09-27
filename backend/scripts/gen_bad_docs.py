"""Generate the three deliberately defective W2 PDF fixtures.

The output documents are isolated from data/docs_raw so normal ingestion does
not index them before the repair/parser-routing workflow explicitly requests it.

Fixtures:
- bad_missing_structure.pdf: native text, but no reliable heading hierarchy;
- bad_rotated_scan.pdf: image-only scan with 90-degree PDF rotation metadata;
- bad_blurred_traditional.pdf: blurred image-only traditional-Chinese scan.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent

FONT_NAME = "WQY"
FONT_PATH = Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
PAGE_PIXELS = (1240, 1754)

BAD_DOCS = {
    "bad_missing_structure": "结构缺失的退款争议处理说明",
    "bad_rotated_scan": "旋转扫描的仓库安全巡检说明",
    "bad_blurred_traditional": "模糊繁體合作方結算說明",
}


def _require_font() -> None:
    if not FONT_PATH.exists():
        raise FileNotFoundError(
            f"Chinese font not found: {FONT_PATH}; "
            "install fonts-wqy-zenhei before generating fixtures"
        )


def _pil_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size=size, index=0)


def _write_image_pdf(
    image: Image.Image,
    output: Path,
    *,
    title: str,
    rotation: int = 0,
) -> None:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=False)

    document = pymupdf.open()
    page = document.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=buffer.getvalue())

    if rotation:
        page.set_rotation(rotation)

    document.set_metadata(
        {
            "title": title,
            "author": "multimodal-explainable-qa-agent",
            "subject": "W2 deliberately defective PDF fixture",
        }
    )
    document.save(output, garbage=4, deflate=True)
    document.close()


def _render_missing_structure(output: Path) -> None:
    """Create native text where title/sections/body all use one font size."""
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH), subfontIndex=0))

    canvas = pdf_canvas.Canvas(
        str(output),
        pagesize=A4,
        pageCompression=1,
    )
    canvas.setTitle(BAD_DOCS["bad_missing_structure"])
    canvas.setAuthor("multimodal-explainable-qa-agent")
    canvas.setSubject("W2 missing-structure fixture")
    canvas.setFont(FONT_NAME, 11)

    lines = [
        "结构缺失的退款争议处理说明",
        "适用范围",
        "本说明用于处理数字专辑退款争议，所有记录须由客户服务部保存。",
        "受理时限",
        "客户提交退款争议后，客服须在 3 个工作日内完成材料核验。",
        "证据要求",
        "客户须提供订单编号、支付记录和问题描述，缺少材料时应一次性告知。",
        "升级路径",
        "一线客服无法解决的争议转客服主管，主管须在 8 小时内首次响应。",
        "最终复核",
        "仍有争议的订单交财务负责人复核，并在 2 个工作日内给出书面结论。",
        "归档要求",
        "处理记录至少保存 24 个月，归档编号须与订单编号建立关联。",
    ]

    x = 2.2 * cm
    y = A4[1] - 2.2 * cm
    for line in lines:
        canvas.drawString(x, y, line)
        y -= 1.05 * cm

    canvas.showPage()
    canvas.save()


def _base_scan_image(
    title: str,
    lines: list[str],
) -> Image.Image:
    image = Image.new("L", PAGE_PIXELS, color=255)
    draw = ImageDraw.Draw(image)

    title_font = _pil_font(54)
    body_font = _pil_font(40)
    small_font = _pil_font(30)

    draw.rectangle((55, 55, 1185, 1699), outline=0, width=4)
    draw.text((105, 115), title, font=title_font, fill=0)

    y = 255
    for line in lines:
        draw.text((110, y), line, font=body_font, fill=0)
        y += 105

    draw.line((105, 1510, 1135, 1510), fill=0, width=3)
    draw.text(
        (105, 1560),
        "W2 文档质量与解析路由测试样本",
        font=small_font,
        fill=0,
    )
    return image


def _render_rotated_scan(output: Path) -> None:
    image = _base_scan_image(
        "仓库安全巡检说明",
        [
            "仓库安全巡检每 6 小时执行一次。",
            "发现通道堵塞后须在 30 分钟内处理。",
            "高价值唱片区由两名员工共同复核。",
            "异常记录须包含时间、区域和处理人员。",
            "连续两次未整改的事项升级至仓库主管。",
        ],
    )
    _write_image_pdf(
        image,
        output,
        title=BAD_DOCS["bad_rotated_scan"],
        rotation=90,
    )


def _render_blurred_traditional(output: Path) -> None:
    image = _base_scan_image(
        "合作方結算與對帳說明",
        [
            "合作方應於每月五日前提交完整結算資料。",
            "財務人員須在五個工作日內完成對帳。",
            "銷售數據與發票金額不一致時暫停付款。",
            "補充資料經確認後重新啟動審核流程。",
            "最終結論應由財務負責人簽字並歸檔。",
        ],
    )

    # Simulate a low-resolution scan enlarged by office software.
    small = image.resize(
        (PAGE_PIXELS[0] // 2, PAGE_PIXELS[1] // 2),
        Image.Resampling.LANCZOS,
    )
    enlarged = small.resize(PAGE_PIXELS, Image.Resampling.BILINEAR)
    blurred = enlarged.filter(ImageFilter.GaussianBlur(radius=4.0))

    _write_image_pdf(
        blurred,
        output,
        title=BAD_DOCS["bad_blurred_traditional"],
    )


def generate_all(output_dir: str | Path) -> list[Path]:
    _require_font()

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    paths = [
        output / "bad_missing_structure.pdf",
        output / "bad_rotated_scan.pdf",
        output / "bad_blurred_traditional.pdf",
    ]

    _render_missing_structure(paths[0])
    _render_rotated_scan(paths[1])
    _render_blurred_traditional(paths[2])

    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "data/docs_bad"),
    )
    args = parser.parse_args()

    paths = generate_all(args.out)
    for path in paths:
        print(f"  {path.name:<36} {path.stat().st_size:>8} bytes")

    print(f"✓ {len(paths)} bad PDF fixtures rendered to {Path(args.out)}")


if __name__ == "__main__":
    main()
