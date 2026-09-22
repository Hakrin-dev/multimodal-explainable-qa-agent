"""Schema Linking v0 (PLAN §4.2 ③, 中级任务 #4 地基 · W3 前期).

Offline: per-table semantic cards (name + business comment + columns + FKs
+ sample values) indexed by keyword (jieba) AND vector (embedding service).
Online: dual-recall top-k tables → FK closure (join connectivity) →
compressed schema context + explicit join paths (FK-graph BFS).

W3 proper adds: LLM 精排 to drop semantically-near distractors
(Invoice vs InvoiceLine) and column-level linking; v0 measures the
token-reduction/accuracy tradeoff (ablation vs full-schema baseline).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..db import schema_meta
from ..db.schema_meta import TableMeta

_ZH_EN_STOP = set("的 有 多少 哪些 是 什么 怎么 请 和 与 对比 按 按照 从 在 中 一共 所有"
                  " the of in on for and to with how what which list show".split())


@dataclass
class LinkingResult:
    selected: list[str]           # table names (recall + FK closure)
    join_paths: list[str]         # explicit join edges, e.g. "track.genreid = genre.genreid"
    recalled: list[str]           # pre-closure recall (for trace/ablation)


def _card_text(t: TableMeta) -> str:
    samples = []
    for c in t.columns:
        if c.is_primary or c.fks:
            continue
        vals = schema_meta.top_values(t.name, c.name, limit=3)
        if vals and all(len(v) <= 30 for v in vals):
            samples.append(f"{c.name}:{','.join(vals[:2])}")
    cols = " ".join(c.name for c in t.columns)
    return f"{t.name} {t.comment or ''} {cols} {' '.join(samples)}"


class SchemaLinker:
    def __init__(self, top_k: int = 5, use_vector: bool = True):
        self.top_k = top_k
        self.use_vector = use_vector
        self._cards: dict[str, str] | None = None
        self._vectors: dict[str, list] | None = None
        self._token_cache: dict[str, dict] = {}

    # -- offline index ------------------------------------------------------

    def _ensure_cards(self) -> dict[str, str]:
        if self._cards is None:
            tables = schema_meta.load_table_meta()
            self._cards = {t.name: _card_text(t) for t in tables}
        return self._cards

    def _ensure_vectors(self) -> dict[str, list]:
        if self._vectors is None and self.use_vector:
            from ..rag.embedding import get_embedding_service
            cards = self._ensure_cards()
            names = list(cards)
            embs = get_embedding_service().embed([cards[n] for n in names])
            self._vectors = {n: e for n, e in zip(names, embs)}
        return self._vectors or {}

    # -- recall ---------------------------------------------------------------

    def _tokens(self, text: str) -> list[str]:
        import jieba
        return [w for w in jieba.cut_for_search(text.lower())
                if w.strip() and w not in _ZH_EN_STOP and not re.fullmatch(r"[\d\s%p]+", w)]

    def _keyword_recall(self, question: str, cards: dict[str, str]) -> list[tuple[str, float]]:
        q_tokens = set(self._tokens(question))
        scored = []
        for name, card in cards.items():
            card_tokens = set(self._tokens(card))
            if not card_tokens:
                continue
            # raw overlap count — normalizing by card size punishes wide tables
            # (customer/invoice) whose single Chinese business words must rank
            overlap = len(card_tokens & q_tokens)
            if overlap:
                scored.append((name, float(overlap)))
        scored.sort(key=lambda x: -x[1])
        return scored

    def _vector_recall(self, question: str) -> list[tuple[str, float]]:
        vectors = self._ensure_vectors()
        if not vectors:
            return []
        from ..rag.embedding import get_embedding_service
        q = get_embedding_service().embed_query(question)
        scored = [(n, float(q @ v)) for n, v in vectors.items()]
        scored.sort(key=lambda x: -x[1])
        return scored

    # -- public ---------------------------------------------------------------

    def link(self, question: str, tables: list[TableMeta] | None = None) -> LinkingResult:
        """Dual recall → merge → FK closure → join paths."""
        cards = self._ensure_cards()
        kw = dict(self._keyword_recall(question, cards))
        vec_list = self._vector_recall(question) if self.use_vector else []
        vec = dict(vec_list)

        # union recall: RRF fusion ∪ each modality's head — guarantees the
        # subject-domain table (e.g. customer for 哪些客户…) isn't edged out
        # by cross-domain token ties (playlisttrack also matches 曲目)
        rrf: dict[str, float] = {}
        for ranked in (sorted(kw, key=kw.get, reverse=True),
                       sorted(vec, key=vec.get, reverse=True)):
            for rank, name in enumerate(ranked):
                rrf[name] = rrf.get(name, 0.0) + 1.0 / (60 + rank + 1)
        kw_head = [n for n, _ in sorted(kw.items(), key=lambda x: -x[1])[:4]]
        vec_head = [n for n, _ in vec_list[:4]]
        recalled = (set(sorted(rrf, key=rrf.get, reverse=True)[:self.top_k])
                    | set(kw_head) | set(vec_head))

        # relevance-constrained partner expansion: FK neighbors of recalled
        # tables whose cards ALSO match the question (token overlap or vector
        # top-5) — pulls fact tables like customer/invoice for 客户/订单
        # without inflating to the whole schema
        selected = self._fk_closure(recalled, cards)
        selected = self._partner_expand(selected, question=question,
                                        vec_recall=set(n for n, _ in vec_list[:5])) | recalled
        selected = self._fk_closure(selected, cards)
        join_paths = self._join_paths(selected)
        return LinkingResult(selected=sorted(selected), join_paths=join_paths,
                             recalled=sorted(recalled))

    def _partner_expand(self, selected: set[str], question: str,
                        vec_recall: set[str], cap: int = 9) -> set[str]:
        """Add FK neighbors (≤2 hops) whose cards match the question — ranked
        by match strength, not set iteration order (customer must not lose
        the cap race to irrelevant tables)."""
        import collections
        graph: dict[str, set[str]] = collections.defaultdict(set)
        for (a, b) in self._fk_graph():
            graph[a].add(b)
            graph[b].add(a)
        q_tokens = set(self._tokens(question))
        cards = self._ensure_cards()
        out = set(selected)
        for _ in range(2):
            if len(out) >= cap:
                break
            # score ALL candidate neighbors of the current frontier first
            candidates: dict[str, int] = {}
            for t in out:
                for nb in graph.get(t, ()):
                    if nb in out:
                        continue
                    nb_tokens = set(self._tokens(cards.get(nb, "")))
                    score = len(nb_tokens & q_tokens) + (3 if nb in vec_recall else 0)
                    if score > 0:
                        candidates[nb] = max(candidates.get(nb, 0), score)
            if not candidates:
                break
            for nb in sorted(candidates, key=candidates.get, reverse=True):
                if len(out) >= cap:
                    break
                out.add(nb)
        return out

    def _fk_closure(self, seeds: set[str], cards: dict[str, str]) -> set[str]:
        """Bridge-connect recall components via shortest FK paths (W3 #5):
        single-table / already-connected recalls stay untouched — no eager
        partner inflation."""
        import collections
        graph: dict[str, set[str]] = collections.defaultdict(set)
        for (a, b) in self._fk_graph():
            graph[a].add(b)
            graph[b].add(a)

        selected = set(seeds)

        def components(nodes: set[str]) -> list[set[str]]:
            seen, comps = set(), []
            for n in nodes:
                if n in seen:
                    continue
                comp = {n}
                queue = collections.deque([n])
                seen.add(n)
                while queue:
                    cur = queue.popleft()
                    for nb in graph.get(cur, ()):  # induced subgraph
                        if nb in nodes and nb not in seen:
                            seen.add(nb)
                            comp.add(nb)
                            queue.append(nb)
                comps.append(comp)
            return comps

        comps = components(selected)
        if len(comps) <= 1:
            return selected
        # merge components pairwise via shortest bridge (BFS through all tables)
        while len(components(selected)) > 1:
            comps = components(selected)
            merged = False
            for i in range(len(comps)):
                for j in range(i + 1, len(comps)):
                    path = self._shortest_bridge(comps[i], comps[j], graph)
                    if path:
                        selected |= set(path)
                        merged = True
                        break
                if merged:
                    break
            if not merged:
                break
        return selected

    @staticmethod
    def _shortest_bridge(comp_a: set[str], comp_b: set[str],
                         graph: dict[str, set[str]]) -> list[str] | None:
        """Shortest path (any tables allowed) connecting the two components;
        returns the intermediate bridge nodes."""
        import collections
        starts = list(comp_a)
        targets = comp_b
        prev: dict[str, str | None] = {s: None for s in starts}
        queue = collections.deque(starts)
        while queue:
            cur = queue.popleft()
            if cur in targets:
                path = []
                node = cur
                while node is not None and node not in comp_a:
                    path.append(node)
                    node = prev[node]
                return list(reversed(path))
            for nb in graph.get(cur, ()):
                if nb not in prev:
                    prev[nb] = cur
                    queue.append(nb)
        return None

    def _fk_graph(self) -> set[tuple[str, str]]:
        if "fk" not in self._token_cache:
            edges = set()
            for t in schema_meta.load_table_meta():
                for c in t.columns:
                    for fk in c.fks:
                        edges.add((t.name, fk.ref_table))
            self._token_cache["fk"] = edges
        return self._token_cache["fk"]

    def _join_paths(self, selected: set[str]) -> list[str]:
        """Spanning-tree join edges among selected tables; BFS starts from the
        highest-degree table (not alphabetical — fixes isolated-start bug)."""
        import collections
        tables = {t.name: t for t in schema_meta.load_table_meta()}
        edges: list[str] = []
        seen: set[tuple[str, str]] = set()
        if not selected:
            return edges
        adj: dict[str, list[tuple[str, str, str, str]]] = collections.defaultdict(list)
        for t in tables.values():
            for c in t.columns:
                for fk in c.fks:
                    if t.name in selected and fk.ref_table in selected:
                        adj[t.name].append((t.name, c.name, fk.ref_table, fk.ref_column))
                        adj[fk.ref_table].append((t.name, c.name, fk.ref_table, fk.ref_column))
        if not adj:
            return edges
        start = max(adj, key=lambda n: len(adj[n]))
        queue = collections.deque([start])
        visited = {start}
        while queue:
            cur = queue.popleft()
            for (a, col, b, bcol) in adj[cur]:
                key = (min(a, b), max(a, b))
                if key not in seen:
                    seen.add(key)
                    edges.append(f"{a}.{col} = {b}.{bcol}")
                if b not in visited:
                    visited.add(b)
                    queue.append(b)
                if a not in visited:
                    visited.add(a)
                    queue.append(a)
        return edges


def build_compressed_context(selected: list[str]) -> str:
    """DDL context for ONLY the selected tables (+ samples)."""
    tables = schema_meta.load_table_meta()
    subset = [t for t in tables if t.name in set(selected)]
    return schema_meta.build_schema_context(subset, with_samples=True)
