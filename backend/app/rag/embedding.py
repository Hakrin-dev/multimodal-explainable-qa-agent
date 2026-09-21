"""Embedding abstraction (D9-B): local CPU model first, API fallback, mock for tests.

Providers:
- local:  sentence-transformers at settings.embedding_model_path (W1: bge-small-zh-v1.5
          on CPU; W3 swap to GPU BGE-M3 by changing the path — zero code changes)
- siliconflow: OpenAI-compatible /embeddings endpoint (needs SILICONFLOW_API_KEY)
- mock:  deterministic hash embeddings for tests (no model, no network)
"""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Sequence

import numpy as np

from ..core.config import REPO_DIR, Settings, get_settings, resolve_repo_path  # noqa: F401


class EmbeddingService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._model = None
        self._api_dim: int | None = None
        self._lock = threading.Lock()

    # -- lazy model -------------------------------------------------------

    def _load_local(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            path = resolve_repo_path(self.settings.embedding_model_path)
            if not path.exists():
                raise FileNotFoundError(
                    f"local embedding model not found: {path} "
                    f"(set EMBEDDING_MODEL_PATH or download per README)")
            self._model = SentenceTransformer(str(path), device="cpu")
        return self._model

    # -- public api ---------------------------------------------------------

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Normalized embeddings, shape (n, dim)."""
        texts = [t.strip() for t in texts]
        provider = self.settings.embedding_provider.lower()
        if provider == "local":
            model = self._load_local()
            with self._lock:  # sentence-transformers is not thread-safe
                return np.asarray(model.encode(texts, normalize_embeddings=True,
                                               show_progress_bar=False), dtype=np.float32)
        if provider == "siliconflow":
            return self._embed_api(texts)
        if provider == "mock":
            return np.stack([_hash_embedding(t, dim=64) for t in texts])
        raise ValueError(f"unknown embedding provider: {provider}")

    def embed_query(self, query: str) -> np.ndarray:
        return self.embed([query])[0]

    @property
    def dim(self) -> int:
        provider = self.settings.embedding_provider.lower()
        if provider == "mock":
            return 64
        if provider == "local":
            model = self._load_local()
            return int(model.get_embedding_dimension())
        # API: one probe call, cached separately from the local model handle
        if self._api_dim is None:
            self._api_dim = int(self._embed_api(["维度探测"])[0].shape[0])
        return self._api_dim

    # -- siliconflow --------------------------------------------------------

    def _embed_api(self, texts: Sequence[str]) -> np.ndarray:
        cfg = self.settings.provider_config("siliconflow")
        if not cfg.api_key:
            raise RuntimeError("siliconflow embedding needs SILICONFLOW_API_KEY")
        import json
        import urllib.request

        req = urllib.request.Request(
            cfg.base_url.rstrip("/") + "/embeddings",
            data=json.dumps({
                "model": self.settings.siliconflow_embedding_model,
                "input": list(texts), "encoding_format": "float",
            }).encode(),
            headers={"Authorization": f"Bearer {cfg.api_key}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        order = {d["index"]: d["embedding"] for d in data["data"]}
        return np.asarray([order[i] for i in range(len(texts))], dtype=np.float32)


def _hash_embedding(text: str, dim: int = 64) -> np.ndarray:
    """Deterministic token-hash embedding — retrieval works via shared tokens."""
    vec = np.zeros(dim, dtype=np.float32)
    for tok in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h >> 16) % dim] += 0.5
    n = np.linalg.norm(vec)
    return vec / n if n > 0 else vec


_default: EmbeddingService | None = None
_default_lock = threading.Lock()


def get_embedding_service() -> EmbeddingService:
    global _default
    with _default_lock:
        if _default is None:
            _default = EmbeddingService()
        return _default
