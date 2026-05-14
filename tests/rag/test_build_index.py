"""Tests for ``rag.build_index``.

Pure-function tests (frontmatter parser, chunkers, write-index round-trip) run
locally; everything that calls an LLM or an embedding model hits the real
TritonAI gateway. No mocks. ``TRITONAI_API_KEY`` must be in ``.env``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from rag import build_index as bi

SAMPLE_DOC = """---
doc_id: KB-099
title: Tier-1 Password Reset Standard Operating Procedure
type: sop
owner: Example Team
last_updated: 2026-04-01
---

Intro paragraph explaining why identity verification matters.

## Identity verification

A Tier-1 analyst must complete one of the following before resetting a
password: confirm with the user's manager over Slack, perform a video
callback to the number on file, or trigger a Yubikey challenge.

## Reset procedure

Open the IDP admin console, choose Force credential rotation, and record
the ticket id in the Reason field.
"""


# ---------------------------------------------------------------------------
# Pure helpers (no LLM, no embeddings)
# ---------------------------------------------------------------------------


def test_parse_frontmatter_extracts_keys_and_body() -> None:
    meta, body = bi.parse_frontmatter(SAMPLE_DOC)
    assert meta["doc_id"] == "KB-099"
    assert meta["type"] == "sop"
    assert "Intro paragraph" in body
    assert "## Identity verification" in body


def test_parse_frontmatter_no_frontmatter_returns_empty_dict() -> None:
    meta, body = bi.parse_frontmatter("just text\n")
    assert meta == {}
    assert body == "just text\n"


def test_section_chunks_splits_on_h2_and_keeps_intro() -> None:
    _, body = bi.parse_frontmatter(SAMPLE_DOC)
    chunks = bi.section_chunks("KB-099", {"title": "Sample"}, body)
    headings = [c.section_heading for c in chunks]
    assert headings == ["<intro>", "Identity verification", "Reset procedure"]
    assert chunks[0].text.startswith("Intro paragraph")
    assert chunks[1].text.startswith("## Identity verification")


def test_section_chunks_doc_with_no_headings() -> None:
    chunks = bi.section_chunks("KB-X", {"title": "T"}, "Just one block of text.\n")
    assert len(chunks) == 1
    assert chunks[0].section_heading == "<whole>"


def test_naive_chunks_produces_overlapping_windows() -> None:
    body = " ".join(f"w{i}" for i in range(1000))
    chunks = bi.naive_chunks("KB-X", {"title": "T"}, body, window=300, overlap=50)
    assert len(chunks) == 4
    overlap = set(chunks[0].text.split()) & set(chunks[1].text.split())
    assert len(overlap) == 50


def test_naive_chunks_empty_body_returns_empty_list() -> None:
    assert bi.naive_chunks("KB-X", {}, "") == []


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


# ---------------------------------------------------------------------------
# Live tests — hit the real TritonAI gateway
# ---------------------------------------------------------------------------


def test_generate_hyq_returns_question_list() -> None:
    """The HyQ generator must produce N plausible questions per chunk."""
    chunk = bi.Chunk(
        chunk_id="KB-099#1",
        doc_id="KB-099",
        doc_title="Tier-1 Password Reset SOP",
        doc_type="sop",
        section_heading="Identity verification",
        text=(
            "A Tier-1 analyst must complete one of the following before "
            "resetting a password: confirm with the user's manager over "
            "Slack, perform a video callback, or trigger a Yubikey challenge."
        ),
    )
    questions = bi.generate_hyq(chunk, n=4)
    assert len(questions) == 4
    assert all(isinstance(q, str) and q.strip() for q in questions)
    assert all(q.strip().endswith("?") for q in questions)
    # All four should be distinct.
    assert len(set(questions)) == 4
    # At least one question should reference an identity-verification concept.
    joined = " ".join(q.lower() for q in questions)
    assert any(term in joined for term in ("verify", "verification", "identity", "reset", "password"))


def test_build_hyq_index_end_to_end(tmp_path: Path) -> None:
    """Build a real HyQ index for one synthetic doc against the live gateway."""
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "KB-099.md").write_text(SAMPLE_DOC, encoding="utf-8")
    kb = bi.load_kb(kb_dir)
    assert len(kb) == 1

    index, matrix = bi.build_hyq_index(kb, questions_per_chunk=3)
    assert index["mode"] == "hyq"
    # SAMPLE_DOC has 3 sections (intro + 2 headings) × 3 questions = 9 rows
    # at 1024 dims (the gateway's only embedding model).
    assert len(index["chunks"]) == 3
    assert matrix.shape == (9, 1024)
    # Each chunk must claim a contiguous, non-overlapping run of rows.
    seen: set[int] = set()
    for c in index["chunks"]:
        rows = c["embedding_rows"]
        assert len(rows) == 3
        assert all(r not in seen for r in rows)
        seen.update(rows)
    # Embeddings should be L2-normalised so cosine == dot product downstream.
    norms = np.linalg.norm(matrix, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_build_naive_index_end_to_end(tmp_path: Path) -> None:
    """Build a real naive index for one synthetic doc against the live gateway."""
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    body_paragraph = " ".join(f"sentence{i} about VPN tunnels and password resets and the IDP" for i in range(60))
    (kb_dir / "KB-100.md").write_text(
        f"---\ndoc_id: KB-100\ntitle: Long Doc\ntype: runbook\n---\n\n{body_paragraph}\n",
        encoding="utf-8",
    )
    kb = bi.load_kb(kb_dir)
    index, matrix = bi.build_naive_index(kb, window=120, overlap=20)
    assert index["mode"] == "naive"
    assert len(index["chunks"]) >= 2
    assert matrix.shape == (len(index["chunks"]), 1024)
    assert all(c["embedding_rows"] == [i] for i, c in enumerate(index["chunks"]))
    norms = np.linalg.norm(matrix, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)
