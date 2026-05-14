"""TritonAI embeddings helper.

Mirrors the no-fallbacks style of :mod:`utils.connect` — a single function that
sends a list of strings to a TritonAI-hosted embedding model and returns an
L2-normalised :class:`numpy.ndarray`. Pre-normalising means downstream
retrieval can compute cosine similarity with a plain matmul.

Example
-------
    from utils.embed import embed

    vecs = embed(["how do I reset my password?", "VPN is down"])
    # vecs.shape == (2, 1024); vecs[i] @ vecs[i] == 1.0

The only embedding model currently exposed by the TritonAI gateway is
``api-tgpt-embeddings`` (1024-dim). Confirm with
``utils.connect.list_models()`` if you need to swap.
"""

from __future__ import annotations

import numpy as np
from openai import OpenAI

from utils.connect import get_client

DEFAULT_EMBED_MODEL = "api-tgpt-embeddings"


def embed(
    texts: list[str],
    model: str = DEFAULT_EMBED_MODEL,
    *,
    batch_size: int = 64,
    client: OpenAI | None = None,
) -> np.ndarray:
    """Return L2-normalised embeddings for ``texts``.

    Parameters
    ----------
    texts:
        Non-empty list of strings to embed.
    model:
        Embedding model id on TritonAI. Defaults to ``api-tgpt-embeddings``.
    batch_size:
        Number of strings per ``embeddings.create`` call. The gateway accepts
        comfortably more than 64, but smaller batches give a smoother failure
        mode if one input is malformed.
    client:
        Optional pre-built :class:`openai.OpenAI` client (e.g., a mock in tests).
        Defaults to one created from ``TRITONAI_API_KEY``.

    Returns
    -------
    numpy.ndarray
        Shape ``(len(texts), dim)``, dtype ``float32``, L2-normalised per row.
    """
    if not texts:
        raise ValueError("embed() requires at least one input string")
    c = client or get_client()

    rows: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        resp = c.embeddings.create(model=model, input=chunk)
        rows.extend(item.embedding for item in resp.data)

    arr = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return arr / norms
