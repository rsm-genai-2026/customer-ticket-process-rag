"""Tests for ``rag.retrieval``."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rag import retrieval


def _save(tmp_path: Path, index: dict, matrix: np.ndarray) -> Path:
    json_path = tmp_path / f"{index['mode']}_index.json"
    npy_path = tmp_path / f"{index['mode']}_embeddings.npy"
    with_ref = {**index, "embeddings_file": npy_path.name}
    json_path.write_text(json.dumps(with_ref), encoding="utf-8")
    np.save(npy_path, matrix)
    return json_path


def _unit(v: list[float]) -> np.ndarray:
    arr = np.asarray(v, dtype=np.float32)
    arr = arr / np.linalg.norm(arr)
    return arr


def _fake_embedder(vec: list[float]):
    """Return an embedder that always emits the given vector."""

    def _e(texts: list[str], model: str = "x") -> np.ndarray:
        v = _unit(vec)
        return np.stack([v] * len(texts), axis=0)

    return _e


# ---------------------------------------------------------------------------
# load_index
# ---------------------------------------------------------------------------


def test_load_index_round_trips(tmp_path: Path) -> None:
    matrix = np.array([_unit([1, 0, 0]), _unit([0, 1, 0])], dtype=np.float32)
    index = {
        "schema_version": 1,
        "mode": "naive",
        "embed_model": "fake",
        "chunks": [
            {
                "chunk_id": "c0",
                "doc_id": "KB-1",
                "doc_title": "T",
                "doc_type": "policy",
                "section_heading": "h",
                "text": "a",
                "hyq": [],
                "embedding_rows": [0],
            },
            {
                "chunk_id": "c1",
                "doc_id": "KB-2",
                "doc_title": "T2",
                "doc_type": "policy",
                "section_heading": "h",
                "text": "b",
                "hyq": [],
                "embedding_rows": [1],
            },
        ],
    }
    json_path = _save(tmp_path, index, matrix)
    loaded = retrieval.load_index(json_path)
    assert loaded["mode"] == "naive"
    assert np.allclose(loaded["embeddings"], matrix)


# ---------------------------------------------------------------------------
# naive_retrieve
# ---------------------------------------------------------------------------


def _naive_index(chunks_meta: list[dict], matrix: np.ndarray) -> dict:
    chunks = []
    for i, m in enumerate(chunks_meta):
        chunks.append(
            {
                "chunk_id": m["chunk_id"],
                "doc_id": m["doc_id"],
                "doc_title": m.get("doc_title", "T"),
                "doc_type": m.get("doc_type", "policy"),
                "section_heading": m.get("section_heading", "h"),
                "text": m.get("text", ""),
                "hyq": [],
                "embedding_rows": [i],
            }
        )
    return {
        "schema_version": 1,
        "mode": "naive",
        "embed_model": "fake",
        "chunks": chunks,
        "embeddings": matrix,
    }


def test_naive_retrieve_returns_top_k_above_threshold() -> None:
    matrix = np.stack([_unit([1, 0]), _unit([0, 1]), _unit([1, 1])])
    index = _naive_index(
        [
            {"chunk_id": "c0", "doc_id": "KB-A", "text": "A"},
            {"chunk_id": "c1", "doc_id": "KB-B", "text": "B"},
            {"chunk_id": "c2", "doc_id": "KB-C", "text": "C"},
        ],
        matrix,
    )
    hits = retrieval.naive_retrieve(
        "query",
        index,
        top_k=2,
        min_score=0.0,
        embedder=_fake_embedder([1, 0]),
    )
    assert [h["chunk_id"] for h in hits] == ["c0", "c2"]
    assert hits[0]["score"] > hits[1]["score"]


def test_naive_retrieve_threshold_returns_empty() -> None:
    matrix = np.stack([_unit([1, 0]), _unit([0, 1])])
    index = _naive_index(
        [
            {"chunk_id": "c0", "doc_id": "KB-A"},
            {"chunk_id": "c1", "doc_id": "KB-B"},
        ],
        matrix,
    )
    # Best cosine vs the [-1, 1] query is 0.707; threshold 0.99 rules both out.
    hits = retrieval.naive_retrieve(
        "query",
        index,
        top_k=5,
        min_score=0.99,
        embedder=_fake_embedder([-1, 1]),
    )
    assert hits == []


def test_naive_retrieve_wrong_mode_raises() -> None:
    index = {"mode": "hyq", "chunks": [], "embeddings": np.zeros((0, 2), dtype=np.float32)}
    with pytest.raises(ValueError):
        retrieval.naive_retrieve("q", index, embedder=_fake_embedder([1, 0]))


# ---------------------------------------------------------------------------
# hyq_retrieve
# ---------------------------------------------------------------------------


def test_hyq_retrieve_deduplicates_by_chunk_id() -> None:
    # Two chunks, each with two hypothetical questions = 4 embedding rows.
    matrix = np.stack(
        [
            _unit([1.0, 0.0]),  # chunk c0, q0  — best match
            _unit([0.95, 0.05]),  # chunk c0, q1 — strong but duplicate
            _unit([0.0, 1.0]),  # chunk c1, q0 — opposite
            _unit([0.1, 0.9]),  # chunk c1, q1 — also far
        ]
    )
    index = {
        "schema_version": 1,
        "mode": "hyq",
        "embed_model": "fake",
        "chunks": [
            {
                "chunk_id": "c0",
                "doc_id": "KB-A",
                "doc_title": "A",
                "doc_type": "policy",
                "section_heading": "h",
                "text": "A text",
                "hyq": ["q0a", "q0b"],
                "embedding_rows": [0, 1],
            },
            {
                "chunk_id": "c1",
                "doc_id": "KB-B",
                "doc_title": "B",
                "doc_type": "policy",
                "section_heading": "h",
                "text": "B text",
                "hyq": ["q1a", "q1b"],
                "embedding_rows": [2, 3],
            },
        ],
        "embeddings": matrix,
    }
    hits = retrieval.hyq_retrieve(
        "query",
        index,
        top_k=5,
        min_score=0.0,
        embedder=_fake_embedder([1, 0]),
    )
    # Both chunks present, but only once each — c0 first with q0a as the
    # matched question.
    chunk_ids = [h["chunk_id"] for h in hits]
    assert chunk_ids == ["c0", "c1"]
    assert hits[0]["matched_question"] == "q0a"


def test_hyq_retrieve_threshold_returns_empty() -> None:
    matrix = np.stack([_unit([1.0, 0.0]), _unit([0.0, 1.0])])
    index = {
        "schema_version": 1,
        "mode": "hyq",
        "embed_model": "fake",
        "chunks": [
            {
                "chunk_id": "c0",
                "doc_id": "KB-A",
                "doc_title": "A",
                "doc_type": "policy",
                "section_heading": "h",
                "text": "A",
                "hyq": ["q"],
                "embedding_rows": [0],
            },
            {
                "chunk_id": "c1",
                "doc_id": "KB-B",
                "doc_title": "B",
                "doc_type": "policy",
                "section_heading": "h",
                "text": "B",
                "hyq": ["q"],
                "embedding_rows": [1],
            },
        ],
        "embeddings": matrix,
    }
    hits = retrieval.hyq_retrieve(
        "query",
        index,
        top_k=5,
        min_score=0.99,
        embedder=_fake_embedder([-1, 1]),
    )
    assert hits == []


def test_hyq_retrieve_empty_matrix() -> None:
    index = {
        "schema_version": 1,
        "mode": "hyq",
        "embed_model": "fake",
        "chunks": [],
        "embeddings": np.zeros((0, 2), dtype=np.float32),
    }
    hits = retrieval.hyq_retrieve("q", index, embedder=_fake_embedder([1, 0]))
    assert hits == []
