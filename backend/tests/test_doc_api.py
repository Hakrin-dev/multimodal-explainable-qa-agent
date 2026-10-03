"""W4 document-management API tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingestion import documents
from app.main import app
from scripts.gen_bad_docs import generate_all


@pytest.fixture()
def managed_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    def resolve(relative: str) -> Path:
        return tmp_path / relative

    monkeypatch.setattr(
        documents,
        "resolve_repo_path",
        resolve,
    )
    return tmp_path


@pytest.fixture()
def bad_docs(tmp_path: Path) -> dict[str, Path]:
    paths = generate_all(tmp_path / "fixtures")
    return {item.name: item for item in paths}


def test_upload_quality_repair_and_download(
    managed_storage: Path,
    bad_docs: dict[str, Path],
) -> None:
    client = TestClient(app)
    source = bad_docs["bad_rotated_scan.pdf"]
    original_bytes = source.read_bytes()

    response = client.post(
        "/api/docs/upload",
        files={
            "file": (
                "rotated-scan.pdf",
                original_bytes,
                "application/pdf",
            )
        },
    )

    assert response.status_code == 201
    uploaded = response.json()
    doc_id = uploaded["doc_id"]

    assert uploaded["state"] == "uploaded"
    assert uploaded["quality"]["orientation"] == 90
    assert uploaded["quality"]["rotated_pages"] == [1]
    assert uploaded["complexity"] == 5

    quality_response = client.get(
        f"/api/docs/{doc_id}/quality"
    )
    assert quality_response.status_code == 200
    assert quality_response.json()["repaired"] is None

    original_response = client.get(
        f"/api/docs/{doc_id}/pdf"
    )
    assert original_response.status_code == 200
    assert original_response.content == original_bytes

    repair_response = client.post(
        f"/api/docs/{doc_id}/repair",
        json={
            "correct_orientation": True,
            "enhance_clarity": True,
            "run_ocr": False,
            "normalize_traditional": True,
        },
    )

    assert repair_response.status_code == 200
    repaired = repair_response.json()

    assert repaired["state"] == "repaired"
    assert "orientation_normalized" in repaired["actions"]
    assert (
        repaired["assessment"]["original"]["quality"]
        ["orientation"]
        == 90
    )
    assert (
        repaired["assessment"]["repaired"]["quality"]
        ["orientation"]
        == 0
    )

    repaired_response = client.get(
        f"/api/docs/{doc_id}/pdf",
        params={"version": "repaired"},
    )
    assert repaired_response.status_code == 200
    assert repaired_response.content.startswith(b"%PDF-")

    # The repair workflow must never overwrite the uploaded original.
    original_again = client.get(
        f"/api/docs/{doc_id}/pdf"
    )
    assert original_again.content == original_bytes


def test_flat_document_recovers_hierarchy_via_api(
    managed_storage: Path,
    bad_docs: dict[str, Path],
) -> None:
    client = TestClient(app)
    source = bad_docs["bad_missing_structure.pdf"]

    response = client.post(
        "/api/docs/upload",
        files={
            "file": (
                source.name,
                source.read_bytes(),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 201

    doc_id = response.json()["doc_id"]

    repaired = client.post(
        f"/api/docs/{doc_id}/repair",
        json={
            "correct_orientation": True,
            "enhance_clarity": True,
            "run_ocr": True,
            "normalize_traditional": True,
        },
    )

    assert repaired.status_code == 200
    result = repaired.json()

    # The healthy native PDF is copied, not rasterized.
    assert result["actions"] == []
    assert result["parsed"]["parser"] == "pymupdf"

    headings = result["parsed"]["headings"]
    assert headings[0] == {
        "text": "结构缺失的退款争议处理说明",
        "level": 0,
        "page": 1,
    }
    assert [
        item["text"]
        for item in headings
        if item["level"] == 1
    ] == [
        "适用范围",
        "受理时限",
        "证据要求",
        "升级路径",
        "最终复核",
        "归档要求",
    ]


def test_upload_rejects_non_pdf_extension(
    managed_storage: Path,
) -> None:
    client = TestClient(app)

    response = client.post(
        "/api/docs/upload",
        files={
            "file": (
                "not-a-pdf.txt",
                b"%PDF-fake",
                "text/plain",
            )
        },
    )

    assert response.status_code == 400
    assert "only .pdf" in response.json()["detail"]


def test_upload_rejects_fake_pdf(
    managed_storage: Path,
) -> None:
    client = TestClient(app)

    response = client.post(
        "/api/docs/upload",
        files={
            "file": (
                "fake.pdf",
                b"this is not a PDF",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 400
    assert "PDF signature" in response.json()["detail"]


def test_upload_limit_returns_413(
    managed_storage: Path,
    bad_docs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    monkeypatch.setattr(
        documents,
        "MAX_UPLOAD_BYTES",
        32,
    )

    response = client.post(
        "/api/docs/upload",
        files={
            "file": (
                "large.pdf",
                bad_docs["bad_rotated_scan.pdf"].read_bytes(),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 413


def test_missing_document_returns_404(
    managed_storage: Path,
) -> None:
    client = TestClient(app)

    response = client.get(
        "/api/docs/not-present/quality"
    )

    assert response.status_code == 404


def test_invalid_document_id_returns_400(
    managed_storage: Path,
) -> None:
    client = TestClient(app)

    response = client.get(
        "/api/docs/bad%24id/quality"
    )

    assert response.status_code == 400


def test_invalid_pdf_version_returns_400(
    managed_storage: Path,
) -> None:
    client = TestClient(app)

    response = client.get(
        "/api/docs/example/pdf",
        params={"version": "../../etc/passwd"},
    )

    assert response.status_code == 400


def test_blurred_scan_uses_standard_and_requires_review(
    managed_storage: Path,
    bad_docs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingestion.ir import BlockIR, DocIR

    observed: dict[str, object] = {}

    def fake_parse_pdf(
        path,
        doc_id=None,
        name=None,
        *,
        binary=None,
        tier=None,
        ocr_mode="auto",
        timeout=None,
    ):
        observed["tier"] = tier
        observed["ocr_mode"] = ocr_mode

        return DocIR(
            doc_id=doc_id or "blurred",
            name="合作方结算与对帐说明",
            source_path=str(path),
            pages=1,
            parser="mineru",
            blocks=[
                BlockIR(
                    id=f"{doc_id}-b1",
                    page=1,
                    type="heading",
                    text="合作方结算与对帐说明",
                    level=0,
                ),
                BlockIR(
                    id=f"{doc_id}-b2",
                    page=1,
                    type="paragraph",
                    text=(
                        "合作方应于每月五日前提交完整结算资料。"
                        "财务人员须在五个工作日内完成对帐。"
                    ),
                ),
            ],
            meta={
                "mineru": {
                    "version": "4.0.7",
                    "tier": tier,
                    "parse_mode": ocr_mode,
                    "schema": "docvortex.middle",
                    "schema_version": "2.0",
                }
            },
        )

    monkeypatch.setattr(
        documents.mineru_parser,
        "parse_pdf",
        fake_parse_pdf,
    )

    client = TestClient(app)
    source = bad_docs["bad_blurred_traditional.pdf"]

    uploaded = client.post(
        "/api/docs/upload",
        files={
            "file": (
                source.name,
                source.read_bytes(),
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 201
    doc_id = uploaded.json()["doc_id"]

    response = client.post(
        f"/api/docs/{doc_id}/repair",
        json={
            "correct_orientation": True,
            "enhance_clarity": True,
            "run_ocr": True,
            "normalize_traditional": True,
        },
    )

    assert response.status_code == 200
    result = response.json()

    assert observed == {
        "tier": "standard",
        "ocr_mode": "ocr",
    }
    assert result["state"] == "repaired"
    assert result["recovery_status"] == "usable_with_review"
    assert result["parsed"]["ocr_tier"] == "standard"
    assert result["parsed"]["requires_human_review"] is True
    assert result["parsed"]["blocks"] == 2
    assert result["parsed"]["chunks"] >= 1
    assert result["parsed"]["limitations"]
    assert (
        result["assessment"]["repaired"]["quality"]["clarity"]
        > result["assessment"]["original"]["quality"]["clarity"]
    )
