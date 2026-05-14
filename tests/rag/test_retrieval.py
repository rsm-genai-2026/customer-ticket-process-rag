"""End-to-end tests for ``rag.retrieval``.

Builds a small real index in ``tmp_path`` and exercises both modes against
the live TritonAI gateway. No mocks. ``TRITONAI_API_KEY`` must be in ``.env``.

The naive and HyQ fixtures are module-scoped so the LLM + embedding calls
happen once per test session, not once per test.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rag import build_index as bi
from rag import retrieval

# A tiny three-doc KB. Each doc has distinct vocabulary so retrieval is easy
# to reason about. All content is synthetic — no real customer or employee.
_DOCS = {
    "KB-901.md": """---
doc_id: KB-901
title: Password Reset SOP
type: sop
owner: Identity & Security
---

## Identity verification

Tier-1 must verify the requester's identity before resetting any password.
Acceptable methods are a Slack DM from the user's manager, a video callback
to the number on file in HR, or a Yubikey challenge through the IDP
self-service portal. Email-based verification is no longer accepted.

## Reset procedure

Use the IDP admin console to force credential rotation. Do not use the
manual password set option; it bypasses the audit log.
""",
    "KB-902.md": """---
doc_id: KB-902
title: VPN Maintenance Windows
type: policy
owner: Network Infrastructure
---

## Recurring windows

The MeshGuard VPN gateway is taken offline every Sunday between 02:00 and
04:00 UTC for routine maintenance. Tunnels established before the window
are dropped at the start.

## Emergency maintenance

Vendor may take the gateway offline with sixty minutes notice. Notifications
land in the network-alerts Slack channel.
""",
    "KB-903.md": """---
doc_id: KB-903
title: Inventory App Cycle Count
type: user_manual
owner: Operations Engineering
---

## Counting bins

Cycle Count uses a two-pane layout: left pane shows the system count, right
pane is your physical tally. Discrepancies above five units require
second-person sign-off before they post.

## Known quirks

Cycle Count occasionally double counts a SKU if the same bin appears twice
in the location bin map. Workaround is to rerun the cycle count for that
bin only.
""",
}


@pytest.fixture(scope="module")
def small_kb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    kb_dir = tmp_path_factory.mktemp("kb")
    for name, body in _DOCS.items():
        (kb_dir / name).write_text(body, encoding="utf-8")
    return kb_dir


@pytest.fixture(scope="module")
def naive_index(small_kb: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    kb = bi.load_kb(small_kb)
    index, matrix = bi.build_naive_index(kb, window=120, overlap=20)
    out_dir = tmp_path_factory.mktemp("naive")
    bi.write_index(index, matrix, out_dir)
    return retrieval.load_index(out_dir / "naive_index.json")


@pytest.fixture(scope="module")
def hyq_index(small_kb: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    kb = bi.load_kb(small_kb)
    index, matrix = bi.build_hyq_index(kb, questions_per_chunk=4)
    out_dir = tmp_path_factory.mktemp("hyq")
    bi.write_index(index, matrix, out_dir)
    return retrieval.load_index(out_dir / "hyq_index.json")


# ---------------------------------------------------------------------------
# load_index — pure file I/O, no LLM needed
# ---------------------------------------------------------------------------


def test_load_index_attaches_matrix(naive_index: dict) -> None:
    assert naive_index["mode"] == "naive"
    matrix = naive_index["embeddings"]
    assert matrix.ndim == 2
    assert matrix.shape[1] == 1024
    assert matrix.shape[0] == len(naive_index["chunks"])


def test_naive_retrieve_wrong_mode_raises(hyq_index: dict) -> None:
    with pytest.raises(ValueError):
        retrieval.naive_retrieve("q", hyq_index)


def test_hyq_retrieve_wrong_mode_raises(naive_index: dict) -> None:
    with pytest.raises(ValueError):
        retrieval.hyq_retrieve("q", naive_index)


# ---------------------------------------------------------------------------
# naive_retrieve — live
# ---------------------------------------------------------------------------


def test_naive_retrieve_finds_password_doc(naive_index: dict) -> None:
    hits = retrieval.naive_retrieve(
        "what's the right way to verify a password reset request",
        naive_index,
        top_k=3,
        min_score=0.0,
    )
    assert hits, "expected at least one hit"
    assert hits[0]["doc_id"] == "KB-901"
    # Scores are sorted descending.
    assert hits == sorted(hits, key=lambda h: -h["score"])


def test_naive_retrieve_finds_vpn_doc(naive_index: dict) -> None:
    hits = retrieval.naive_retrieve(
        "when is the VPN gateway down for maintenance",
        naive_index,
        top_k=3,
        min_score=0.0,
    )
    assert "KB-902" in [h["doc_id"] for h in hits]


def test_naive_retrieve_threshold_filters_off_topic_query(naive_index: dict) -> None:
    """An unrelated query should not clear a high cosine threshold."""
    hits = retrieval.naive_retrieve(
        "what is the capital of Argentina",
        naive_index,
        top_k=3,
        min_score=0.90,
    )
    assert hits == []


# ---------------------------------------------------------------------------
# hyq_retrieve — live
# ---------------------------------------------------------------------------


def test_hyq_retrieve_finds_password_doc(hyq_index: dict) -> None:
    hits = retrieval.hyq_retrieve(
        "how do I confirm an employee's identity before doing a password reset",
        hyq_index,
        top_k=3,
        min_score=0.0,
    )
    assert hits, "expected at least one hit"
    assert hits[0]["doc_id"] == "KB-901"
    assert "matched_question" in hits[0]
    assert hits[0]["matched_question"].strip().endswith("?")


def test_hyq_retrieve_finds_inventory_doc(hyq_index: dict) -> None:
    hits = retrieval.hyq_retrieve(
        "the cycle count is counting the same item twice",
        hyq_index,
        top_k=3,
        min_score=0.0,
    )
    assert "KB-903" in [h["doc_id"] for h in hits]


def test_hyq_retrieve_deduplicates_to_unique_chunks(hyq_index: dict) -> None:
    """Multiple top questions may live in the same chunk; result list dedupes."""
    hits = retrieval.hyq_retrieve(
        "what are the VPN maintenance hours",
        hyq_index,
        top_k=5,
        min_score=0.0,
    )
    chunk_ids = [h["chunk_id"] for h in hits]
    assert len(chunk_ids) == len(set(chunk_ids))


def test_hyq_retrieve_threshold_filters_off_topic_query(hyq_index: dict) -> None:
    hits = retrieval.hyq_retrieve(
        "what is the capital of Argentina",
        hyq_index,
        top_k=3,
        min_score=0.90,
    )
    assert hits == []
