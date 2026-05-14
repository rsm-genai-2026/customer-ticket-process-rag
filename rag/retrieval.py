"""Retrieve relevant KB chunks for a query, using an in-memory dict index.

The corpus is small enough (≤ a few hundred chunks × 1024 dims) that no
vector database is needed: a single numpy matmul against the whole
embedding matrix is fast and easy to inspect. The index lives on disk as
two files (``<mode>_index.json`` + ``<mode>_embeddings.npy``); at
load-time both are merged into one Python dict.

Two retrieval modes are supported:

- ``naive_retrieve`` — one embedding per chunk; rank chunks directly.
- ``hyq_retrieve``   — multiple embeddings per chunk (one per hypothetical
  question); rank questions, then de-duplicate to the parent chunk.

If no row clears ``min_score``, both functions return ``[]`` so the
caller can degrade gracefully (skip RAG context, fall back to a generic
LLM call, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from utils.embed import DEFAULT_EMBED_MODEL, embed


def load_index(json_path: str | Path) -> dict:
    """Load a serialised index. Returns a dict with an ``embeddings`` numpy matrix."""
    path = Path(json_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    npy_name = data.get("embeddings_file")
    if not npy_name:
        raise ValueError(f"{path} missing 'embeddings_file'")
    matrix = np.load(path.parent / npy_name)
    data["embeddings"] = matrix
    return data


def _embed_query(query: str, model: str, embedder) -> np.ndarray:
    matrix = embedder([query], model=model)
    return matrix[0]


def _cosine_topk(q: np.ndarray, matrix: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(top_indices, top_scores)`` sorted descending by score.

    Embeddings stored in the index are pre-normalised so ``matrix @ q`` is
    already cosine similarity.
    """
    if matrix.shape[0] == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=matrix.dtype)
    scores = matrix @ q
    take = min(k, scores.shape[0])
    if take <= 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=scores.dtype)
    part = np.argpartition(-scores, take - 1)[:take]
    order = part[np.argsort(-scores[part])]
    return order, scores[order]


def naive_retrieve(
    query: str,
    index: dict,
    *,
    top_k: int = 5,
    min_score: float = 0.30,
    embedder=embed,
) -> list[dict]:
    """Return the top-``k`` chunks for ``query`` from a naive-mode index."""
    if index.get("mode") != "naive":
        raise ValueError(f"naive_retrieve expected mode='naive', got {index.get('mode')!r}")
    q = _embed_query(query, index.get("embed_model", DEFAULT_EMBED_MODEL), embedder)
    rows, scores = _cosine_topk(q, index["embeddings"], top_k)
    chunks = index["chunks"]
    out: list[dict] = []
    for row, score in zip(rows.tolist(), scores.tolist()):
        if score < min_score:
            break
        c = chunks[row]
        out.append(
            {
                "chunk_id": c["chunk_id"],
                "doc_id": c["doc_id"],
                "doc_title": c["doc_title"],
                "doc_type": c["doc_type"],
                "section_heading": c["section_heading"],
                "text": c["text"],
                "score": float(score),
            }
        )
    return out


def hyq_retrieve(
    query: str,
    index: dict,
    *,
    top_k: int = 5,
    min_score: float = 0.30,
    embedder=embed,
) -> list[dict]:
    """Return the top-``k`` *chunks* for ``query`` from a HyQ-mode index.

    The embedding matrix indexes hypothetical questions, not chunks, so
    multiple top rows may resolve to the same chunk; we de-duplicate by
    ``chunk_id`` and keep the best matched question.
    """
    if index.get("mode") != "hyq":
        raise ValueError(f"hyq_retrieve expected mode='hyq', got {index.get('mode')!r}")

    matrix = index["embeddings"]
    if matrix.shape[0] == 0:
        return []

    q = _embed_query(query, index.get("embed_model", DEFAULT_EMBED_MODEL), embedder)
    # Pull a generous candidate pool so we can de-dup down to top_k unique chunks.
    pool = min(matrix.shape[0], max(top_k * 4, top_k + 8))
    rows, scores = _cosine_topk(q, matrix, pool)

    row_to_chunk = _row_to_chunk_index(index)
    chunks = index["chunks"]

    seen: dict[str, dict] = {}
    for row, score in zip(rows.tolist(), scores.tolist()):
        if score < min_score:
            break
        chunk_idx = row_to_chunk.get(row)
        if chunk_idx is None:
            continue
        c = chunks[chunk_idx]
        existing = seen.get(c["chunk_id"])
        if existing and existing["score"] >= score:
            continue
        seen[c["chunk_id"]] = {
            "chunk_id": c["chunk_id"],
            "doc_id": c["doc_id"],
            "doc_title": c["doc_title"],
            "doc_type": c["doc_type"],
            "section_heading": c["section_heading"],
            "text": c["text"],
            "score": float(score),
            "matched_question": c["hyq"][c["embedding_rows"].index(row)],
        }
        if len(seen) >= top_k:
            break
    return sorted(seen.values(), key=lambda d: d["score"], reverse=True)


def _row_to_chunk_index(index: dict) -> dict[int, int]:
    """Map ``embedding_row → chunk position`` for a HyQ index."""
    mapping: dict[int, int] = {}
    for i, chunk in enumerate(index["chunks"]):
        for row in chunk.get("embedding_rows", []):
            mapping[row] = i
    return mapping
