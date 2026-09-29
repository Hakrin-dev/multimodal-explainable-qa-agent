"""Embedding device and batching configuration tests."""

from __future__ import annotations

import numpy as np
import pytest
import sentence_transformers
from pydantic import ValidationError

from app.core.config import Settings
from app.rag.embedding import EmbeddingService


class FakeSentenceTransformer:
    created_with: tuple[str, str] | None = None
    encode_kwargs: dict | None = None

    def __init__(self, model_path: str, device: str):
        type(self).created_with = (model_path, device)

    def encode(self, texts, **kwargs):
        type(self).encode_kwargs = kwargs
        vectors = np.arange(
            len(texts) * 4,
            dtype=np.float32,
        ).reshape(len(texts), 4)
        norms = np.linalg.norm(
            vectors,
            axis=1,
            keepdims=True,
        )
        norms[norms == 0] = 1.0
        return vectors / norms

    def get_embedding_dimension(self) -> int:
        return 4


def test_local_embedding_uses_configured_device_and_batch(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        sentence_transformers,
        "SentenceTransformer",
        FakeSentenceTransformer,
    )

    settings = Settings(
        embedding_provider="local",
        embedding_model_path=str(tmp_path),
        embedding_device="cuda",
        embedding_batch_size=7,
    )

    service = EmbeddingService(settings)
    vectors = service.embed(["第一条", "第二条"])

    assert vectors.shape == (2, 4)
    assert vectors.dtype == np.float32
    assert FakeSentenceTransformer.created_with == (
        str(tmp_path),
        "cuda",
    )
    assert FakeSentenceTransformer.encode_kwargs == {
        "batch_size": 7,
        "normalize_embeddings": True,
        "show_progress_bar": False,
    }
    assert service.dim == 4


def test_embedding_defaults_remain_cpu():
    settings = Settings(_env_file=None)

    assert settings.embedding_device == "cpu"
    assert settings.embedding_batch_size == 32


@pytest.mark.parametrize("batch_size", [0, -1, 1025])
def test_embedding_batch_size_is_validated(batch_size):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            embedding_batch_size=batch_size,
        )
