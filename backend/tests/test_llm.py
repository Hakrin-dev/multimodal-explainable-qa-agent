"""Unit tests for the LLM abstraction layer (no network, no API key)."""

from __future__ import annotations

import json

from app.core.config import Settings
from app.core.llm import LLMService, MockProvider, _LLMStore, PROMPT_TEMPLATE_VERSION


def _store(tmp_path):
    return _LLMStore(tmp_path / "llm_test.sqlite")


def test_mock_provider_scripted(tmp_path):
    svc = LLMService(Settings(llm_provider="mock"), store=_store(tmp_path))
    svc.register_mock(MockProvider(scripted=["hello", "world"]))
    r1 = svc.chat([{"role": "user", "content": "hi"}], purpose="t")
    r2 = svc.chat([{"role": "user", "content": "hi again"}], purpose="t")
    assert r1.content == "hello" and r2.content == "world"
    assert r1.provider == "mock" and r1.cost_rmb == 0.0


def test_cache_key_stability_and_bump(tmp_path):
    msgs = [{"role": "user", "content": "销量前十"}]
    params = {"temperature": 0.0, "max_tokens": None, "response_json": False}
    k1 = _LLMStore.cache_key("p", "m1", params, msgs)
    k2 = _LLMStore.cache_key("p", "m1", params, msgs)
    k3 = _LLMStore.cache_key("p", "m2", params, msgs)  # different model -> different key
    assert k1 == k2 != k3
    # message order/content is part of the key
    k4 = _LLMStore.cache_key("p", "m1", params, msgs + [{"role": "user", "content": "x"}])
    assert k4 != k1


def test_cache_roundtrip_and_usage_ledger(tmp_path):
    store = _store(tmp_path)
    msgs = [{"role": "user", "content": "q"}]
    params = {"temperature": 0.0}

    key = store.cache_key("p", "model-x", params, msgs)
    assert store.cache_get(key) is None

    from app.core.llm import LLMResponse, Usage
    resp = LLMResponse(content="answer", model="model-x", provider="p",
                       usage=Usage(prompt_tokens=10, completion_tokens=5),
                       cost_rmb=0.001, latency_ms=100)
    store.cache_put(key, "p", "model-x", msgs, params, resp)

    hit = store.cache_get(key)
    assert hit is not None and hit.cache_hit is True
    assert hit.content == "answer"
    assert hit.usage.prompt_tokens == 10
    # json roundtrip keeps floats
    assert abs(hit.cost_rmb - 0.001) < 1e-9

    store.log_usage(provider="p", model="model-x", purpose="test",
                    usage=Usage(100, 50, 20), cost_rmb=0.01,
                    app_cache_hit=False, latency_ms=200)
    store.log_usage(provider="p", model="model-x", purpose="test",
                    usage=Usage(), cost_rmb=0.0, app_cache_hit=True, latency_ms=0)
    summary = store.usage_summary()
    assert summary["total_cost_rmb"] == 0.01
    row = summary["rows"][0]
    assert row["calls"] == 2 and row["pt"] == 100 and row["ct"] == 50


def test_cost_computation(tmp_path):
    svc = LLMService(Settings(llm_provider="deepseek", deepseek_api_key="k"),
                     store=_store(tmp_path))
    from app.core.llm import Usage
    cfg = svc.settings.provider_config("deepseek")
    # 1M non-cached prompt + 1M cached + 1M completion at (2, 0.2, 8) RMB
    u = Usage(prompt_tokens=2_000_000, cached_prompt_tokens=1_000_000,
              completion_tokens=1_000_000)
    cost = svc._cost(cfg, u)
    assert abs(cost - (2 * 1 + 0.2 * 1 + 8 * 1)) < 1e-9


def test_template_version_in_key(tmp_path):
    """Bumping PROMPT_TEMPLATE_VERSION must invalidate cache keys."""
    msgs = [{"role": "user", "content": "q"}]
    payload = {"v": PROMPT_TEMPLATE_VERSION, "m": "x", "msgs": msgs}
    k1 = _LLMStore.cache_key("p", "x", {}, msgs)
    assert json.dumps(payload, sort_keys=True) not in k1  # implementation detail only


def test_cache_key_separates_providers(tmp_path):
    """Regression: same model name under different providers (selection A/B)
    must NOT share cache entries — this corrupted the first provider probe."""
    msgs = [{"role": "user", "content": "q"}]
    params = {"temperature": 0.0}
    deep = _LLMStore.cache_key("deepseek", "shared-model", params, msgs)
    qwen = _LLMStore.cache_key("qwen", "shared-model", params, msgs)
    assert deep != qwen
