"""Step 5 (FAQ branch): draft a customer-safe response from a matched FAQ.

Refuses to run unless the latest ``data/working/faq_decisions.csv`` row
for this ticket has ``faq_match_found=true`` and a recommended next
step of ``draft-faq-response``. The text is templated from the FAQ's
``solution_steps`` and ``required_customer_info`` so it never invents
a fix.

Run from the repo root::

    uv run python skills/draft-faq-response/scripts/draft_faq_response.py \\
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

SKILL_NAME = "draft-faq-response"
DRAFTS_TABLE = "customer_response_drafts"


def load_response_context(data_dir: Path, out_dir: Path, ticket_id: str) -> dict:
    """Return ticket + matched FAQ + the upstream FAQ decision.

    Raises ``LookupError`` with a clear stderr-friendly message if no
    FAQ decision row exists, the decision was no-match, or the FAQ id
    does not resolve to an entry in the knowledge base.
    """

    ticket = require_ticket(data_dir, ticket_id)
    decision = latest_working_row(out_dir, "faq_decisions", ticket_id)
    if decision is None:
        raise LookupError(f"no FAQ decision found for ticket {ticket_id}. Run the check-faq-resolution skill first.")
    if str(decision.get("faq_match_found", "")).lower() != "true":
        raise LookupError(
            f"FAQ decision for ticket {ticket_id} reports no match — "
            f"draft-faq-response cannot proceed. "
            f"Route to escalate-to-specialist instead."
        )
    next_step = decision.get("recommended_next_step", "")
    if next_step and next_step != "draft-faq-response":
        raise LookupError(
            f"FAQ decision for ticket {ticket_id} recommends '{next_step}', "
            f"not 'draft-faq-response'. Refusing to draft."
        )

    faq_id = decision.get("faq_id", "")
    faqs = read_csv(data_dir, "raw/faq_knowledge_base.csv")
    matching = faqs.filter(pl.col("faq_id") == faq_id).to_dicts()
    if not matching:
        raise LookupError(
            f"FAQ id {faq_id!r} from working/faq_decisions.csv does not exist in raw/faq_knowledge_base.csv."
        )
    return {
        "ticket": ticket,
        "faq": matching[0],
        "faq_decision": decision,
    }


def draft_faq_response(context: dict) -> dict:
    """Build the draft response and supporting metadata.

    Returns a dict with both ``draft_text`` (full version, may include
    bracketed cues for the IT member) and ``sent_text`` (the same text
    cleaned for the customer). No internal-only specialist notes are
    ever included.
    """

    ticket = context["ticket"]
    faq = context["faq"]
    customer_first = (ticket.get("submitted_by_name") or "there").split()[0]

    impact = (ticket.get("business_impact_text") or "").strip()
    impact_line = (
        f"I understand this is impacting you ({impact}); we'd like to fix it quickly."
        if impact
        else "Thanks for reaching out — we'd like to get this fixed quickly."
    )

    steps = (faq.get("solution_steps") or "").strip()
    required_info = (faq.get("required_customer_info") or "").strip()

    follow_up_request = "Could you reply once you've tried these steps and let me know whether the issue is resolved?"
    customer_action = (
        f"Try the steps above and confirm whether they fix the issue. "
        f"If you can include {required_info.lower()}, that will speed things up if we need to dig further."
        if required_info
        else "Try the steps above and confirm whether they fix the issue."
    )

    draft_text = (
        f"Hi {customer_first},\n\n"
        f'Thanks for the ticket about "{ticket.get("subject", "your issue")}". '
        f"{impact_line}\n\n"
        f"This looks like a known issue ({faq.get('issue_pattern', 'standard pattern')}). "
        f"Here's what to try:\n\n"
        f"{steps}\n\n"
        f"{customer_action}\n\n"
        f"{follow_up_request}\n\n"
        f"Best,\nIT Support"
    )

    sent_text = re.sub(r"\n{3,}", "\n\n", draft_text).strip()

    return {
        "draft_text": draft_text,
        "sent_text": sent_text,
        "customer_action_required": customer_action,
        "included_context": pipe_join(
            [
                f"faq_id={faq.get('faq_id', '')}",
                f"issue_pattern={faq.get('issue_pattern', '')}",
                f"affected_system={ticket.get('affected_system', '')}",
            ]
        ),
        "follow_up_request": follow_up_request,
    }


def quality_check_response(draft: dict) -> dict:
    """Return ``{"ok": bool, "notes": str}``.

    Flags missing customer action, missing follow-up request, presence
    of internal/specialist phrasing in the customer-facing text, and
    drafts that do not include solution steps. ``notes`` is an empty
    string when the draft is OK.
    """

    issues: list[str] = []
    if not draft.get("customer_action_required"):
        issues.append("missing customer_action_required")
    if not draft.get("follow_up_request"):
        issues.append("missing follow_up_request")
    sent = (draft.get("sent_text") or "").lower()
    for forbidden in ("specialist", "internal note", "credential", "log line"):
        if forbidden in sent:
            issues.append(f"sent_text contains internal language: '{forbidden}'")
    if "try" not in sent and "steps" not in sent:
        issues.append("sent_text does not appear to include any actionable steps")
    return {"ok": not issues, "notes": "; ".join(issues)}


def build_response_row(context: dict) -> dict:
    """End-to-end build of a row for ``customer_response_drafts.csv``."""

    ticket = context["ticket"]
    faq = context["faq"]
    draft = draft_faq_response(context)
    qc = quality_check_response(draft)
    created_at = now_iso()
    safe_ts = re.sub(r"[^0-9]", "", created_at)
    message_id = f"MSG-{ticket['ticket_id']}-faq-{safe_ts}"
    return {
        "message_id": message_id,
        "ticket_id": ticket["ticket_id"],
        "created_at": created_at,
        "skill_name": SKILL_NAME,
        "message_source": "faq",
        "draft_text": draft["draft_text"],
        "sent_text": draft["sent_text"],
        "customer_action_required": draft["customer_action_required"],
        "included_context": draft["included_context"],
        "follow_up_request": draft["follow_up_request"],
        "quality_check_notes": qc["notes"],
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "raw/faq_knowledge_base.csv",
                "working/faq_decisions.csv",
            ]
        ),
        "decision_summary": (f"drafted FAQ-based response using {faq.get('faq_id', '')}; quality_ok={qc['ok']}"),
        "confidence_score": context["faq_decision"].get("match_confidence", ""),
    }


def write_customer_response(out_dir: Path, response: dict) -> None:
    append_csv_row(Path(out_dir) / f"{DRAFTS_TABLE}.csv", response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="data/working")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()

    try:
        context = load_response_context(data_dir, out_dir, args.ticket_id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    row = build_response_row(context)
    write_customer_response(out_dir, row)

    print(
        f"FAQ response drafted for {row['ticket_id']} (message_id={row['message_id']}):\n"
        f"  faq_id             : {context['faq']['faq_id']}\n"
        f"  quality check ok?  : {not row['quality_check_notes']}\n"
        f"  follow-up request  : {row['follow_up_request']}\n"
        f"\n--- draft ---\n{row['sent_text']}\n--- end draft ---\n"
        f"\nNext valid action: send to customer, then run "
        f"verify-feedback-close-or-reopen with the customer's reply."
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": row["ticket_id"],
            "created_at": row["created_at"],
            "skill_name": SKILL_NAME,
            "action": "faq_response_drafted",
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": row["confidence_score"],
            "notes": row["quality_check_notes"],
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
