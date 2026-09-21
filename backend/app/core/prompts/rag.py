"""RAG prompts — static-layer candidates for B (freeze at W2, see
docs/contracts/prompt_static_layer.md §3.4).
"""

from __future__ import annotations

RAG_GENERATE_STATIC = """\
你是企业知识库问答助手。仅依据给定的【知识库片段】回答问题，并遵守：

1. 每个事实性陈述后必须紧跟引用标注，格式为 [编号]，如：年假为 10 天[1]。
2. 只使用片段中出现的信息，禁止编造或引用片段之外的常识。
3. 若所有片段都不能回答问题，直接回答："知识库中未找到相关信息"，不要猜测。
4. 回答使用中文，简洁直接（2~5 句），不要复述全部片段。"""


def build_rag_messages(question: str, chunks: list[dict]) -> list[dict]:
    """chunks: [{idx, doc, page, breadcrumb, text}] — semi-static context,
    dynamic question last."""
    ctx_lines = []
    for c in chunks:
        ctx_lines.append(
            f"[{c['idx']}] 《{c['doc']}》 第{c['page']}页 {c['breadcrumb']}\n{c['text']}"
        )
    context = "\n\n".join(ctx_lines) if ctx_lines else "（无检索结果）"
    return [
        {"role": "system", "content": RAG_GENERATE_STATIC},
        {"role": "user", "content": f"【知识库片段】\n{context}\n\n【问题】\n{question}"},
    ]
