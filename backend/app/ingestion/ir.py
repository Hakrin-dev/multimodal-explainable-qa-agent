"""Ingestion intermediate representation (IR) — B 的核心契约。

Contract doc: docs/contracts/ingestion_ir.md
Freeze target: end of W1 (with Trace & eval contracts).

Pipeline position:
    raw file → quality/complexity assess (#9/#8, W2) → parse (pymupdf/mineru)
    → DocIR (blocks) → chunking (breadcrumb-aware) → ChunkIR[] → index
    → formula assets (FormulaIR, W2)
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

BlockType = Literal["heading", "paragraph", "table", "formula", "figure"]


@dataclass
class BlockIR:
    """Atomic layout unit from the parser (one text block / table / formula)."""
    id: str
    page: int                      # 1-based
    type: BlockType
    text: str
    level: int = 0                 # heading level 1-4 (headings only)
    bbox: list[float] = field(default_factory=list)   # [x0, y0, x1, y1] PDF pt
    font_size: float = 0.0         # median span size (parser-dependent)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChunkIR:
    """Retrieval unit: merged blocks under one breadcrumb path."""
    id: str
    doc_id: str
    block_ids: list[str]
    breadcrumb: list[str]          # ["文档名", "H1", "H2"]
    page_start: int
    page_end: int
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FormulaIR:
    """公式资产（中级 #6）：LaTeX + 参数语义标签，供 formula_eval 工具绑定槽位。"""
    id: str
    doc_id: str
    page: int
    breadcrumb: list[str]
    latex: str                     # e.g. "0.03 * S + 0.02 * max(S - 100000, 0)"
    name: str = ""                 # e.g. "销售提成"
    params: dict[str, dict] = field(default_factory=dict)
    # {"S": {"desc": "当月个人销售额", "unit": "元", "source": "db|doc|user"}}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocIR:
    """One parsed document. The persistence & handover unit between stages."""
    doc_id: str
    name: str
    source_path: str
    pages: int
    parser: str = "pymupdf"        # pymupdf | mineru | paddleocr (路由结果, #8)
    blocks: list[BlockIR] = field(default_factory=list)
    chunks: list[ChunkIR] = field(default_factory=list)
    formulas: list[FormulaIR] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    # meta keys (W2 填充): quality {orientation, skew, clarity, has_text_layer,
    #   traditional_chars, score}, complexity (1-5), content_hash

    def content_hash(self) -> str:
        h = hashlib.sha256()
        for b in self.blocks:
            h.update(f"{b.page}|{b.type}|{b.level}|{b.text}".encode())
        return h.hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "name": self.name,
            "source_path": self.source_path, "pages": self.pages,
            "parser": self.parser, "meta": self.meta,
            "blocks": [b.to_dict() for b in self.blocks],
            "chunks": [c.to_dict() for c in self.chunks],
            "formulas": [f.to_dict() for f in self.formulas],
        }
