"""Recoverable PDF repair helpers for the W4 document console.

The original file is never overwritten. Image processing is deliberately
described as clarity enhancement rather than restoration of lost information.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import shutil
from typing import Any

import pymupdf
from PIL import Image, ImageFilter, ImageOps

from .ir import DocIR
from .quality import QualityAssessment, assess_quality


class DocumentRepairError(RuntimeError):
    """A document cannot be repaired without risking the original."""


@dataclass(frozen=True)
class RepairResult:
    source_path: str
    repaired_path: str
    actions: tuple[str, ...]
    before: QualityAssessment
    after: QualityAssessment

    def metadata(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "repaired_path": self.repaired_path,
            "actions": list(self.actions),
            "before": self.before.metadata()["quality"],
            "after": self.after.metadata()["quality"],
        }


def _enhance_image(image: Image.Image) -> Image.Image:
    """Strong scan enhancement; never described as content restoration."""
    grayscale = ImageOps.grayscale(image)
    contrasted = ImageOps.autocontrast(grayscale, cutoff=1)
    sharpened = contrasted.filter(
        ImageFilter.UnsharpMask(
            radius=2.2,
            percent=260,
            threshold=2,
        )
    )
    return sharpened.convert("RGB")


def repair_pdf(
    source_path: str | Path,
    output_path: str | Path,
    *,
    correct_orientation: bool = True,
    enhance_clarity: bool = True,
    render_scale: float = 2.0,
) -> RepairResult:
    """Render a normalized copy while preserving the original PDF."""
    source = Path(source_path)
    output = Path(output_path)

    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".pdf":
        raise DocumentRepairError("only PDF documents can be repaired")
    if source.resolve() == output.resolve():
        raise DocumentRepairError("repair output must not overwrite source")
    if render_scale < 1.0 or render_scale > 4.0:
        raise ValueError("render_scale must be between 1.0 and 4.0")

    before = assess_quality(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    needs_orientation = (
        correct_orientation
        and bool(before.rotated_pages)
    )
    needs_clarity = (
        enhance_clarity
        and before.clarity < 0.35
    )

    actions: list[str] = []
    if needs_orientation:
        actions.append("orientation_normalized")
    if needs_clarity:
        actions.append("clarity_enhanced")

    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()

    # A structurally flat but otherwise healthy native PDF must retain its
    # original text layer. Hierarchy recovery operates on DocIR separately.
    if not actions:
        try:
            shutil.copy2(source, temporary)
            temporary.replace(output)
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

        after = assess_quality(output)
        return RepairResult(
            source_path=str(source),
            repaired_path=str(output),
            actions=(),
            before=before,
            after=after,
        )

    try:
        with pymupdf.open(source) as document:
            if document.needs_pass:
                raise DocumentRepairError(
                    "encrypted PDF cannot be repaired"
                )
            if document.page_count == 0:
                raise DocumentRepairError("PDF contains no pages")

            repaired = pymupdf.open()
            try:
                for page in document:
                    # PyMuPDF renders the visual orientation, including
                    # existing PDF rotation metadata.
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(
                            render_scale,
                            render_scale,
                        ),
                        alpha=False,
                    )
                    image = Image.open(
                        BytesIO(pixmap.tobytes("png"))
                    ).convert("RGB")

                    if needs_clarity:
                        image = _enhance_image(image)

                    buffer = BytesIO()
                    image.save(
                        buffer,
                        format="PNG",
                        optimize=False,
                    )

                    visual_rect = page.rect
                    target_page = repaired.new_page(
                        width=visual_rect.width,
                        height=visual_rect.height,
                    )
                    target_page.insert_image(
                        target_page.rect,
                        stream=buffer.getvalue(),
                    )

                metadata = dict(document.metadata or {})
                metadata["producer"] = (
                    "multimodal-explainable-qa-agent W4 repair"
                )
                repaired.set_metadata(metadata)
                repaired.save(
                    temporary,
                    garbage=4,
                    deflate=True,
                )
            finally:
                repaired.close()

        temporary.replace(output)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise

    after = assess_quality(output)
    return RepairResult(
        source_path=str(source),
        repaired_path=str(output),
        actions=tuple(actions),
        before=before,
        after=after,
    )


def simplify_doc_text(doc: DocIR) -> int:
    """Convert OCR/native IR text to simplified Chinese after parsing.

    The visible PDF is not claimed to have changed language. This function
    normalizes the searchable DocIR produced by PyMuPDF or MinerU.
    """
    try:
        from opencc import OpenCC
    except ImportError as exc:
        raise DocumentRepairError(
            "OpenCC is required for traditional-to-simplified conversion"
        ) from exc

    converter = OpenCC("t2s")
    changed = 0

    converted_name = converter.convert(doc.name)
    if converted_name != doc.name:
        doc.name = converted_name
        changed += 1

    for block in doc.blocks:
        converted = converter.convert(block.text)
        if converted != block.text:
            block.text = converted
            changed += 1

    for chunk in doc.chunks:
        converted = converter.convert(chunk.text)
        if converted != chunk.text:
            chunk.text = converted
            changed += 1
        chunk.breadcrumb = [
            converter.convert(item)
            for item in chunk.breadcrumb
        ]

    doc.meta.setdefault("repair", {})
    doc.meta["repair"]["traditional_to_simplified"] = {
        "applied": True,
        "changed_items": changed,
    }
    return changed
