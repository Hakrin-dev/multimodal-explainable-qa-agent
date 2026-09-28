"""LLM abstraction layer (A-role core asset, §11 of PLAN.md).

Design goals:
- Provider switching (deepseek / qwen / siliconflow / mock) is pure config (.env).
- Application-layer response cache: hash(model + params + messages + template
  version) -> SQLite.  Makes eval re-runs and dev hot loops free.
- Usage accounting: every real call is logged (model, tokens incl. provider
  cache-hit tokens, estimated RMB cost, purpose tag) -> feeds budget reports.

The `mock` provider allows the whole pipeline to run (and be tested) without
any API key; it returns scripted/deterministic responses.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence

from .config import LLMProviderConfig, Settings, get_settings  # noqa: F401 (re-export)


def _store_path(settings: Settings) -> Path:
    """llm_usage_db is relative to backend/ (default var/llm_usage.sqlite)."""
    from .config import BACKEND_DIR
    p = Path(settings.llm_usage_db)
    return p if p.is_absolute() else BACKEND_DIR / p

Role = Literal["system", "user", "assistant"]

# Bump when prompt templates change -> invalidates stale cache entries.
# v0.4-w2-d3: agent.plan schema evolution (task DAG: id/depends_on/placeholders)
# — registered exception to the text freeze, see prompt_static_layer.md changelog.
PROMPT_TEMPLATE_VERSION = "v0.4-w2-d3"


# ---------------------------------------------------------------------------
# data models
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # provider-side prefix-cache hits (DeepSeek reports them; others may be 0)
    cached_prompt_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    cost_rmb: float = 0.0
    cache_hit: bool = False  # app-layer response cache hit (no real call)
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


Message = dict  # {"role": ..., "content": ...}


@dataclass
class StreamChunk:
    """chat_stream yield type: text deltas, with the final LLMResponse attached
    to the last chunk (usage/accounting complete)."""
    delta: str = ""
    response: LLMResponse | None = None


# ---------------------------------------------------------------------------
# storage: response cache + usage accounting (SQLite)
# ---------------------------------------------------------------------------


class _LLMStore:
    """SQLite store for response cache & usage ledger. Thread-safe."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS llm_cache (
        cache_key   TEXT PRIMARY KEY,
        provider    TEXT NOT NULL,
        model       TEXT NOT NULL,
        messages    TEXT NOT NULL,
        params      TEXT NOT NULL,
        response    TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS llm_usage (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        ts                   TEXT NOT NULL,
        provider             TEXT NOT NULL,
        model                TEXT NOT NULL,
        purpose              TEXT NOT NULL DEFAULT '',
        prompt_tokens        INTEGER NOT NULL DEFAULT 0,
        cached_prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens    INTEGER NOT NULL DEFAULT 0,
        cost_rmb             REAL NOT NULL DEFAULT 0,
        app_cache_hit        INTEGER NOT NULL DEFAULT 0,
        latency_ms           INTEGER NOT NULL DEFAULT 0
    );
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as con:
            con.executescript(self._DDL)

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    # ---- cache ----

    @staticmethod
    def cache_key(provider: str, model: str, params: dict,
                  messages: Sequence[Message]) -> str:
        """Provider IS part of the key: same model name across providers
        (e.g. selection eval A/B) must never share cached responses."""
        payload = json.dumps(
            {
                "template_version": PROMPT_TEMPLATE_VERSION,
                "provider": provider,
                "model": model,
                "params": params,
                "messages": list(messages),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def cache_get(self, key: str) -> LLMResponse | None:
        with self._lock, self._conn() as con:
            row = con.execute("SELECT response FROM llm_cache WHERE cache_key=?", (key,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["response"])
        resp = LLMResponse(**{k: v for k, v in data.items() if k != "usage"})
        resp.usage = Usage(**data.get("usage", {}))
        resp.cache_hit = True
        return resp

    def cache_put(self, key: str, provider: str, model: str, messages: Sequence[Message],
                  params: dict, resp: LLMResponse) -> None:
        payload = {
            "content": resp.content,
            "model": resp.model,
            "provider": resp.provider,
            "usage": {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "cached_prompt_tokens": resp.usage.cached_prompt_tokens,
            },
            "cost_rmb": resp.cost_rmb,
            "latency_ms": resp.latency_ms,
            "raw": {},
        }
        with self._lock, self._conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO llm_cache (cache_key, provider, model, messages, params, response, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (key, provider, model, json.dumps(list(messages), ensure_ascii=False),
                 json.dumps(params, sort_keys=True), json.dumps(payload, ensure_ascii=False),
                 dt.datetime.now().isoformat(timespec="seconds")),
            )
            con.commit()

    # ---- usage ledger ----

    def log_usage(self, *, provider: str, model: str, purpose: str, usage: Usage,
                  cost_rmb: float, app_cache_hit: bool, latency_ms: int) -> None:
        with self._lock, self._conn() as con:
            con.execute(
                "INSERT INTO llm_usage (ts, provider, model, purpose, prompt_tokens,"
                " cached_prompt_tokens, completion_tokens, cost_rmb, app_cache_hit, latency_ms)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (dt.datetime.now().isoformat(timespec="seconds"), provider, model, purpose,
                 usage.prompt_tokens, usage.cached_prompt_tokens, usage.completion_tokens,
                 round(cost_rmb, 6), int(app_cache_hit), latency_ms),
            )
            con.commit()

    def usage_summary(self, since: str | None = None) -> dict[str, Any]:
        q = ("SELECT provider, model, COUNT(*) AS calls, SUM(prompt_tokens) AS pt,"
             " SUM(cached_prompt_tokens) AS cpt, SUM(completion_tokens) AS ct,"
             " SUM(cost_rmb) AS cost FROM llm_usage")
        args: tuple = ()
        if since:
            q += " WHERE ts >= ?"
            args = (since,)
        q += " GROUP BY provider, model ORDER BY cost DESC"
        with self._conn() as con:
            rows = [dict(r) for r in con.execute(q, args).fetchall()]
        return {
            "rows": rows,
            "total_cost_rmb": round(sum(r["cost"] or 0 for r in rows), 4),
        }


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


class MockProvider:
    """Deterministic provider for tests / no-key environments.

    Either script responses per call sequence (list of strings) or provide a
    callable mapping (messages, params) -> content.
    """

    name = "mock"

    def __init__(self, scripted: Sequence[str] | None = None,
                 handler: Callable[[Sequence[Message], dict], str] | None = None):
        if scripted is None and handler is None:
            handler = lambda messages, params: "MOCK LLM RESPONSE"  # noqa: E731
        self._scripted = list(scripted or [])
        self._handler = handler
        self._i = 0

    def chat(self, model: str, messages: Sequence[Message], params: dict) -> tuple[str, Usage]:
        t0 = time.monotonic()
        if self._scripted:
            content = self._scripted[min(self._i, len(self._scripted) - 1)]
            self._i += 1
        else:
            content = self._handler(messages, params)
        usage = Usage(
            prompt_tokens=sum(len(str(m.get("content", ""))) for m in messages) // 4,
            completion_tokens=len(content) // 4,
        )
        time.sleep(0.001)  # shape parity with real providers
        return content, usage

    def chat_stream(self, model: str, messages: Sequence[Message],
                    params: dict) -> "Iterator[tuple[str, Usage | None]]":
        """Yield (delta, usage); usage is None until the final chunk."""
        t0 = time.monotonic()
        if self._scripted:
            content = self._scripted[min(self._i, len(self._scripted) - 1)]
            self._i += 1
        else:
            content = self._handler(messages, params)
        usage = Usage(
            prompt_tokens=sum(len(str(m.get("content", ""))) for m in messages) // 4,
            completion_tokens=len(content) // 4,
        )
        for j in range(0, len(content), 8):
            yield content[j:j + 8], None
        yield "", usage


class OpenAICompatProvider:
    """Thin wrapper over the openai SDK; works for DeepSeek / Qwen / SiliconFlow."""

    def __init__(self, cfg: LLMProviderConfig, timeout: float = 60.0, max_retries: int = 2):
        from openai import OpenAI  # lazy import; mock envs don't need it
        if not cfg.api_key:
            raise RuntimeError(f"provider '{cfg.name}' has no API key set (.env)")
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key,
                             timeout=timeout, max_retries=max_retries)

    def chat(self, model: str, messages: Sequence[Message], params: dict) -> tuple[str, Usage]:
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            timeout=params.get("timeout"),
        )
        if "temperature" in params and params["temperature"] is not None:
            kwargs["temperature"] = params["temperature"]
        if params.get("max_tokens"):
            kwargs["max_tokens"] = params["max_tokens"]
        if params.get("response_json"):
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        u = resp.usage
        cached = 0
        if u is not None:
            extra = getattr(u, "model_extra", None) or {}
            cached = int(extra.get("prompt_cache_hit_tokens", 0) or 0)
        usage = Usage(
            prompt_tokens=(u.prompt_tokens if u else 0) or 0,
            completion_tokens=(u.completion_tokens if u else 0) or 0,
            cached_prompt_tokens=cached,
        )
        return content, usage

    def chat_stream(self, model: str, messages: Sequence[Message],
                    params: dict) -> "Iterator[tuple[str, Usage | None]]":
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            timeout=params.get("timeout"),
            stream=True,
            stream_options={"include_usage": True},
        )
        if "temperature" in params and params["temperature"] is not None:
            kwargs["temperature"] = params["temperature"]
        if params.get("max_tokens"):
            kwargs["max_tokens"] = params["max_tokens"]
        usage: Usage | None = None
        for chunk in self.client.chat.completions.create(**kwargs):
            u = getattr(chunk, "usage", None)
            if u is not None:
                extra = getattr(u, "model_extra", None) or {}
                usage = Usage(
                    prompt_tokens=u.prompt_tokens or 0,
                    completion_tokens=u.completion_tokens or 0,
                    cached_prompt_tokens=int(extra.get("prompt_cache_hit_tokens", 0) or 0),
                )
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content, None
        yield "", usage


# ---------------------------------------------------------------------------
# service facade
# ---------------------------------------------------------------------------


class LLMService:
    """Facade used across the codebase (nl2sql / agent / rag / eval)."""

    def __init__(self, settings: Settings | None = None, store: _LLMStore | None = None):
        self.settings = settings or get_settings()
        self.store = store or _LLMStore(_store_path(self.settings))
        self._providers: dict[str, Any] = {}

    # -- provider mgmt ------------------------------------------------------

    def _provider(self, name: str | None = None):
        name = (name or self.settings.llm_provider).lower()
        if name not in self._providers:
            cfg = self.settings.provider_config(name)
            if name == "mock":
                self._providers[name] = MockProvider()
            else:
                self._providers[name] = OpenAICompatProvider(
                    cfg, timeout=self.settings.llm_timeout,
                    max_retries=self.settings.llm_max_retries)
        return self._providers[name]

    def register_mock(self, mock: MockProvider) -> None:
        """Inject a configured mock (tests)."""
        self._providers["mock"] = mock

    # -- cost ---------------------------------------------------------------

    def _cost(self, cfg: LLMProviderConfig, usage: Usage) -> float:
        non_cached_prompt = max(usage.prompt_tokens - usage.cached_prompt_tokens, 0)
        cost = (
            non_cached_prompt / 1e6 * cfg.price_prompt
            + usage.cached_prompt_tokens / 1e6 * cfg.price_cached
            + usage.completion_tokens / 1e6 * cfg.price_completion
        )
        return cost

    # -- main entry ---------------------------------------------------------

    def chat(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_json: bool = False,
        purpose: str = "",
        use_cache: bool | None = None,
    ) -> LLMResponse:
        pname = (provider or self.settings.llm_provider).lower()
        cfg = self.settings.provider_config(pname)
        model = model or (self.settings.llm_model or cfg.default_model)
        params: dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_json": response_json,
        }

        cacheable = pname != "mock"
        if use_cache is None:
            use_cache = cacheable and self.settings.llm_response_cache

        if use_cache:
            key = self.store.cache_key(pname, model, params, messages)
            hit = self.store.cache_get(key)
            if hit is not None:
                # ledger: record the free hit WITH the original token counts —
                # enables "savings" reporting (cost logged as 0, app_cache_hit=1)
                self.store.log_usage(provider=pname, model=model, purpose=purpose,
                                     usage=hit.usage, cost_rmb=0.0, app_cache_hit=True,
                                     latency_ms=0)
                return hit

        t0 = time.monotonic()
        content, usage = self._provider(pname).chat(model, messages, params)
        latency_ms = int((time.monotonic() - t0) * 1000)
        cost = 0.0 if pname == "mock" else self._cost(cfg, usage)
        resp = LLMResponse(content=content, model=model, provider=pname, usage=usage,
                           cost_rmb=cost, cache_hit=False, latency_ms=latency_ms)
        if use_cache:
            key = self.store.cache_key(pname, model, params, messages)
            self.store.cache_put(key, pname, model, messages, params, resp)
        self.store.log_usage(provider=pname, model=model, purpose=purpose, usage=usage,
                             cost_rmb=cost, app_cache_hit=False, latency_ms=latency_ms)
        return resp

    def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.5,
        max_tokens: int | None = None,
        purpose: str = "",
    ) -> "Iterator[StreamChunk]":
        """Real streaming. Yields StreamChunk(delta=...) per token group; the
        final chunk carries the complete LLMResponse (usage + cost + ledger).
        Streams bypass the response cache by design."""
        pname = (provider or self.settings.llm_provider).lower()
        cfg = self.settings.provider_config(pname)
        model = model or (self.settings.llm_model or cfg.default_model)
        params: dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_json": False,
        }
        t0 = time.monotonic()
        parts: list[str] = []
        final_usage = Usage()
        for delta, usage in self._provider(pname).chat_stream(model, messages, params):
            if delta:
                parts.append(delta)
                yield StreamChunk(delta=delta)
            if usage is not None:
                final_usage = usage
        latency_ms = int((time.monotonic() - t0) * 1000)
        cost = 0.0 if pname == "mock" else self._cost(cfg, final_usage)
        resp = LLMResponse(content="".join(parts), model=model, provider=pname,
                           usage=final_usage, cost_rmb=cost, cache_hit=False,
                           latency_ms=latency_ms)
        self.store.log_usage(provider=pname, model=model, purpose=purpose,
                             usage=final_usage, cost_rmb=cost, app_cache_hit=False,
                             latency_ms=latency_ms)
        yield StreamChunk(response=resp)

    # -- reporting ----------------------------------------------------------

    def usage_summary(self, since: str | None = None) -> dict[str, Any]:
        return self.store.usage_summary(since=since)


_default_service: LLMService | None = None
_default_lock = threading.Lock()


def get_llm_service() -> LLMService:
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = LLMService()
        return _default_service
