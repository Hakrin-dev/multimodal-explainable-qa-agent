"""MinerU 4.x middle-JSON adapter.

MinerU runs in its own virtual environment. This module invokes the stateless
``mineru-kit parse`` command and converts docvortex.middle v2 output to the
project's frozen DocIR contract.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf

from ..ir import BlockIR, DocIR

_SCHEMA = "docvortex.middle"
_SUPPORTED_SCHEMA_VERSIONS = {"2.0"}

_SKIP_TYPES = {
    "header",
    "footer",
    "page_header",
    "page_footer",
}

_HEADING_TYPES = {
    "title",
    "heading",
    "section_title",
}

_TABLE_TYPES = {
    "table",
}

_FORMULA_TYPES = {
    "formula",
    "equation",
    "interline_equation",
    "display_formula",
}

_FIGURE_TYPES = {
    "image",
    "figure",
}


class MinerUError(RuntimeError):
    """MinerU command or result-contract failure."""


class MinerUUnavailableError(MinerUError):
    """The MinerU executable cannot be located."""


def _resolve_binary(explicit: str | None = None) -> str:
    requested = explicit or os.getenv("MINERU_BIN", "mineru-kit")

    resolved = shutil.which(requested)
    if resolved:
        return resolved

    path = Path(requested).expanduser()
    if path.is_file():
        return str(path)

    conventional = Path.home() / ".venvs/mqa-mineru/bin/mineru-kit"
    if conventional.is_file():
        return str(conventional)

    raise MinerUUnavailableError(
        "mineru-kit was not found. Set MINERU_BIN to the MinerU 4.x "
        "executable (e.g. /path/to/mqa-mineru-venv/bin/mineru-kit)."
    )


def _content_strings(value: Any) -> Iterator[str]:
    """Yield text payloads recursively without serialising metadata."""
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return

    if isinstance(value, list):
        for item in value:
            yield from _content_strings(item)
        return

    if isinstance(value, dict) and "content" in value:
        yield from _content_strings(value["content"])


def _block_text(block: dict[str, Any]) -> str:
    return "\n".join(_content_strings(block.get("content", []))).strip()


def _layout_sizes(data: dict[str, Any]) -> dict[int, tuple[float, float]]:
    layout = (
        data.get("extensions", {})
        .get("docvortex_layout", {})
        .get("pages", [])
    )

    sizes: dict[int, tuple[float, float]] = {}
    for page in layout:
        try:
            page_idx = int(page["page_idx"])
            width = float(page["width_pt"])
            height = float(page["height_pt"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MinerUError("invalid docvortex page layout metadata") from exc

        if width <= 0 or height <= 0:
            raise MinerUError("MinerU returned a non-positive page size")

        sizes[page_idx] = (width, height)

    return sizes


def _bbox_in_points(
    bbox: Any,
    *,
    width: float,
    height: float,
    pdf_page: pymupdf.Page,
) -> list[float]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return []

    try:
        values = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return []

    # MinerU 4 middle JSON normally uses normalised visual-page coordinates.
    if all(-0.001 <= value <= 1.001 for value in values):
        rect = pymupdf.Rect(
            values[0] * width,
            values[1] * height,
            values[2] * width,
            values[3] * height,
        )
    else:
        rect = pymupdf.Rect(values)

    if rect.is_empty or rect.is_infinite:
        return []

    # MinerU describes the visually rotated page. Frozen BlockIR bbox semantics
    # follow PyMuPDF's unrotated PDF coordinate space.
    if pdf_page.rotation:
        rect = rect * pdf_page.derotation_matrix

    return [
        round(rect.x0, 2),
        round(rect.y0, 2),
        round(rect.x1, 2),
        round(rect.y1, 2),
    ]


def _ir_type(source_type: str) -> str:
    if source_type in _HEADING_TYPES:
        return "heading"
    if source_type in _TABLE_TYPES:
        return "table"
    if source_type in _FORMULA_TYPES:
        return "formula"
    if source_type in _FIGURE_TYPES:
        return "figure"
    return "paragraph"


def doc_from_middle_json(
    data: dict[str, Any],
    source_path: str | Path,
    *,
    doc_id: str | None = None,
    name: str | None = None,
) -> DocIR:
    """Convert MinerU docvortex.middle v2 JSON into the frozen DocIR."""
    if data.get("schema") != _SCHEMA:
        raise MinerUError(
            f"unsupported MinerU schema: {data.get('schema')!r}"
        )

    version = str(data.get("schema_version", ""))
    if version not in _SUPPORTED_SCHEMA_VERSIONS:
        raise MinerUError(f"unsupported MinerU schema version: {version!r}")

    source = Path(source_path)
    doc_id = doc_id or source.stem

    metadata = data.get("metadata", {})
    document_metadata = metadata.get("document", {})
    producer = metadata.get("producer", {})

    metadata_title = str(document_metadata.get("title") or "").strip()
    name = name or metadata_title or source.stem

    pages_data = data.get("pages")
    if not isinstance(pages_data, list):
        raise MinerUError("MinerU result does not contain a pages list")

    sizes = _layout_sizes(data)
    blocks: list[BlockIR] = []

    with pymupdf.open(source) as pdf:
        for page_data in pages_data:
            if not isinstance(page_data, dict):
                raise MinerUError("MinerU page entry must be an object")

            try:
                page_idx = int(page_data["page_idx"])
            except (KeyError, TypeError, ValueError) as exc:
                raise MinerUError("MinerU page is missing page_idx") from exc

            if page_idx < 0 or page_idx >= pdf.page_count:
                raise MinerUError(
                    f"MinerU page_idx {page_idx} is outside the source PDF"
                )

            if page_idx not in sizes:
                raise MinerUError(
                    f"MinerU layout metadata is missing page {page_idx}"
                )

            width, height = sizes[page_idx]
            pdf_page = pdf[page_idx]

            page_blocks = page_data.get("blocks", [])
            if not isinstance(page_blocks, list):
                raise MinerUError("MinerU page blocks must be a list")

            for source_block in page_blocks:
                if not isinstance(source_block, dict):
                    continue

                source_type = str(source_block.get("type") or "text").lower()

                # Page decorations must not become retrieval facts.
                if source_type in _SKIP_TYPES:
                    continue

                text = _block_text(source_block)
                if not text:
                    continue

                ir_type = _ir_type(source_type)
                level = 0

                if ir_type == "heading":
                    raw_level = source_block.get("level")
                    if source_type == "title":
                        level = 0
                    elif isinstance(raw_level, int) and 1 <= raw_level <= 4:
                        level = raw_level
                    else:
                        level = 1

                meta: dict[str, Any] = {
                    "mineru_type": source_type,
                    "mineru_index": source_block.get("index"),
                }

                if source_type == "title":
                    meta["is_title"] = True

                blocks.append(
                    BlockIR(
                        id=f"{doc_id}-b{len(blocks) + 1}",
                        page=page_idx + 1,
                        type=ir_type,
                        text=text,
                        level=level,
                        bbox=_bbox_in_points(
                            source_block.get("bbox"),
                            width=width,
                            height=height,
                            pdf_page=pdf_page,
                        ),
                        font_size=0.0,
                        meta=meta,
                    )
                )

        page_count = pdf.page_count

    mineru_extension = data.get("extensions", {}).get("mineru", {})

    doc = DocIR(
        doc_id=doc_id,
        name=name,
        source_path=str(source),
        pages=page_count,
        parser="mineru",
        blocks=blocks,
        meta={
            "body_font_size": 0.0,
            "n_blocks": len(blocks),
            "mineru": {
                "version": producer.get("version"),
                "tier": mineru_extension.get("tier"),
                "parse_mode": mineru_extension.get("parse_mode"),
                "schema": data.get("schema"),
                "schema_version": version,
            },
        },
    )
    return doc


def parse_pdf(
    path: str | Path,
    doc_id: str | None = None,
    name: str | None = None,
    *,
    binary: str | None = None,
    tier: str | None = None,
    ocr_mode: str = "auto",
    timeout: int | None = None,
) -> DocIR:
    """Run stateless MinerU parsing and convert its middle JSON to DocIR."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)

    executable = _resolve_binary(binary)
    selected_tier = tier or os.getenv("MINERU_TIER", "basic")

    if timeout is None:
        try:
            timeout = int(os.getenv("MINERU_TIMEOUT_SECONDS", "600"))
        except ValueError as exc:
            raise MinerUError(
                "MINERU_TIMEOUT_SECONDS must be an integer"
            ) from exc

    with tempfile.TemporaryDirectory(prefix="mqa-mineru-") as temp_dir:
        output = Path(temp_dir) / "middle.json"

        command = [
            executable,
            "parse",
            str(source.resolve()),
            "--output",
            str(output),
            "--format",
            "middle_json",
            "--pages",
            "all",
            "--tier",
            selected_tier,
            "--ocr-mode",
            ocr_mode,
        ]

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as exc:
            raise MinerUError(
                f"MinerU timed out after {timeout} seconds"
            ) from exc
        except OSError as exc:
            raise MinerUError(f"failed to start MinerU: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise MinerUError(
                f"MinerU exited with code {completed.returncode}: "
                f"{detail[-1000:]}"
            )

        if not output.is_file():
            raise MinerUError(
                "MinerU reported success but did not create middle JSON"
            )

        try:
            data = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MinerUError("invalid MinerU middle JSON output") from exc

        return doc_from_middle_json(
            data,
            source,
            doc_id=doc_id,
            name=name,
        )