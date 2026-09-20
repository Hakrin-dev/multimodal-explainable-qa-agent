"""Question rewriter v0 — retrieval-free term linking (W1).

W1: exact alias matching against biz_term (longest-alias-first), recording
rewrite pairs for the explainability panel.
W3 upgrade path: n-gram candidate spans + vector recall (pgvector) +
edit-distance fuzzy match for typos — same output contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..db.session import get_conn


@dataclass
class RewriteResult:
    question: str
    rewrites: list[dict] = field(default_factory=list)  # {before, after, term, binding}

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
                                "term": canonical, "binding": binding})
                lowered = result.lower()
                start = s + len(canonical)
            else:
                start = e
            if len(applied) >= 5:  # safety valve
                break
        if len(applied) >= 5:
            break

    return RewriteResult(question=result, rewrites=applied)


def applied_names(applied: list[dict]) -> set[str]:
    return {a["after"] for a in applied}
