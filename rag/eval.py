"""Side-by-side comparison of Naive RAG vs. HyQ on a fixed query suite.

Runs ten hand-picked queries that paraphrase real support situations,
checks whether the expected KB doc shows up in the top-3 of each mode,
and prints a comparison table plus a hit-rate-at-3 summary. The point is
to give students a concrete, repeatable demo of when HyQ wins.

Usage
-----

    uv run python rag/eval.py
    uv run python rag/eval.py --top-k 5     # widen the cut-off
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rag.retrieval import hyq_retrieve, load_index, naive_retrieve  # noqa: E402

_INDEX_DIR = _REPO_ROOT / "rag" / "index"


@dataclass
class Query:
    text: str
    expected_doc: str
    rationale: str


SUITE: list[Query] = [
    Query(
        "my Mac keeps dropping the VPN every minute since the latest update",
        "KB-012",
        "post-incident review of the 2025-09 MeshGuard regression",
    ),
    Query(
        "what's the right way to confirm someone's identity before resetting their password",
        "KB-001",
        "Tier-1 password reset SOP — identity verification options",
    ),
    Query(
        "two of my invoices look like they belong to a different customer",
        "KB-013",
        "RCA on the QuotidianPay truncated-customer-id bug",
    ),
    Query(
        "the analytics dashboard says I'm not allowed to see something I could see yesterday",
        "KB-010",
        "Analytics Dashboard runbook — stale permissions cache",
    ),
    Query(
        "what happens if a customer pastes their password into the ticket body",
        "KB-005",
        "After-hours support policy — contaminated tickets",
    ),
    Query(
        "how long does the vendor say we have to notify a customer of a breach",
        "KB-006",
        "AuriLite IDP contract — breach notification SLA",
    ),
    Query(
        "who do I page if the data analytics specialist is unreachable",
        "KB-014",
        "escalation matrix and on-call rota",
    ),
    Query(
        "is there a system change scheduled that could explain the current portal slowness",
        "KB-015",
        "Q2 2026 change calendar — Customer Portal upgrade windows",
    ),
    Query(
        "the cycle count is counting the same item twice",
        "KB-017",
        "Inventory App user manual — known quirks for v3.2",
    ),
    Query(
        "what does ERR_HANDSHAKE_DRIFT mean and how do I fix it",
        "KB-018",
        "MeshGuard admin manual excerpt — client error codes table",
    ),
]


def _hits(results: list[dict], top_k: int) -> list[str]:
    return [r["doc_id"] for r in results[:top_k]]


def run(naive_path: Path, hyq_path: Path, top_k: int) -> int:
    naive = load_index(naive_path)
    hyq = load_index(hyq_path)

    print(f"{'Query':70} {'Expect':<7} {'Naive top-3':<32} {'HyQ top-3':<32} ")
    print("-" * 144)
    naive_hits = 0
    hyq_hits = 0
    for q in SUITE:
        n = _hits(naive_retrieve(q.text, naive, top_k=top_k, min_score=0.0), top_k)
        h = _hits(hyq_retrieve(q.text, hyq, top_k=top_k, min_score=0.0), top_k)
        n_ok = q.expected_doc in n
        h_ok = q.expected_doc in h
        naive_hits += int(n_ok)
        hyq_hits += int(h_ok)
        text = (q.text[:67] + "...") if len(q.text) > 70 else q.text
        marker = lambda ok: " " if ok else "x"  # noqa: E731
        print(f"{text:<70} {q.expected_doc:<7} {','.join(n):<30}{marker(n_ok)}  {','.join(h):<30}{marker(h_ok)}  ")

    n_rate = naive_hits / len(SUITE)
    h_rate = hyq_hits / len(SUITE)
    print()
    print(
        f"Hit-rate@{top_k}: NAIVE = {naive_hits}/{len(SUITE)} ({n_rate:.0%})   HyQ = {hyq_hits}/{len(SUITE)} ({h_rate:.0%})"
    )
    print(
        "Higher is better. HyQ embeds questions rather than chunk bodies, which usually helps when user queries are themselves questions."
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--naive", default=str(_INDEX_DIR / "naive_index.json"))
    p.add_argument("--hyq", default=str(_INDEX_DIR / "hyq_index.json"))
    p.add_argument("--top-k", type=int, default=3)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv(_REPO_ROOT / ".env")
    args = _build_parser().parse_args(argv)
    return run(Path(args.naive), Path(args.hyq), args.top_k)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
