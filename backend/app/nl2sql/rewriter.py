"""Question rewriter — term linking (W1 exact → W3 fuzzy 全链路, 中级任务 #1/#2).

Stages (each records a rewrite pair for the explainability panel):
1. exact alias match (W1, longest-alias-first, unchanged contract/fast path);
2. edit-distance match (W3): non-dictionary spans (likely typos, e.g. 摇磙)
   → nearest alias within edit budget → canonical;
3. vector match (W3): remaining spans → pgvector cosine top-k against
   biz_term.embedding → canonical when sim ≥ threshold (colloquial names the
   alias lib doesn't have yet).

Output contract unchanged: RewriteResult{question, rewrites[{before, after,
term, binding, method?, score?}]}. Pure mode (terms injected) skips the DB
fuzzy stages — unit tests stay deterministic.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from ..db.session import get_conn

_EDIT_MAX_DIST = {2: 1, 3: 1}   # length → levenshtein budget (longer: 2)
_EDIT_DEFAULT_DIST = 2
_EDIT_MIN_RATIO = 0.5
_VEC_MIN_SIM = 0.75
_MAX_FUZZY_REWRITES = 3
_SPAN_MAX_LEN = 8


@dataclass
class RewriteResult:
    question: str
    rewrites: list[dict] = field(default_factory=list)  # {before, after, term, binding, method?, score?}

    @property
    def changed(self) -> bool:
        return bool(self.rewrites)


def load_terms() -> list[dict]:
    """[{canonical, aliases[], binding}] — ordered longest-alias-first."""
    terms: list[dict] = []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT canonical_name, aliases, binding FROM biz_term "
            "WHERE binding IS NOT NULL AND binding <> ''"
        )
        for canonical, aliases, binding in cur.fetchall():
            terms.append({"canonical": canonical, "aliases": list(aliases or []),
                          "binding": binding})
    return terms


def rewrite(question: str, terms: list[dict] | None = None) -> RewriteResult:
    """Exact alias matching (W1). When `terms` is None (DB mode) the W3 fuzzy
    stages (edit distance + pgvector) run afterwards, config-gated."""
    terms = terms if terms is not None else load_terms()
    if not terms:
        return RewriteResult(question=question)

    # match units: alias -> (canonical, binding), longest first (贪心最长匹配)
    units: list[tuple[str, str, str]] = []
    for t in terms:
        for alias in set([t["canonical"], *t["aliases"]]):
            if alias and len(alias) >= 2:
                units.append((alias, t["canonical"], t["binding"]))
    units.sort(key=lambda u: len(u[0]), reverse=True)

    result = question
    applied: list[dict] = []
    consumed: list[tuple[int, int]] = []  # spans already rewritten

    def overlaps(s: int, e: int) -> bool:
        return any(not (e <= cs or s >= ce) for cs, ce in consumed)

    lowered = question.lower()
    for alias, canonical, binding in units:
        if canonical in applied_names(applied):
            continue
        start = 0
        while True:
            idx = lowered.find(alias.lower(), start)
            if idx < 0:
                break
            s, e = idx, idx + len(alias)
            if not overlaps(s, e) and alias.lower() != canonical.lower():
                result = result[:s] + canonical + result[e:]
                consumed.append((s, e))
                applied.append({"before": alias, "after": canonical,
                                "term": canonical, "binding": binding,
                                "method": "exact"})
                lowered = result.lower()
                start = s + len(canonical)
            else:
                start = e
            if len(applied) >= 5:  # safety valve
                break
        if len(applied) >= 5:
            break

    # W3 fuzzy stages (DB mode only — injected terms keep pure behavior)
    if terms is None:
        result, applied, consumed = _fuzzy_stages(
            result, applied, consumed,
            [(a, c, b) for a, c, b in units])
    return RewriteResult(question=result, rewrites=applied)


# ------------------------------------------------------------------ W3 ----

def _fuzzy_stages(result: str, applied: list[dict],
                  consumed: list[tuple[int, int]],
                  units: list[tuple[str, str, str]]):
    """Edit-distance then pgvector matching over unmatched spans.

    Re-tokenizes after every replacement — offsets from a previous scan are
    invalid once a rewrite changes the string length.
    """
    from ..core.config import get_settings
    if not get_settings().term_vector_match:
        return result, applied, consumed

    seen_spans: set[str] = set()
    for _ in range(_MAX_FUZZY_REWRITES):
        if len(applied) >= 5:
            break
        hit = method = span = None
        for _s, _e, span in _candidate_spans(result, consumed):
            if span.lower() in seen_spans:
                continue
            seen_spans.add(span.lower())
            if span.lower() in {c.lower() for c in applied_names(applied)}:
                continue
            hit = _edit_match(span, units)
            method = "edit"
            if hit is None:
                hit = _vector_match(span)
                method = "vector"
            if hit is not None:
                break
        if hit is None:
            break
        canonical, binding, alias_matched, score = hit
        idx = result.lower().find(span.lower())
        if idx < 0:  # defensive — span came from result itself
            break
        result = result[:idx] + canonical + result[idx + len(span):]
        consumed.append((idx, idx + len(canonical)))
        applied.append({"before": span, "after": canonical, "term": canonical,
                        "binding": binding, "method": method, "score": round(score, 3),
                        "matched_alias": alias_matched})
    return result, applied, consumed


_SPAN_STOP = set("什么 怎么 怎样 哪些 哪个 多少 请 给 和 与 的 了 是 在 有 按 从 中 "
                 "对应 输出 排序 显示 列出 查询 统计 以及 分别 每个 所有 数据".split())


def _candidate_spans(question: str, consumed: list[tuple[int, int]],
                     cap: int = 12) -> list[tuple[int, int, str]]:
    """Unmatched jieba spans (2..8 chars). Order: longest first."""
    import jieba
    tokens: list[tuple[int, int, str]] = []
    pos = 0
    for w in jieba.cut_for_search(question):
        s, e = pos, pos + len(w)
        pos = e
        w = w.strip()
        if not w or len(w) < 2 or len(w) > _SPAN_MAX_LEN:
            continue
        if w in _SPAN_STOP:
            continue
        if any(not (e <= cs or s >= ce) for cs, ce in consumed):
            continue
        tokens.append((s, e, w))
    tokens.sort(key=lambda t: len(t[2]), reverse=True)
    return tokens[:cap]


def _edit_match(span: str, units: list[tuple[str, str, str]]):
    """Typo bridge: span vs every alias, levenshtein-budgeted. Only spans that
    are NOT dictionary words reach here in practice (see _candidate_spans
    filter in callers' spirit) — 曲目→曲风 style false hits are excluded by
    that in FREQ check."""
    import jieba
    if jieba.dt.FREQ.get(span):  # real word — not a typo candidate
        return None
    budget = _EDIT_MAX_DIST.get(len(span), _EDIT_DEFAULT_DIST)
    best = None
    for alias, canonical, binding in units:
        a = alias.lower()
        s = span.lower()
        if a == s:
            continue
        dist = _levenshtein(s, a)
        if dist > budget:
            continue
        ratio = difflib.SequenceMatcher(None, s, a).ratio()
        if ratio < _EDIT_MIN_RATIO:
            continue
        if best is None or dist < best[3]:
            best = (canonical, binding, alias, dist)
    if best is None:
        return None
    canonical, binding, alias, dist = best
    return (canonical, binding, alias, 1.0 / (1 + dist))


def _vector_match(span: str):
    """Semantic bridge: span embedding → pgvector cosine top-3."""
    try:
        from ..rag.embedding import get_embedding_service
        emb = get_embedding_service().embed_query(span).tolist()
        with get_conn(readonly=True) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT canonical_name, binding, "
                "1 - (embedding <=> %s::vector) AS sim "
                "FROM biz_term WHERE embedding IS NOT NULL "
                "ORDER BY embedding <=> %s::vector LIMIT 3",
                (emb, emb))
            rows = cur.fetchall()
    except Exception:  # noqa: BLE001 — no DB/model → stage silently off
        return None
    if not rows:
        return None
    canonical, binding, sim = rows[0]
    if sim is None or float(sim) < _VEC_MIN_SIM:
        return None
    return (canonical, binding, canonical, float(sim))


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[lb]


def applied_names(applied: list[dict]) -> set[str]:
    return {a["after"] for a in applied}
