"""End-to-end tests for ``utils.embed.embed`` against the real TritonAI gateway.

These tests require ``TRITONAI_API_KEY`` in ``.env`` (loaded by the top-level
``conftest.py``). No mocks.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils.embed import DEFAULT_EMBED_MODEL, embed


def test_embed_returns_l2_normalised_matrix() -> None:
    out = embed(["hello world", "the VPN is down"])
    assert out.shape == (2, 1024)
    assert out.dtype == np.float32
    norms = np.linalg.norm(out, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_embed_batching_preserves_order_and_semantics() -> None:
    """Re-embedding the same inputs at a different batch size must not reorder
    or substantially change the vectors. The gateway is not bit-exact across
    batches, so we assert near-identical cosine similarity per row instead."""
    inputs = [f"a short sentence number {i} that says something" for i in range(5)]
    single = embed(inputs, batch_size=64)
    batched = embed(inputs, batch_size=2)
    assert single.shape == batched.shape == (5, 1024)
    cos_per_row = (single * batched).sum(axis=1)
    assert np.all(cos_per_row > 0.99), f"per-row cosines: {cos_per_row.tolist()}"


def test_embed_paraphrases_are_closer_than_unrelated() -> None:
    """A real sanity check on the embedding model: paraphrases should cluster."""
    vecs = embed(
        [
            "how do I reset my password",
            "I forgot my password and cannot log in",
            "the office wifi is incredibly slow today",
        ]
    )
    cos = vecs @ vecs.T  # vecs are L2-normalised
    paraphrase = cos[0, 1]
    unrelated_a = cos[0, 2]
    unrelated_b = cos[1, 2]
    assert paraphrase > unrelated_a
    assert paraphrase > unrelated_b


def test_embed_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        embed([])


def test_embed_default_model_is_advertised_by_gateway() -> None:
    """Catch the case where TritonAI silently renames or removes the model."""
    from utils.connect import list_models

    ids = {m["id"] for m in list_models() if m["type"] == "embeddings"}
    assert DEFAULT_EMBED_MODEL in ids, f"{DEFAULT_EMBED_MODEL!r} not in {ids!r}"
