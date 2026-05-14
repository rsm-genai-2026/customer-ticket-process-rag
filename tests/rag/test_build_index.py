"""Tests for the chunkers, frontmatter parser, and HyQ generator mock."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from rag import build_index as bi


SAMPLE_DOC = """---
doc_id: KB-099
title: Sample Policy
type: policy
owner: Example Team
last_updated: 2026-04-01
---

Intro paragraph that lives above the first heading.

## First section

Body of the first section.

Another paragraph.

## Second section

Body of the second section.
"""


def test_parse_frontmatter_extracts_keys_and_body() -> None:
    meta, body = bi.parse_frontmatter(SAMPLE_DOC)
    assert meta["doc_id"] == "KB-099"
    assert meta["title"] == "Sample Policy"
    assert meta["type"] == "policy"
    assert "Intro paragraph" in body
    assert "## First section" in body


def test_parse_frontmatter_no_frontmatter_returns_empty_dict() -> None:
    meta, body = bi.parse_frontmatter("just text\n")
    assert meta == {}
    assert body == "just text\n"


def test_section_chunks_splits_on_h2_and_keeps_intro() -> None:
    _, body = bi.parse_frontmatter(SAMPLE_DOC)
    chunks = bi.section_chunks("KB-099", {"title": "Sample"}, body)
    headings = [c.section_heading for c in chunks]
    assert headings == ["<intro>", "First section", "Second section"]
    assert chunks[0].text.startswith("Intro paragraph")
    assert chunks[1].text.startswith("## First section")


def test_section_chunks_doc_with_no_headings() -> None:
    chunks = bi.section_chunks("KB-X", {"title": "T"}, "Just one block of text.\n")
    assert len(chunks) == 1
    assert chunks[0].section_heading == "<whole>"


def test_naive_chunks_produces_overlapping_windows() -> None:
    body = " ".join(f"w{i}" for i in range(1000))
    chunks = bi.naive_chunks("KB-X", {"title": "T"}, body, window=300, overlap=50)
    # 1000 tokens, step 250 → starts at 0, 250, 500, 750; the 1000-token doc
    # finishes inside the fifth window starting at 750, so we expect 4 chunks.
    assert len(chunks) == 4
    first_tokens = chunks[0].text.split()
    second_tokens = chunks[1].text.split()
    overlap = set(first_tokens) & set(second_tokens)
    assert len(overlap) == 50


def test_naive_chunks_empty_body_returns_empty_list() -> None:
    assert bi.naive_chunks("KB-X", {}, "") == []


def test_generate_hyq_uses_env_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HYQ_QUESTIONS_MOCK_JSON",
        json.dumps({"questions": ["Q1?", "Q2?", "Q3?", "Q4?", "Q5?", "Q6?"]}),
    )
    chunk = bi.Chunk(
        chunk_id="x",
        doc_id="d",
        doc_title="t",
        doc_type="policy",
        section_heading="s",
        text="body",
    )
    out = bi.generate_hyq(chunk, n=3)
    assert out == ["Q1?", "Q2?", "Q3?"]


def test_build_hyq_index_with_fake_embedder_and_mocked_generator(tmp_path: Path) -> None:
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "KB-099.md").write_text(SAMPLE_DOC, encoding="utf-8")

    kb = bi.load_kb(kb_dir)
    assert len(kb) == 1
    doc_id, meta, _ = kb[0]
    assert doc_id == "KB-099"
    assert meta["type"] == "policy"

    # Force a deterministic question count and matrix.
    def fake_gen(chunk: bi.Chunk, n: int, *, model: str) -> list[str]:
        return [f"what is {chunk.section_heading}?" for _ in range(n)]

    def fake_embed(texts: list[str], model: str = "x") -> np.ndarray:
        # 8-dim unit vectors derived from text length so each row is unique.
        arr = np.zeros((len(texts), 8), dtype=np.float32)
        for i, t in enumerate(texts):
            arr[i, i % 8] = 1.0 + 0.001 * len(t)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / norms

    index, matrix = bi.build_hyq_index(
        kb,
        questions_per_chunk=2,
        embedder=fake_embed,
        generator=fake_gen,
    )
    assert index["mode"] == "hyq"
    # 3 chunks (intro + 2 sections) × 2 questions = 6 rows.
    assert matrix.shape == (6, 8)
    assert len(index["chunks"]) == 3
    assert index["chunks"][0]["embedding_rows"] == [0, 1]
    assert index["chunks"][1]["embedding_rows"] == [2, 3]
    assert index["chunks"][2]["embedding_rows"] == [4, 5]


def test_build_naive_index_with_fake_embedder(tmp_path: Path) -> None:
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    body_paragraph = " ".join(f"tok{i}" for i in range(700))
    (kb_dir / "KB-100.md").write_text(
        f"---\ndoc_id: KB-100\ntitle: Long Doc\ntype: runbook\n---\n\n{body_paragraph}\n",
        encoding="utf-8",
    )

    def fake_embed(texts: list[str], model: str = "x") -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32) / 2.0

    kb = bi.load_kb(kb_dir)
    index, matrix = bi.build_naive_index(kb, window=300, overlap=50, embedder=fake_embed)
    assert index["mode"] == "naive"
    assert matrix.shape[0] == len(index["chunks"])
    assert all(c["embedding_rows"] == [i] for i, c in enumerate(index["chunks"]))


def test_write_index_round_trip(tmp_path: Path) -> None:
    index = {
        "schema_version": 1,
        "mode": "naive",
        "embed_model": "fake",
        "built_at": "now",
        "chunks": [
            {
                "chunk_id": "a",
                "doc_id": "KB-1",
                "doc_title": "T",
                "doc_type": "policy",
                "section_heading": "h",
                "text": "t",
                "hyq": [],
                "embedding_rows": [0],
            }
        ],
    }
    matrix = np.array([[1.0, 0.0]], dtype=np.float32)
    json_path, npy_path = bi.write_index(index, matrix, tmp_path)
    assert json_path.exists() and npy_path.exists()
    on_disk = json.loads(json_path.read_text())
    assert on_disk["embeddings_file"] == "naive_embeddings.npy"
    reloaded = np.load(npy_path)
    assert np.array_equal(reloaded, matrix)
