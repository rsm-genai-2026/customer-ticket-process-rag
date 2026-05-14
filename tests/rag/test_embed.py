"""Unit tests for ``utils.embed.embed``.

The TritonAI client is monkeypatched so these tests run offline.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from utils import embed as embed_mod


class _FakeEmbeddings:
    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def create(self, *, model: str, input: list[str]):  # noqa: A002 (mirrors OpenAI sig)
        self.calls.append(list(input))
        # Deterministic non-zero vectors so L2-normalisation has something to do.
        data = [SimpleNamespace(embedding=[float(len(text) + i + 1) for i in range(self.dim)]) for text in input]
        return SimpleNamespace(data=data)


class _FakeClient:
    def __init__(self, dim: int = 4) -> None:
        self.embeddings = _FakeEmbeddings(dim=dim)


def test_embed_returns_l2_normalised_matrix() -> None:
    client = _FakeClient()
    out = embed_mod.embed(["hello", "world"], model="m", client=client)
    assert out.shape == (2, 4)
    assert out.dtype == np.float32
    norms = np.linalg.norm(out, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-6)


def test_embed_batches_inputs() -> None:
    client = _FakeClient()
    inputs = [f"text-{i}" for i in range(5)]
    out = embed_mod.embed(inputs, model="m", client=client, batch_size=2)
    assert out.shape == (5, 4)
    # 5 inputs, batch_size=2 → batches of 2, 2, 1.
    assert [len(call) for call in client.embeddings.calls] == [2, 2, 1]


def test_embed_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        embed_mod.embed([], model="m", client=_FakeClient())


def test_embed_handles_zero_vector_without_dividing_by_zero() -> None:
    class ZeroEmbeddings(_FakeEmbeddings):
        def create(self, *, model: str, input: list[str]):  # noqa: A002
            data = [SimpleNamespace(embedding=[0.0] * self.dim) for _ in input]
            return SimpleNamespace(data=data)

    client = SimpleNamespace(embeddings=ZeroEmbeddings())
    out = embed_mod.embed(["x"], model="m", client=client)
    assert out.shape == (1, 4)
    assert np.all(np.isfinite(out))
