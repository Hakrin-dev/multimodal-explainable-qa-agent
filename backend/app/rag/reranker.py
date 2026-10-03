"""Cross-encoder reranking for RAG retrieval candidates."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from ..core.config import Settings, get_settings, resolve_repo_path


class RerankerError(RuntimeError):
    """Raised when the configured reranker cannot produce valid scores."""


class RerankerService:
    """Lazy local cross-encoder reranker returning raw relevance logits."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._tokenizer = None
        self._model = None
        self._lock = threading.Lock()

    def _load_local(self):
        if self._model is None or self._tokenizer is None:
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

            path = resolve_repo_path(self.settings.rerank_model_path)
            if not path.exists():
                raise FileNotFoundError(
                    f"local reranker model not found: {path} "
                    "(set RERANK_MODEL_PATH or disable RERANK_ENABLED)"
                )

            self._tokenizer = AutoTokenizer.from_pretrained(str(path))
            self._model = (
                AutoModelForSequenceClassification.from_pretrained(str(path))
                .to(self.settings.rerank_device)
                .eval()
            )

        return self._tokenizer, self._model

    def score(
        self,
        query: str,
        documents: Sequence[str],
    ) -> list[float]:
        """Return one relevance logit for each candidate document."""
        if not documents:
            return []

        provider = self.settings.rerank_provider.lower()
        if provider != "local":
            raise RerankerError(
                f"unknown rerank provider: {self.settings.rerank_provider}"
            )

        import torch

        scores: list[float] = []
        batch_size = self.settings.rerank_batch_size

        with self._lock:
            tokenizer, model = self._load_local()

            with torch.inference_mode():
                for start in range(0, len(documents), batch_size):
                    batch = documents[start : start + batch_size]
                    pairs = [(query, text) for text in batch]
                    inputs = tokenizer(
                        pairs,
                        padding=True,
                        truncation=True,
                        max_length=self.settings.rerank_max_length,
                        return_tensors="pt",
                    )
                    inputs = {
                        key: value.to(self.settings.rerank_device)
                        for key, value in inputs.items()
                    }
                    logits = model(**inputs).logits.view(-1).float().cpu()
                    scores.extend(float(value) for value in logits)

        if len(scores) != len(documents):
            raise RerankerError(
                "reranker returned "
                f"{len(scores)} scores for {len(documents)} documents"
            )

        return scores
