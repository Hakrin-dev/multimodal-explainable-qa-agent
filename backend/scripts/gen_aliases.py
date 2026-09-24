"""LLM batch alias generation (W3, 中级任务 #1/#2 数据扩充, PLAN §4.2 ②).

For each biz_term the LLM proposes colloquial aliases / abbreviations /
common misspellings (source='llm'). Two-phase per governance:
  1. default: candidates -> data/db_schema/alias_candidates.jsonl (human review)
  2. --apply: reviewed file merged into biz_term (dedup vs existing aliases)

Budget: ~130 terms × 1 short call, response-cached → well under ¥1.
After --apply, re-run scripts/embed_terms.py --all to refresh embeddings.

Usage:
    python scripts/gen_aliases.py [--limit N] [--apply] [--file PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings, resolve_repo_path  # noqa: E402
from app.db.session import get_conn  # noqa: E402
from app.core.llm import get_llm_service  # noqa: E402

ALIAS_SYSTEM = """\
你是业务术语词典维护员。给定一个数据库字段值/业务术语及其含义，生成它在中文口语中常见的别名、俗称、简称、或贴近原词的常见错拼。

要求：
1. 只输出 JSON：{"aliases": ["别名1", "别名2"]}，不要输出其他内容。
2. 3~6 个，每个 2~6 字；不要与已有别名重复；不要生僻或易歧义的表达。
3. 贴近原词的常见错拼最多 1 个（如"爵士"→"爵士乐"这类不算错拼，直接作为别名）。"""

DEFAULT_FILE = "data/db_schema/alias_candidates.jsonl"


def load_terms() -> list[dict]:
    with get_conn(readonly=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, canonical_name, aliases, term_type, description, binding "
                    "FROM biz_term ORDER BY id")
        return [{"id": r[0], "canonical": r[1], "aliases": list(r[2] or []),
                 "term_type": r[3], "description": r[4], "binding": r[5]}
                for r in cur.fetchall()]


def generate(term: dict) -> list[str]:
    svc = get_llm_service()
    user = (f"术语：{term['canonical']}；类型：{term['term_type']}；"
            f"业务含义：{term['description'] or term['binding']}；"
            f"字段：{term['binding']}；已有别名：{', '.join(term['aliases']) or '无'}")
    resp = svc.chat(
        [{"role": "system", "content": ALIAS_SYSTEM},
         {"role": "user", "content": user}],
        temperature=0.2, max_tokens=200, purpose="scripts.alias_gen")
    m = re.search(r"\{.*\}", resp.content, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    existing = {a.lower() for a in term["aliases"]} | {term["canonical"].lower()}
    for a in data.get("aliases", []):
        a = str(a).strip()
        if 1 < len(a) <= 12 and a.lower() not in existing:
            out.append(a)
    return out[:6]




# ---- W3 审阅剪枝规则（A，2026-09-24 复审固化）--------------------------------
# 1) 泛化常用词/噪音/过时译名黑名单：这些词在普通句子里高频出现，作精确别名会误改写
#    （例：“大多客户”被改写成 “Toronto 客户”）。
COMMON_WORD_BLOCKLIST = {
    "大多", "安卓", "影片", "片子", "剧集", "听书", "波普", "流行",  # 流行=泛化词，Pop 交给模糊匹配
    "身毒", "牙利", "爱兰", "奥地", "剧乐", "爵式", "欧剧", "泡泡", "雷吉", "雷盖",
    "安猪", "巴子", "库比", "布达", "佩斯", "花都", "巴梨", "小玛", "玛姬", "小罗", "老史",
    "西德", "东德", "印地", "法籍", "图桑", "土孙", "迪戎", "狄戎", "第荣", "巴希", "芬籣",
}
# 2) 混合脚本噪音（如 “摇gun”）与纯拉丁单字母缩写由正则拦截
import re as _re
_MIXED_SCRIPT = _re.compile(r"^[\u4e00-\u9fff]+[a-zA-Z]+[\u4e00-\u9fff]*$|^[a-zA-Z]+[\u4e00-\u9fff]+$")

def review_prune(candidates: dict[str, list[str]], canonicals: set[str]) -> dict[str, list[str]]:
    """Apply blocklist + resolve cross-canonical ambiguity (base-term rule):
    an alias shared by multiple canonicals stays ONLY on the canonical that is
    a substring of the others (Rock ⊂ Rock And Roll); otherwise pruned."""
    alias_owners: dict[str, set[str]] = {}
    for canon, aliases in candidates.items():
        for a in set(aliases):
            alias_owners.setdefault(a, set()).add(canon)
    resolved: dict[str, set[str]] = {c: set(als) for c, als in candidates.items()}
    for a, owners in alias_owners.items():
        if a in canonicals:
            continue
        keep = None
        if len(owners) > 1:
            bases = [o for o in owners if all(o == x or o in x for x in owners)]
            keep = bases[0] if len(bases) == 1 else None
        for o in owners:
            resolved[o].discard(a)
            if keep == o:
                resolved[o].add(a)
    out = {}
    for canon, aliases in resolved.items():
        out[canon] = [a for a in aliases
                      if a not in COMMON_WORD_BLOCKLIST and not _MIXED_SCRIPT.match(a)]
    return out

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only first N terms (smoke)")
    ap.add_argument("--apply", action="store_true",
                    help="merge reviewed candidates into biz_term")
    ap.add_argument("--file", default=DEFAULT_FILE)
    args = ap.parse_args()
    path = resolve_repo_path(args.file)
    s = get_settings()

    if args.apply:
        merged = skipped = 0
        with get_conn(readonly=False) as conn, conn.cursor() as cur:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                cur.execute("SELECT aliases FROM biz_term WHERE id = %s", (rec["id"],))
                row = cur.fetchone()
                if row is None:
                    continue
                existing = list(row[0] or [])
                known = {a.lower() for a in existing} | {rec["canonical"].lower()}
                new = [a for a in rec.get("candidates", []) if a.lower() not in known]
                if not new:
                    skipped += 1
                    continue
                cur.execute(
                    "UPDATE biz_term SET aliases = aliases || %s, source = 'llm' "
                    "WHERE id = %s",
                    (new, rec["id"]))
                merged += len(new)
        print(f"· applied: +{merged} aliases ({skipped} records had nothing new)")
        print("· now re-embed: python scripts/embed_terms.py --all")
        return

    terms = load_terms()
    if args.limit:
        terms = terms[:args.limit]
    print(f"provider={s.llm_provider} terms={len(terms)} -> {args.file}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i, term in enumerate(terms, 1):
            cands = generate(term)
            fh.write(json.dumps({"id": term["id"], "canonical": term["canonical"],
                                 "binding": term["binding"], "candidates": cands},
                                ensure_ascii=False) + "\n")
            print(f"  [{i}/{len(terms)}] {term['canonical']}: {', '.join(cands) or '-'}")
    print(f"· candidates written for review; merge with --apply")


if __name__ == "__main__":
    main()
