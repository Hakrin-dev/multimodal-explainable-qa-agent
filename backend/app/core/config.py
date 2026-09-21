"""Central configuration, driven by .env (see .env.example).

All provider/credential knobs live here so that switching the main LLM
(D1: DeepSeek-V3 vs Qwen-Plus) or the fallback (D5/D9: SiliconFlow) is a
pure config change, zero code edits.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/ (container: /app)
REPO_DIR = BACKEND_DIR.parent                        # host: repo root; container: /


def resolve_repo_path(rel: str | Path) -> Path:
    """Resolve a repo-relative resource to an existing absolute path.

    Container layout mounts data & models under BACKEND_DIR (/app/data, /app/models);
    host layout keeps them in the repo root next to backend/. Probe both.
    """
    p = Path(rel)
    if p.is_absolute():
        return p
    for base in (BACKEND_DIR, REPO_DIR):
        cand = base / p
        if cand.exists():
            return cand
    return REPO_DIR / p  # non-existent: caller raises a clear error


class LLMProviderConfig(BaseModel):
    """One OpenAI-compatible provider endpoint."""

    name: str
    base_url: str
    api_key: str = ""
    default_model: str = ""
    # RMB per 1M tokens (prompt / cached-prompt / completion); for cost accounting.
    price_prompt: float = 0.0
    price_cached: float = 0.0
    price_completion: float = 0.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- active LLM ----
    llm_provider: str = "mock"  # deepseek | qwen | siliconflow | mock
    llm_model: str = ""  # optional override; falls back to provider default

    # ---- provider credentials ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"

    # ---- database ----
    postgres_user: str = "chinook"
    postgres_password: str = "chinook123"
    postgres_db: str = "chinook"
    postgres_host: str = "localhost"
    postgres_port: int = 5433

    # ---- llm layer options ----
    llm_response_cache: bool = True
    llm_usage_db: str = "var/llm_usage.sqlite"  # relative to backend/
    llm_timeout: float = 60.0
    llm_max_retries: int = 2

    # ---- embedding (D9-B: local first, SiliconFlow API fallback) ----
    embedding_provider: str = "local"  # local | siliconflow | mock
    embedding_model_path: str = "models/bge-small-zh-v1.5"  # relative to repo
    siliconflow_embedding_model: str = "BAAI/bge-m3"

    # ---- nl2sql pipeline ----
    sql_max_rows: int = 50
    sql_timeout_ms: int = 5000

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_dsn(self) -> str:
        """Keyword DSN for psql scripts."""
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"dbname={self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password}"
        )

    def provider_config(self, name: str | None = None) -> LLMProviderConfig:
        """Resolve provider settings; 'mock' is a deterministic local provider."""
        name = (name or self.llm_provider).lower()
        if name == "deepseek":
            return LLMProviderConfig(
                name="deepseek",
                base_url=self.deepseek_base_url,
                api_key=self.deepseek_api_key,
                default_model="deepseek-chat",
                # official list price (RMB / 1M tokens), cache-hit = 1/10
                price_prompt=2.0,
                price_cached=0.2,
                price_completion=8.0,
            )
        if name == "qwen":
            return LLMProviderConfig(
                name="qwen",
                base_url=self.qwen_base_url,
                api_key=self.qwen_api_key,
                default_model=self.qwen_model,
                price_prompt=0.8,
                price_cached=0.2,
                price_completion=2.0,
            )
        if name == "siliconflow":
            return LLMProviderConfig(
                name="siliconflow",
                base_url=self.siliconflow_base_url,
                api_key=self.siliconflow_api_key,
                default_model="Qwen/Qwen2.5-72B-Instruct",
            )
        if name == "mock":
            return LLMProviderConfig(name="mock", base_url="mock://local", default_model="mock-1")
        raise ValueError(f"unknown LLM provider: {name}")

    @property
    def active_model(self) -> str:
        if self.llm_provider == "mock":
            return "mock-1"
        return self.llm_model or self.provider_config().default_model


@lru_cache
def get_settings() -> Settings:
    return Settings()


def var_dir() -> Path:
    d = BACKEND_DIR / "var"
    d.mkdir(parents=True, exist_ok=True)
    return d
