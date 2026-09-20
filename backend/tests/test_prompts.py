"""Prompt template layering tests (contract: static -> semi-static -> dynamic)."""

from __future__ import annotations

from app.core.prompts import agent, nl2sql


def test_intent_messages_order():
    msgs = agent.build_intent_messages(
        "销量前十的曲目",
        history=[{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}],
        tool_inventory="nl2sql(question), rag_search(question)",
    )
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"].startswith(agent.INTENT_STATIC)
    user = msgs[1]["content"]
    # semi-static (tools) and history BEFORE the dynamic question
    assert user.index("【可用能力】") < user.index("【对话历史】") < user.index("【用户输入】")
    # static layer never contains the dynamic question
    assert "销量前十" not in msgs[0]["content"]


def test_nl2sql_messages_order():
    msgs = nl2sql.build_messages(
        schema_context="TABLE track (...)",
        question="销量前十的曲目",
        feedback="上次错误: 未知的表 tracks",
    )
    assert msgs[0]["content"].startswith(nl2sql.STATIC_RULES)
    assert "TABLE track" in msgs[0]["content"]           # semi-static with system prefix
    user = msgs[1]["content"]
    # dynamic layer: question first (shared prefix with non-repair round), feedback after
    assert user.index("【问题】") < user.index("失败反馈")
    assert "销量前十的曲目" in user
    assert "未知的表" in user


def test_static_layers_are_stable():
    """Guard: static layer content changes require a PROMPT_TEMPLATE_VERSION bump."""
    assert "只读" in nl2sql.STATIC_RULES or "SELECT" in nl2sql.STATIC_RULES
    assert "AMBIGUOUS" in agent.INTENT_STATIC
    # output format contract markers
    assert "【分析】" in nl2sql.STATIC_RULES and "【SQL】" in nl2sql.STATIC_RULES
    assert '"intent"' in agent.INTENT_STATIC
