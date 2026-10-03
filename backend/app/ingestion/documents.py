"""Managed document upload, assessment and repair workflow."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import pymupdf

from ..core.config import resolve_repo_path
from . import chunker, pdf_ingest
from .complexity import assess_pdf
from .parsers import mineru as mineru_parser
from .quality import assess_quality
from .repair import repair_pdf, simplify_doc_text
from .structure import recover_flat_headings

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_DOC_ID_RE = re.compile(r"^[a-z0-9_-]+$", re.I)
_SAFE_STEM_RE = re.compile(r"[^a-z0-9_-]+", re.I)


class DocumentManagementError(RuntimeError):
    """Base error exposed by document-management APIs."""


class DocumentValidationError(DocumentManagementError):
    """The uploaded content is not an acceptable PDF."""


class DocumentTooLargeError(DocumentValidationError):
    """The uploaded file exceeds the configured limit."""


class ManagedDocumentNotFoundError(DocumentManagementError):
    """A managed document ID does not exist."""


def _directory(name: str) -> Path:
    path = resolve_repo_path(f"data/{name}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validate_doc_id(doc_id: str) -> None:
    if not _DOC_ID_RE.fullmatch(doc_id):
        raise DocumentValidationError("invalid document id")


def _new_doc_id(filename: str) -> str:
    stem = Path(filename).stem.lower()
    stem = _SAFE_STEM_RE.sub("-", stem).strip("-_")
    if not stem:
        stem = "document"
    stem = stem[:48]
    return f"{stem}-{uuid4().hex[:8]}"


def _validate_pdf(content: bytes) -> int:
    if not content.startswith(b"%PDF-"):
        raise DocumentValidationError("file does not start with PDF signature")

    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                raise DocumentValidationError(
                    "encrypted PDF is not supported"
                )
            if not document.is_pdf:
                raise DocumentValidationError("file is not a PDF")
            if document.page_count < 1:
                raise DocumentValidationError("PDF contains no pages")
            return document.page_count
    except DocumentValidationError:
        raise
    except Exception as exc:
        raise DocumentValidationError(
            "damaged or unreadable PDF"
        ) from exc


def _assessment(path: Path) -> dict:
    quality = assess_quality(path)
    complexity = assess_pdf(path)

    payload = {}
    payload.update(quality.metadata())
    payload.update(complexity.metadata())
    return payload


def save_upload(filename: str | None, content: bytes) -> dict:
    """Validate and atomically persist an uploaded PDF."""
    safe_name = Path(filename or "").name

    if not safe_name.lower().endswith(".pdf"):
        raise DocumentValidationError("only .pdf uploads are supported")
    if not content:
        raise DocumentValidationError("uploaded PDF is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise DocumentTooLargeError(
            f"PDF exceeds {MAX_UPLOAD_BYTES} byte limit"
        )

    pages = _validate_pdf(content)
    doc_id = _new_doc_id(safe_name)
    target = _directory("docs_upload") / f"{doc_id}.pdf"
    temporary = target.with_suffix(".pdf.tmp")

    try:
        temporary.write_bytes(content)
        os.replace(temporary, target)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise

    result = {
        "doc_id": doc_id,
        "filename": safe_name,
        "size": len(content),
        "pages": pages,
        "state": "uploaded",
        "pdf_url": f"/api/docs/{doc_id}/pdf",
    }
    result.update(_assessment(target))
    return result


def resolve_pdf(
    doc_id: str,
    *,
    version: str = "original",
) -> Path:
    _validate_doc_id(doc_id)

    if version == "repaired":
        candidates = [
            _directory("docs_repaired") / f"{doc_id}.pdf",
        ]
    elif version == "original":
        candidates = [
            _directory("docs_upload") / f"{doc_id}.pdf",
            resolve_repo_path("data/docs_raw") / f"{doc_id}.pdf",
            resolve_repo_path("data/docs_bad") / f"{doc_id}.pdf",
        ]
    else:
        raise DocumentValidationError(
            "version must be original or repaired"
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise ManagedDocumentNotFoundError(
        f"document {doc_id!r} not found"
    )


def quality_report(doc_id: str) -> dict:
    original = resolve_pdf(doc_id, version="original")

    report = {
        "doc_id": doc_id,
        "original": {
            "size": original.stat().st_size,
            "pdf_url": f"/api/docs/{doc_id}/pdf",
            **_assessment(original),
        },
        "repaired": None,
    }

    try:
        repaired = resolve_pdf(doc_id, version="repaired")
    except ManagedDocumentNotFoundError:
        repaired = None

    if repaired is not None:
        report["repaired"] = {
            "size": repaired.stat().st_size,
            "pdf_url": (
                f"/api/docs/{doc_id}/pdf?version=repaired"
            ),
            **_assessment(repaired),
        }

    return report


def _parse_repaired(
    path: Path,
    *,
    doc_id: str,
    normalize_traditional: bool,
    mineru_tier: str,
) -> dict:
    assessment = assess_pdf(path)

    if assessment.parser == "pymupdf":
        doc = pdf_ingest.parse_pdf(path, doc_id=doc_id)
        recover_flat_headings(doc)
    elif assessment.parser == "mineru":
        doc = mineru_parser.parse_pdf(
            path,
            doc_id=doc_id,
            tier=mineru_tier,
            ocr_mode="ocr",
        )
    else:
        raise DocumentManagementError(
            f"unsupported parser route: {assessment.parser}"
        )

    changed = 0
    if normalize_traditional:
        changed = simplify_doc_text(doc)

    chunker.chunk_doc(doc)
    if not doc.chunks:
        raise DocumentManagementError(
            "repaired PDF produced no searchable chunks"
        )

    preview = "\n".join(
        block.text
        for block in doc.blocks
    )[:2000]

    headings = [
        {
            "text": block.text,
            "level": block.level,
            "page": block.page,
        }
        for block in doc.blocks
        if block.type == "heading"
    ]

    requires_human_review = doc.parser == "mineru"

    return {
        "parser": doc.parser,
        "ocr_tier": (
            mineru_tier
            if doc.parser == "mineru"
            else None
        ),
        "recovery_status": (
            "usable_with_review"
            if requires_human_review
            else "usable"
        ),
        "requires_human_review": requires_human_review,
        "limitations": (
            [
                "OCR text may contain recognition errors",
                "Original PDF is preserved separately",
            ]
            if requires_human_review
            else []
        ),
        "blocks": len(doc.blocks),
        "chunks": len(doc.chunks),
        "heading_count": len(headings),
        "headings": headings[:20],
        "traditional_to_simplified": {
            "applied": normalize_traditional,
            "changed_items": changed,
        },
        "text_preview": preview,
    }


def repair_managed_document(
    doc_id: str,
    *,
    correct_orientation: bool = True,
    enhance_clarity: bool = True,
    run_ocr: bool = True,
    normalize_traditional: bool = True,
) -> dict:
    """Repair a managed PDF without overwriting its original."""
    original = resolve_pdf(doc_id, version="original")
    repaired = _directory("docs_repaired") / f"{doc_id}.pdf"

    result = repair_pdf(
        original,
        repaired,
        correct_orientation=correct_orientation,
        enhance_clarity=enhance_clarity,
    )

    # Heavy blur needs the VLM-backed Standard tier. Normal scans retain
    # the faster Basic path. The tier is passed per invocation rather than
    # mutating process-wide environment variables.
    mineru_tier = (
        "standard"
        if result.before.clarity < 0.35
        else "basic"
    )

    parsed = None
    if run_ocr:
        try:
            parsed = _parse_repaired(
                repaired,
                doc_id=doc_id,
                normalize_traditional=normalize_traditional,
                mineru_tier=mineru_tier,
            )
        except Exception as exc:
            # Keep the valid repaired PDF and expose the parsing failure.
            parsed = {
                "error": str(exc)[:500],
                "ocr_tier": mineru_tier,
                "recovery_status": "repair_only",
                "requires_human_review": True,
                "limitations": [
                    "OCR parsing failed",
                    "Only the repaired PDF is available",
                    "Original PDF is preserved separately",
                ],
                "traditional_to_simplified": {
                    "applied": False,
                    "changed_items": 0,
                },
            }

    recovery_status = (
        parsed.get("recovery_status", "repair_only")
        if parsed is not None
        else "repair_only"
    )

    return {
        "doc_id": doc_id,
        "state": "repaired",
        "recovery_status": recovery_status,
        "actions": list(result.actions),
        "pdf_url": (
            f"/api/docs/{doc_id}/pdf?version=repaired"
        ),
        "assessment": quality_report(doc_id),
        "parsed": parsed,
    }
