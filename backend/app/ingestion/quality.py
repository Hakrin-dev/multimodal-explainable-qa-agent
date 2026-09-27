"""Explainable lightweight PDF quality assessment.

This module detects document-quality signals without OCR:

- page rotation from PDF metadata;
- text-line skew from PyMuPDF line directions;
- rendered-page clarity using Laplacian variance;
- native text-layer coverage;
- a conservative traditional-Chinese character signal.

All page numbers exposed in metadata are 1-based.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf

# Conservative set: only characters whose traditional form differs from
# the simplified form. Shared characters such as 案 and 描 must not appear.
_TRADITIONAL_ONLY = frozenset(
    "體學習員報銷與為這個門開關進應後發現時間過來"
    "國業務數據網頁車萬東樂錄專權聲線圖書會說請"
    "產經營總區號隻條項層級檔掃轉換驗證"
)


@dataclass(frozen=True)
class QualityAssessment:
    """Document-level quality result."""

    pages: int
    orientation: int
    skew: float
    clarity: float
    has_text_layer: bool
    traditional_chars: bool
    textless_pages: tuple[int, ...]
    rotated_pages: tuple[int, ...]
    page_reports: tuple[dict[str, object], ...]
    warnings: tuple[str, ...]
    score: float

    def metadata(self) -> dict[str, object]:
        """Return the optional DocIR metadata owned by this detector."""
        return {
            "quality": {
                "version": "rules-v1",
                "orientation": self.orientation,
                "skew": self.skew,
                "clarity": self.clarity,
                "has_text_layer": self.has_text_layer,
                "traditional_chars": self.traditional_chars,
                "textless_pages": list(self.textless_pages),
                "rotated_pages": list(self.rotated_pages),
                "page_reports": [dict(report) for report in self.page_reports],
                "warnings": list(self.warnings),
                "score": self.score,
            }
        }


def _contains_traditional(text: str) -> bool:
    return any(char in _TRADITIONAL_ONLY for char in text)


def _line_skew(page: pymupdf.Page) -> float:
    """Return median deviation from the nearest right angle."""
    deviations: list[float] = []
    text_dict = page.get_text("dict")

    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            direction = line.get("dir", (1.0, 0.0))
            dx, dy = float(direction[0]), float(direction[1])
            if dx == 0.0 and dy == 0.0:
                continue

            angle = math.degrees(math.atan2(dy, dx))
            deviation = abs(((angle + 45.0) % 90.0) - 45.0)
            deviations.append(deviation)

    if not deviations:
        return 0.0
    return float(np.median(deviations))


def _page_clarity(page: pymupdf.Page) -> float:
    """Estimate rendered sharpness and normalize it to 0..1."""
    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(1.5, 1.5),
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )

    image = np.frombuffer(pix.samples, dtype=np.uint8)
    image = image.reshape(pix.height, pix.stride)[:, : pix.width].astype(np.float32)

    if image.shape[0] < 3 or image.shape[1] < 3:
        return 0.0

    laplacian = (
        -4.0 * image[1:-1, 1:-1]
        + image[:-2, 1:-1]
        + image[2:, 1:-1]
        + image[1:-1, :-2]
        + image[1:-1, 2:]
    )
    variance = float(np.var(laplacian))

    # Smooth normalization: 0 stays 0 and increasingly sharp pages approach 1.
    return variance / (variance + 100.0)


def _dominant_orientation(rotations: list[int]) -> int:
    if not rotations:
        return 0
    return Counter(rotations).most_common(1)[0][0]


def assess_quality(path: str | Path) -> QualityAssessment:
    """Assess a PDF without changing or repairing it."""
    pdf_path = Path(path)

    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc:
        raise ValueError(f"unable to open PDF: {pdf_path}") from exc

    try:
        if document.needs_pass:
            raise ValueError(f"encrypted PDF is not supported: {pdf_path}")
        if document.page_count == 0:
            raise ValueError(f"PDF has no pages: {pdf_path}")

        reports: list[dict[str, object]] = []
        rotations: list[int] = []
        textless_pages: list[int] = []
        rotated_pages: list[int] = []
        clarity_values: list[float] = []
        skew_values: list[float] = []
        traditional = False

        for index, page in enumerate(document):
            page_number = index + 1
            text = page.get_text().strip()
            has_text = bool(text)
            orientation = int(page.rotation) % 360
            skew = _line_skew(page)
            clarity = _page_clarity(page)
            page_traditional = _contains_traditional(text)

            rotations.append(orientation)
            clarity_values.append(clarity)
            skew_values.append(skew)
            traditional = traditional or page_traditional

            if not has_text:
                textless_pages.append(page_number)
            if orientation != 0:
                rotated_pages.append(page_number)

            reports.append(
                {
                    "page": page_number,
                    "orientation": orientation,
                    "skew": round(skew, 3),
                    "clarity": round(clarity, 4),
                    "has_text_layer": has_text,
                    "traditional_chars": page_traditional,
                }
            )

        orientation = _dominant_orientation(rotations)
        skew = max(skew_values, default=0.0)
        clarity = float(np.mean(clarity_values))
        text_coverage = 1.0 - len(textless_pages) / document.page_count
        has_text_layer = text_coverage > 0.0

        warnings: list[str] = []
        if textless_pages:
            warnings.append("textless_pages")
        if rotated_pages:
            warnings.append("rotated_pages")
        if skew > 1.0:
            warnings.append("skew_detected")
        if clarity < 0.35:
            warnings.append("low_clarity")
        if traditional:
            warnings.append("traditional_text")

        score = 1.0
        score -= (1.0 - text_coverage) * 0.35
        if rotated_pages:
            score -= 0.15
        score -= min(skew / 10.0, 1.0) * 0.20
        score -= (1.0 - clarity) * 0.30
        score = max(0.0, min(1.0, score))

        return QualityAssessment(
            pages=document.page_count,
            orientation=orientation,
            skew=round(skew, 3),
            clarity=round(clarity, 4),
            has_text_layer=has_text_layer,
            traditional_chars=traditional,
            textless_pages=tuple(textless_pages),
            rotated_pages=tuple(rotated_pages),
            page_reports=tuple(reports),
            warnings=tuple(warnings),
            score=round(score, 4),
        )
    finally:
        document.close()
