"""Explainable PDF complexity assessment and parser selection (#8, CPU only).

Evidence lives in DocIR.meta, independently of the frozen block content hash.
The pending MinerU adapter is explicitly unavailable, never silently bypassed.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import pymupdf


def route_parser(level: int) -> str:
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 5:
        raise ValueError("complexity must be an integer from 1 to 5")
    return "pymupdf" if level <= 2 else "mineru"


@dataclass(frozen=True)
class ComplexityAssessment:
    pages: int
    textless_pages: tuple[int, ...]
    table_pages: tuple[int, ...]
    image_ratio: float

    @property
    def contributions(self) -> dict[str, int]:
        return {
            "textless_page": 3 if self.textless_pages else 0,
            "table": 1 if self.table_pages else 0,
            "over_20_pages": 1 if self.pages > 20 else 0,
            "image_over_30_percent": 1 if self.image_ratio > 0.30 else 0,
        }

    @property
    def level(self) -> int:
        return min(5, 1 + sum(self.contributions.values()))

    @property
    def parser(self) -> str:
        return route_parser(self.level)

    def metadata(self) -> dict:
        return {
            "complexity": self.level,
            "complexity_details": {
                "version": "rules-v1",
                "base_score": 1,
                "contributions": self.contributions,
                "textless_pages": list(self.textless_pages),
                "table_pages": list(self.table_pages),
                "image_ratio": self.image_ratio,
                "selected_parser": self.parser,
                "warnings": ["postprocessing_required"] if self.level >= 4 else [],
            },
        }


class ParserUnavailableError(RuntimeError):
    """Selected parser is unavailable; retain evidence for callers/UI reporting."""

    def __init__(self, assessment: ComplexityAssessment):
        self.assessment = assessment
        super().__init__(
            f"PDF complexity={assessment.level} requires {assessment.parser}; "
            "MinerU adapter is not implemented. No document or chunks were written."
        )


def _union_area(rectangles: list[pymupdf.Rect]) -> float:
    """Overlapping or repeated image placements count once."""
    xs = sorted({x for r in rectangles for x in (r.x0, r.x1)})
    area = 0.0
    for left, right in pairwise(xs):
        intervals = sorted(
            (r.y0, r.y1) for r in rectangles if r.x0 < right and r.x1 > left
        )
        end = float("-inf")
        height = 0.0
        for low, high in intervals:
            height += max(0.0, high - max(low, end))
            end = max(end, high)
        area += (right - left) * height
    return area


def assess_pdf(path: str | Path) -> ComplexityAssessment:
    """Inspect every page, including scanned pages in otherwise native PDFs.

    Tables use PyMuPDF's default ruled-table detector (borderless tables are
    not guaranteed). Image ratio is visible image union area / total page area.
    Blank pages conservatively require OCR review rather than silent omission.
    """
    textless, tables = [], []
    image_area = total_area = 0.0
    with pymupdf.open(path) as pdf:
        if not pdf.is_pdf or pdf.needs_pass or not pdf.page_count:
            raise ValueError("assessment requires a nonempty, unencrypted PDF")
        for number, page in enumerate(pdf, start=1):
            if not page.get_text().strip():
                textless.append(number)
            if page.find_tables().tables:
                tables.append(number)
            # Extraction coordinates are unrotated; use unrotated crop size.
            bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
            rectangles = [
                pymupdf.Rect(info["bbox"]) & bounds for info in page.get_image_info()
            ]
            image_area += _union_area([r for r in rectangles if not r.is_empty])
            total_area += bounds.get_area()
        return ComplexityAssessment(
            pages=pdf.page_count,
            textless_pages=tuple(textless),
            table_pages=tuple(tables),
            image_ratio=min(1.0, image_area / total_area) if total_area else 0.0,
        )
