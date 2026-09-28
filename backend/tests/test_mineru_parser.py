"""Tests for the MinerU 4.x middle-JSON adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pymupdf
import pytest
from app.ingestion.parsers import mineru


def make_pdf(
    path: Path,
    *,
    width: float = 200,
    height: float = 400,
    rotation: int = 0,
) -> Path:
    with pymupdf.open() as document:
        page = document.new_page(width=width, height=height)
        page.insert_text((20, 40), "source")
        page.set_rotation(rotation)
        document.save(path)
    return path


def middle_json() -> dict:
    return {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "is_full_document": True,
        "metadata": {
            "file_suffix": "pdf",
            "producer": {
                "name": "mineru",
                "version": "4.0.7",
            },
            "document": {
                "title": "MinerU 测试文档",
                "page_count": 1,
            },
        },
        "extensions": {
            "mineru": {
                "tier": "basic",
                "parse_mode": "ocr",
            },
            "docvortex_layout": {
                "version": 1,
                "pages": [
                    {
                        "page_idx": 0,
                        "width_pt": 200,
                        "height_pt": 400,
                    }
                ],
            },
        },
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "index": 0,
                        "type": "header",
                        "bbox": [0.1, 0.01, 0.9, 0.04],
                        "content": [
                            {
                                "type": "text",
                                "content": "应过滤的页眉",
                            }
                        ],
                    },
                    {
                        "index": 1,
                        "type": "title",
                        "bbox": [0.1, 0.05, 0.7, 0.1],
                        "content": [
                            {
                                "type": "text",
                                "content": "文档标题",
                            }
                        ],
                    },
                    {
                        "index": 2,
                        "type": "text",
                        "bbox": [0.1, 0.2, 0.9, 0.3],
                        "content": [
                            {
                                "type": "text",
                                "content": "正文第一部分",
                            },
                            {
                                "type": "text",
                                "content": "正文第二部分",
                            },
                        ],
                    },
                    {
                        "index": 3,
                        "type": "table",
                        "bbox": [0.1, 0.35, 0.9, 0.5],
                        "content": [
                            {
                                "type": "text",
                                "content": "项目 | 数量",
                            }
                        ],
                    },
                    {
                        "index": 4,
                        "type": "formula",
                        "bbox": [0.1, 0.55, 0.9, 0.65],
                        "content": [
                            {
                                "type": "text",
                                "content": "S = P * Q",
                            }
                        ],
                    },
                    {
                        "index": 5,
                        "type": "figure",
                        "bbox": [0.1, 0.7, 0.9, 0.9],
                        "content": [
                            {
                                "type": "text",
                                "content": "图 1：示意图",
                            }
                        ],
                    },
                    {
                        "index": 6,
                        "type": "footer",
                        "bbox": [0.1, 0.95, 0.9, 0.99],
                        "content": [
                            {
                                "type": "text",
                                "content": "应过滤的页脚",
                            }
                        ],
                    },
                ],
            }
        ],
    }


def test_middle_json_converts_to_frozen_ir(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "sample.pdf")

    document = mineru.doc_from_middle_json(
        middle_json(),
        source,
    )

    assert document.doc_id == "sample"
    assert document.name == "MinerU 测试文档"
    assert document.pages == 1
    assert document.parser == "mineru"

    assert [block.type for block in document.blocks] == [
        "heading",
        "paragraph",
        "table",
        "formula",
        "figure",
    ]

    title = document.blocks[0]
    assert title.page == 1
    assert title.level == 0
    assert title.meta["is_title"] is True

    paragraph = document.blocks[1]
    assert paragraph.text == "正文第一部分\n正文第二部分"
    assert paragraph.bbox == [20.0, 80.0, 180.0, 120.0]
    assert paragraph.meta["mineru_type"] == "text"
    assert paragraph.meta["mineru_index"] == 2

    assert "应过滤的页眉" not in [
        block.text for block in document.blocks
    ]
    assert "应过滤的页脚" not in [
        block.text for block in document.blocks
    ]

    assert document.meta["n_blocks"] == 5
    assert document.meta["mineru"]["version"] == "4.0.7"
    assert document.meta["mineru"]["tier"] == "basic"
    assert document.meta["mineru"]["parse_mode"] == "ocr"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", "unknown.schema", "unsupported MinerU schema"),
        ("schema_version", "99.0", "unsupported MinerU schema version"),
    ],
)
def test_middle_json_rejects_unknown_contract(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    source = make_pdf(tmp_path / "sample.pdf")
    data = middle_json()
    data[field] = value

    with pytest.raises(mineru.MinerUError, match=message):
        mineru.doc_from_middle_json(data, source)


def test_parse_pdf_invokes_stateless_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_pdf(tmp_path / "scan.pdf")
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        mineru,
        "_resolve_binary",
        lambda explicit=None: "/fake/mineru-kit",
    )

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs

        output_index = command.index("--output") + 1
        output = Path(command[output_index])
        output.write_text(
            json.dumps(middle_json(), ensure_ascii=False),
            encoding="utf-8",
        )

        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout="Parsed 1 input(s).",
            stderr="",
        )

    monkeypatch.setattr(mineru.subprocess, "run", fake_run)

    document = mineru.parse_pdf(
        source,
        binary="/fake/mineru-kit",
        tier="basic",
        ocr_mode="ocr",
        timeout=30,
    )

    command = observed["command"]

    assert command[0] == "/fake/mineru-kit"
    assert "--format" in command
    assert "middle_json" in command
    assert "--pages" in command
    assert "all" in command
    assert "--tier" in command
    assert "basic" in command
    assert "--ocr-mode" in command
    assert "ocr" in command

    assert document.parser == "mineru"
    assert document.doc_id == "scan"


def test_parse_pdf_reports_cli_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_pdf(tmp_path / "scan.pdf")

    monkeypatch.setattr(
        mineru,
        "_resolve_binary",
        lambda explicit=None: "/fake/mineru-kit",
    )

    monkeypatch.setattr(
        mineru.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            returncode=2,
            stdout="",
            stderr="model unavailable",
        ),
    )

    with pytest.raises(
        mineru.MinerUError,
        match="model unavailable",
    ):
        mineru.parse_pdf(source)


def test_parse_pdf_reports_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_pdf(tmp_path / "scan.pdf")

    monkeypatch.setattr(
        mineru,
        "_resolve_binary",
        lambda explicit=None: "/fake/mineru-kit",
    )

    def timeout_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout=1)

    monkeypatch.setattr(mineru.subprocess, "run", timeout_run)

    with pytest.raises(mineru.MinerUError, match="timed out"):
        mineru.parse_pdf(source, timeout=1)