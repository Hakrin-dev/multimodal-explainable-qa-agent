"""W4-D2 cross-source routing unit tests.

The multi-doc compare intercept and the fuse evidence-use hint are pure
functions; end-to-end routing is covered by eval/run_cross_source.py (10/10).
"""

from __future__ import annotations

from app.agent.kernel import _is_multidoc_compare
from app.agent.planner import build_fuse_messages


def test_multidoc_compare_detects_compare_plus_doc_noun():
    assert _is_multidoc_compare("员工手册规定的年假天数，和客服SOP里客户投诉处理的时限，分别是什么")
    assert _is_multidoc_compare("对比员工手册与供应商合作协议的条款")


def test_multidoc_compare_negative():
    # no compare wording
    assert not _is_multidoc_compare("员工手册里年假有几天")
    # no document noun
    assert not _is_multidoc_compare("Rock 和 Jazz 哪个销量高")
    # neither
    assert not _is_multidoc_compare("2024年销售冠军是谁")


def test_fuse_messages_carries_evidence_hint():
    msgs = build_fuse_messages(
        "问题", [{"tool": "nl2sql", "question": "x", "result": {"rows": [[1, "a"]]}}]
    )
    assert msgs[0]["role"] == "system"
    assert "不要声称" in msgs[1]["content"]
    assert "问题" in msgs[1]["content"]
