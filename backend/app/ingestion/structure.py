"""Conservative heading recovery for visually flat native PDFs."""

from __future__ import annotations

from .ir import DocIR

_TERMINAL = tuple("。！？；.!?;")


def _single_line(text: str) -> str:
    return " ".join(text.split()).strip()


def recover_flat_headings(doc: DocIR) -> int:
    """Recover a title and H1 headings only when no hierarchy exists.

    Rules intentionally remain conservative:
    - first short, punctuation-free block may be the document title;
    - a short punctuation-free block followed by a longer sentence is H1;
    - documents already containing H1-H4 are not modified.
    """
    if any(
        block.type == "heading"
        and 1 <= block.level <= 4
        for block in doc.blocks
    ):
        return 0

    blocks = [
        block
        for block in doc.blocks
        if _single_line(block.text)
    ]
    if not blocks:
        return 0

    changed = 0
    first_text = _single_line(blocks[0].text)

    if (
        blocks[0].page == 1
        and len(first_text) <= 40
        and not first_text.endswith(_TERMINAL)
    ):
        blocks[0].type = "heading"
        blocks[0].level = 0
        blocks[0].meta["is_title"] = True
        blocks[0].meta["heading_source"] = "flat_structure_rules_v1"
        changed += 1

    for index in range(1, len(blocks) - 1):
        block = blocks[index]
        text = _single_line(block.text)
        following = _single_line(blocks[index + 1].text)

        candidate = (
            1 <= len(text) <= 18
            and "\n" not in block.text
            and not text.endswith(_TERMINAL)
            and len(following) >= max(12, len(text) + 5)
            and following.endswith(_TERMINAL)
        )

        if not candidate:
            continue

        block.type = "heading"
        block.level = 1
        block.meta["heading_source"] = "flat_structure_rules_v1"
        changed += 1

    if changed:
        doc.meta["structure_recovery"] = {
            "version": "rules-v1",
            "changed_blocks": changed,
        }

    return changed
