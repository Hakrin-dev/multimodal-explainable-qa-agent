"""Breadcrumb-aware chunking (层级感知切片 v0).

Rules (W1 minimal; W3 tunes with retrieval evals):
- chunks never cross an H1 boundary; H2 starts a new chunk
- target size 256~512 chars (中文按字符计), min 60
- breadcrumb = [doc_name, H1, H2?]
"""

from __future__ import annotations

from .ir import BlockIR, ChunkIR, DocIR

TARGET_CHARS = 512
MIN_CHARS = 60


SENTENCE_END = "。！？；：）)\u3002"


def _assemble_text(blocks: list[BlockIR]) -> str:
    """Join block texts: newline after headings & sentence ends;
    hard line-wraps inside a sentence are concatenated (PDF layout artifact)."""
    out = ""
    prev: BlockIR | None = None
    for b in blocks:
        if not out:
            out = b.text
        elif (prev is not None and prev.type == "heading") or b.type == "heading":
            out += "\n" + b.text
        elif out[-1] in SENTENCE_END:
            out += "\n" + b.text
        else:
            out += b.text
        prev = b
    return out


def chunk_doc(doc: DocIR) -> list[ChunkIR]:
    chunks: list[ChunkIR] = []
    breadcrumb: list[str] = [doc.name]
    current_blocks: list[BlockIR] = []
    current_text = ""
    page_start = 1

    def flush() -> None:
        nonlocal current_blocks, current_text
        if not current_blocks:
            return
        text = _assemble_text(current_blocks).strip()
        if text:
            chunks.append(ChunkIR(
                id=f"{doc.doc_id}-c{len(chunks) + 1}",
                doc_id=doc.doc_id,
                block_ids=[b.id for b in current_blocks],
                breadcrumb=list(breadcrumb),
                page_start=current_blocks[0].page,
                page_end=current_blocks[-1].page,
                text=text,
            ))
        current_blocks, current_text = [], ""

    for blk in doc.blocks:
        if blk.type == "heading":
            flush()
            level = blk.level or 1
            path = _recent_headings(doc.blocks, blk)  # list[str] up to this heading
            breadcrumb = [doc.name] + path[:level]
            # heading itself becomes the first block of the new chunk
            # (keeps Q&A "这一节讲什么" answerable)
            current_blocks = [blk]
            current_text = blk.text
            continue
        if len(current_text) + len(blk.text) > TARGET_CHARS and len(current_text) >= MIN_CHARS:
            flush()
            current_blocks = [blk]
            current_text = blk.text
        else:
            current_blocks.append(blk)
            current_text += blk.text
    flush()
    doc.chunks = chunks
    return chunks


def _recent_headings(blocks: list[BlockIR], current: BlockIR) -> list[str]:
    """Breadcrumb headings up to (and including) the current heading block."""
    path: list[str] = []
    idx = next(i for i, b in enumerate(blocks) if b.id == current.id)
    for b in blocks[:idx + 1]:
        if b.type == "heading" and (b.level or 1) >= 1:  # level 0 = doc title, skip
            level = b.level or 1
            if level == 1:
                path = [b.text]
            elif level == 2:
                if not path:
                    path = [b.text]
                else:
                    path = path[:1] + [b.text]
    return path
