"""Usage-ledger analytics tests (cache economics, W2-D1)."""

from __future__ import annotations

from app.core.config import Settings
from app.core.llm import LLMService, MockProvider, _LLMStore
from app.core.usage import stats


def test_stats_shape_and_cache_economics(tmp_path):
    store = _LLMStore(tmp_path / "u.sqlite")
    s = Settings(llm_provider="mock")
    svc = LLMService(s, store)

    # one real call + one cache hit (same messages) — mock provider bypasses
    # cache, so drive the ledger directly like a real provider would:
    msgs = [{"role": "user", "content": "q"}]
    from app.core.llm import LLMResponse, Usage
    resp = LLMResponse(content="a", model="deepseek-chat", provider="deepseek",
                       usage=Usage(prompt_tokens=100, completion_tokens=50,
                                   cached_prompt_tokens=60), cost_rmb=0.0006)
    key = store.cache_key("deepseek", "deepseek-chat", {"temperature": 0}, msgs)
    store.cache_put(key, "deepseek", "deepseek-chat", msgs, {"temperature": 0}, resp)
    store.log_usage(provider="deepseek", model="deepseek-chat", purpose="t.real",
                    usage=resp.usage, cost_rmb=0.0006, app_cache_hit=False, latency_ms=10)
    # cache hit — logged WITH original tokens, cost 0 (W2-D1 behavior)
    store.log_usage(provider="deepseek", model="deepseek-chat", purpose="t.real",
                    usage=resp.usage, cost_rmb=0.0, app_cache_hit=True, latency_ms=0)

    # point analytics at this ledger
    st = stats(settings=Settings(llm_usage_db=str(tmp_path / "u.sqlite")))
    assert st["total_calls"] == 1 and st["app_cache_hits"] == 1
    assert st["prompt_tokens"] == 100 and st["prefix_cached_tokens"] == 60
    assert st["prefix_cache_hit_rate"] == 0.6
    # savings: no-prefix-cache cost = 100*2/1e6 + 50*8/1e6 = 0.0006? -> (0.0002+0.0004)=0.0006
    #   real cost 0.0006 (60 tokens already billed at cached price inside it)
    #   hit-would-cost = 100*2/1e6 + 50*8/1e6 = 0.0006 (app layer)
    assert st["app_cache_savings_rmb"] > 0
    assert st["by_purpose"]["t.real"]["calls"] == 1
    assert st["by_purpose"]["t.real"]["app_cache_hits"] == 1


def test_stats_empty_ledger(tmp_path):
    st = stats(settings=Settings(llm_usage_db=str(tmp_path / "none.sqlite")))
    assert st["total_calls"] == 0 and st["actual_cost_rmb"] == 0.0


def test_mock_provider_still_scripts():
    svc = LLMService(Settings(llm_provider="mock"), _LLMStore(_p := __import__("pathlib").Path("/tmp/_t.sqlite")))
    svc.register_mock(MockProvider(scripted=["x"]))
    assert svc.chat([{"role": "user", "content": "q"}]).content == "x"
