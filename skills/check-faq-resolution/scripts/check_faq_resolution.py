"""Step 3: decide whether an FAQ entry resolves a ticket.

Deterministic logic:

* Load the triage decision (working dir preferred; ``processed/ticket_triage.csv``
  fallback for synthetic historical examples).
* Filter to active FAQ entries.
* Score each FAQ:
    * +3 if FAQ category equals the ticket's assigned category.
    * +2 if FAQ system equals the ticket's affected system.
    * +text-overlap score (number of distinct meaningful tokens that
      appear in both the ticket text and the FAQ symptoms / issue
      pattern, capped at 6).
* Top-scoring FAQ wins. If the top score is below the threshold (5),
  no match is declared. If a match is found but the customer has not
  supplied the required info, the recommended next step is still
  escalation.

Run from the repo root::

    uv run python skills/check-faq-resolution/scripts/check_faq_resolution.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills.ticketing_common.ticketing_common import (  # noqa: E402
    append_action_log,
    append_csv_row,
    latest_working_row,
    now_iso,
    pipe_join,
    read_csv,
    require_ticket,
)

SKILL_NAME = "check-faq-resolution"
FAQ_DECISIONS_TABLE = "faq_decisions"
MATCH_SCORE_THRESHOLD = 5

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "is",
    "it",
    "this",
    "that",
    "i",
    "we",
    "you",
    "my",
    "our",
    "be",
    "been",
    "are",
    "at",
    "as",
    "by",
    "from",
    "but",
    "not",
    "no",
    "yes",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "was",
    "were",
    "will",
    "would",
    "should",
    "can",
    "could",
    "if",
    "when",
    "while",
    "any",
    "some",
    "all",
    "very",
    "every",
    "other",
    "than",
    "then",
    "so",
    "just",
    "also",
    "out",
    "over",
    "into",
    "about",
    "after",
    "before",
    "again",
    "still",
    "now",
    "here",
    "there",
    "ticket",
    "issue",
    "problem",
}


def _tokens(text: str) -> set[str]:
    """Lowercase tokenize, drop short/stopword tokens.

    Used both for ticket text and FAQ symptoms/issue_pattern. Tokens
    shorter than 4 chars are dropped to suppress noise.
    """

    if not text:
        return set()
    raw = re.findall(r"[a-zA-Z]+", text.lower())
    return {t for t in raw if len(t) >= 4 and t not in STOPWORDS}


def _ticket_text(ticket: dict) -> str:
    return " ".join(
        ticket.get(k, "") or ""
        for k in (
            "subject",
            "description",
            "error_or_symptom_detail",
            "steps_already_tried",
            "expected_outcome",
            "business_impact_text",
        )
    )


def load_faq_context(data_dir: Path, out_dir: Path, ticket_id: str) -> dict:
    """Return everything needed to make an FAQ decision.

    Loads the ticket, the FAQ knowledge base (active rows only), and the
    triage decision. Triage is read first from ``data/working/`` so live
    skill output is preferred; if absent, falls back to the historical
    ``data/processed/ticket_triage.csv`` for synthetic examples. Raises
    ``LookupError`` if neither source has a row for this ticket.
    """

    ticket = require_ticket(data_dir, ticket_id)
    faqs = read_csv(data_dir, "raw/faq_knowledge_base.csv").filter(pl.col("active_flag"))

    triage = latest_working_row(out_dir, "triage_decisions", ticket_id)
    triage_source = "working/triage_decisions.csv"
    if triage is None:
        historical = read_csv(data_dir, "processed/ticket_triage.csv")
        rows = historical.filter(pl.col("ticket_id") == ticket_id).to_dicts()
        if not rows:
            raise LookupError(
                f"no triage decision available for ticket {ticket_id}. Run the classify-prioritize-ticket skill first."
            )
        triage = rows[0]
        triage_source = "processed/ticket_triage.csv"

    return {
        "ticket": ticket,
        "faqs": faqs,
        "triage": triage,
        "triage_source": triage_source,
    }


def build_search_terms(context: dict) -> list[str]:
    """Return the deduplicated list of search terms used for ranking.

    Uses the ticket's assigned category as a leading term so it always
    appears in ``search_terms``, even when no other tokens cross the
    minimum-length filter.
    """

    terms = list(_tokens(_ticket_text(context["ticket"])))
    terms.sort()
    cat = context["triage"].get("assigned_category", "")
    if cat:
        cat_token = cat.replace("_", " ")
        if cat_token not in terms:
            terms.insert(0, cat_token)
    return terms


def rank_faq_candidates(context: dict, faqs: pl.DataFrame) -> pl.DataFrame:
    """Score every active FAQ. Sorted descending by score then ``faq_id``."""

    ticket = context["ticket"]
    triage = context["triage"]
    ticket_tokens = _tokens(_ticket_text(ticket))
    assigned_category = triage.get("assigned_category", "")
    affected_system = ticket.get("affected_system", "")

    rows = []
    for faq in faqs.to_dicts():
        text = " ".join(faq.get(k, "") or "" for k in ("symptoms", "issue_pattern", "solution_steps"))
        faq_tokens = _tokens(text)
        overlap = ticket_tokens & faq_tokens
        score = min(len(overlap), 6)
        if faq.get("category") == assigned_category:
            score += 3
        if faq.get("system_name") == affected_system:
            score += 2
        rows.append(
            {
                "faq_id": faq["faq_id"],
                "category": faq.get("category", ""),
                "system_name": faq.get("system_name", ""),
                "issue_pattern": faq.get("issue_pattern", ""),
                "score": score,
                "overlap_terms": pipe_join(sorted(overlap)),
            }
        )
    return pl.DataFrame(rows).sort(["score", "faq_id"], descending=[True, False])


def _required_info_available(ticket: dict) -> bool:
    """Return True if the ticket has enough operational detail to apply an FAQ.

    Heuristic: the ``error_or_symptom_detail`` field must be non-empty
    AND at least one of ``steps_already_tried`` or
    ``business_impact_text`` must be non-empty. This mirrors what the
    generator already populates and is a deliberately strict bar.
    """

    detail = (ticket.get("error_or_symptom_detail") or "").strip()
    steps = (ticket.get("steps_already_tried") or "").strip()
    impact = (ticket.get("business_impact_text") or "").strip()
    return bool(detail) and bool(steps or impact)


def decide_faq_applicability(context: dict, ranked: pl.DataFrame) -> dict:
    """Return a dict of fields ready for ``faq_decisions.csv``."""

    if ranked.is_empty():
        return {
            "faq_match_found": False,
            "faq_id": "",
            "match_confidence": 0.10,
            "candidate_faq_ids": "",
            "faq_application_reason": "no active FAQ entries available",
            "recommended_next_step": "escalate-to-specialist",
        }

    top = ranked.row(0, named=True)
    runner_up = ranked.row(1, named=True) if ranked.height >= 2 else None
    margin = top["score"] - (runner_up["score"] if runner_up else 0)

    candidate_ids = ranked.head(5)["faq_id"].to_list()
    info_ok = _required_info_available(context["ticket"])

    if top["score"] < MATCH_SCORE_THRESHOLD:
        return {
            "faq_match_found": False,
            "faq_id": "",
            "match_confidence": round(min(0.45, 0.10 + 0.05 * top["score"]), 2),
            "candidate_faq_ids": pipe_join(candidate_ids),
            "faq_application_reason": (
                f"top FAQ score {top['score']} below threshold "
                f"{MATCH_SCORE_THRESHOLD}; symptoms do not match closely enough"
            ),
            "recommended_next_step": "escalate-to-specialist",
        }

    confidence = max(0.55, min(0.95, 0.50 + 0.05 * top["score"] + 0.05 * margin))
    if not info_ok:
        return {
            "faq_match_found": True,
            "faq_id": top["faq_id"],
            "match_confidence": round(confidence * 0.85, 2),
            "candidate_faq_ids": pipe_join(candidate_ids),
            "faq_application_reason": (
                f"FAQ {top['faq_id']} matches but customer has not provided "
                f"required information; cannot apply confidently"
            ),
            "recommended_next_step": "escalate-to-specialist",
        }

    return {
        "faq_match_found": True,
        "faq_id": top["faq_id"],
        "match_confidence": round(confidence, 2),
        "candidate_faq_ids": pipe_join(candidate_ids),
        "faq_application_reason": (
            f"FAQ {top['faq_id']} ({top['issue_pattern']}) matches category "
            f"{top['category']} and system {top['system_name']}"
            + (f" with overlap [{top['overlap_terms']}]" if top["overlap_terms"] else "")
        ),
        "recommended_next_step": "draft-faq-response",
    }


def write_faq_decision(out_dir: Path, decision: dict) -> None:
    append_csv_row(Path(out_dir) / f"{FAQ_DECISIONS_TABLE}.csv", decision)


def build_faq_decision_row(context: dict) -> dict:
    """End-to-end: search terms → ranked candidates → decision row."""

    search_terms = build_search_terms(context)
    ranked = rank_faq_candidates(context, context["faqs"])
    decision = decide_faq_applicability(context, ranked)
    info_ok = _required_info_available(context["ticket"])
    return {
        "ticket_id": context["ticket"]["ticket_id"],
        "created_at": now_iso(),
        "skill_name": SKILL_NAME,
        "faq_match_found": decision["faq_match_found"],
        "faq_id": decision["faq_id"],
        "match_confidence": decision["match_confidence"],
        "search_terms": pipe_join(search_terms),
        "candidate_faq_ids": decision["candidate_faq_ids"],
        "required_customer_info_available": info_ok,
        "faq_application_reason": decision["faq_application_reason"],
        "recommended_next_step": decision["recommended_next_step"],
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "raw/faq_knowledge_base.csv",
                context["triage_source"],
            ]
        ),
        "decision_summary": (
            f"match={decision['faq_match_found']}; "
            f"faq_id={decision['faq_id'] or '(none)'}; "
            f"next={decision['recommended_next_step']}"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="data/working")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()

    try:
        context = load_faq_context(data_dir, out_dir, args.ticket_id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    row = build_faq_decision_row(context)
    write_faq_decision(out_dir, row)

    print(
        f"FAQ check for {row['ticket_id']}:\n"
        f"  match found        : {row['faq_match_found']}\n"
        f"  matched FAQ        : {row['faq_id'] or '(none)'}\n"
        f"  match confidence   : {row['match_confidence']}\n"
        f"  required info ok?  : {row['required_customer_info_available']}\n"
        f"  candidates         : {row['candidate_faq_ids'] or '(none)'}\n"
        f"  reason             : {row['faq_application_reason']}\n"
        f"\nNext valid action: {row['recommended_next_step']}."
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": row["ticket_id"],
            "created_at": row["created_at"],
            "skill_name": SKILL_NAME,
            "action": "faq_decision",
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": row["match_confidence"],
            "notes": "",
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
