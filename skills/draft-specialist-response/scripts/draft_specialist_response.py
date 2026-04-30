"""Step 8: translate a specialist solution into a customer-facing message.

Refuses to run unless a row in ``data/working/specialist_solutions.csv``
exists for the ticket. When the upstream escalation says
``re-escalation after customer rejection``, the draft acknowledges that
the first attempt didn't fix the issue.

Run from the repo root::

    uv run python skills/draft-specialist-response/scripts/draft_specialist_response.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills.ticketing_common.ticketing_common import (  # noqa: E402
    append_action_log,
    append_csv_row,
    latest_working_row,
    now_iso,
    pipe_join,
    require_ticket,
)

SKILL_NAME = "draft-specialist-response"
DRAFTS_TABLE = "customer_response_drafts"


def load_specialist_response_context(data_dir: Path, out_dir: Path, ticket_id: str) -> dict:
    """Return ticket, specialist solution, and the originating escalation.

    Raises ``LookupError`` if there is no specialist solution to draft from.
    """

    ticket = require_ticket(data_dir, ticket_id)
    solution = latest_working_row(out_dir, "specialist_solutions", ticket_id)
    if solution is None:
        raise LookupError(
            f"no specialist solution found for ticket {ticket_id}. Run the investigate-specialist-solution skill first."
        )
    escalation = latest_working_row(out_dir, "escalation_decisions", ticket_id)
    return {
        "ticket": ticket,
        "specialist_solution": solution,
        "escalation": escalation,
    }


def _is_post_rejection(escalation: dict | None) -> bool:
    if not escalation:
        return False
    return "re-escalation" in (escalation.get("escalation_reason") or "").lower()


def draft_specialist_response(context: dict) -> dict:
    """Build the draft message and supporting metadata.

    The draft never includes ``specialist_notes`` or other internal
    fields. Post-rejection drafts include a single sentence acknowledging
    the earlier attempt.
    """

    ticket = context["ticket"]
    solution = context["specialist_solution"]
    customer_first = (ticket.get("submitted_by_name") or "there").split()[0]

    impact = (ticket.get("business_impact_text") or "").strip()
    impact_line = (
        f"I understand this is impacting you ({impact})." if impact else "Thanks for sticking with us on this."
    )

    revised_line = ""
    if _is_post_rejection(context.get("escalation")):
        revised_line = (
            "Apologies that our first attempt didn't fully resolve this — our "
            "specialist took another look and adjusted the approach.\n\n"
        )

    summary = (solution.get("solution_summary") or "").strip()
    customer_action = (solution.get("customer_action_required") or "Confirm whether this resolves the issue.").strip()
    follow_up_request = "Could you reply once you've tried this and let me know whether the issue is resolved?"

    draft_text = (
        f"Hi {customer_first},\n\n"
        f'Thanks for the ticket about "{ticket.get("subject", "your issue")}". '
        f"{impact_line}\n\n"
        f"{revised_line}"
        f"Our specialist looked into this and recommends the following:\n\n"
        f"{summary}\n\n"
        f"What we'd like you to do: {customer_action}\n\n"
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
                f"specialist_id={solution.get('specialist_id', '')}",
                f"root_cause={solution.get('root_cause', '')[:140]}",
                f"affected_system={ticket.get('affected_system', '')}",
                f"post_rejection={_is_post_rejection(context.get('escalation'))}",
            ]
        ),
        "follow_up_request": follow_up_request,
        "is_post_rejection": _is_post_rejection(context.get("escalation")),
    }


def quality_check_response(draft: dict) -> dict:
    """Return ``{"ok": bool, "notes": str}`` describing any issues found.

    Mirrors the FAQ-response quality check but adds a check that
    post-rejection drafts include the apology/acknowledgement line.
    """

    issues: list[str] = []
    if not draft.get("customer_action_required"):
        issues.append("missing customer_action_required")
    if not draft.get("follow_up_request"):
        issues.append("missing follow_up_request")
    sent = (draft.get("sent_text") or "").lower()
    for forbidden in ("specialist_notes", "internal note", "credential", "log line"):
        if forbidden in sent:
            issues.append(f"sent_text contains internal language: '{forbidden}'")
    if draft.get("is_post_rejection") and "first attempt" not in sent:
        issues.append("post-rejection draft is missing acknowledgement of earlier attempt")
    if (
        not draft.get("customer_action_required")
        or "confirm" not in (draft.get("customer_action_required") or "").lower()
    ):
        # The action should ask the customer to confirm something —
        # otherwise we won't get a clean accept/reject signal back.
        if "reply" not in (draft.get("customer_action_required") or "").lower():
            issues.append("customer_action_required does not ask the customer to confirm or reply")
    return {"ok": not issues, "notes": "; ".join(issues)}


def build_response_row(context: dict) -> dict:
    """Build the row written to ``customer_response_drafts.csv``."""

    ticket = context["ticket"]
    solution = context["specialist_solution"]
    draft = draft_specialist_response(context)
    qc = quality_check_response(draft)
    created_at = now_iso()
    safe_ts = re.sub(r"[^0-9]", "", created_at)
    message_id = f"MSG-{ticket['ticket_id']}-spec-{safe_ts}"
    return {
        "message_id": message_id,
        "ticket_id": ticket["ticket_id"],
        "created_at": created_at,
        "skill_name": SKILL_NAME,
        "message_source": "specialist_solution",
        "draft_text": draft["draft_text"],
        "sent_text": draft["sent_text"],
        "customer_action_required": draft["customer_action_required"],
        "included_context": draft["included_context"],
        "follow_up_request": draft["follow_up_request"],
        "quality_check_notes": qc["notes"],
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "working/specialist_solutions.csv",
                "working/escalation_decisions.csv",
            ]
        ),
        "decision_summary": (
            f"drafted specialist response from solution by "
            f"{solution.get('specialist_id', '')}; "
            f"post_rejection={draft['is_post_rejection']}; quality_ok={qc['ok']}"
        ),
        "confidence_score": solution.get("confidence_score", ""),
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
        context = load_specialist_response_context(data_dir, out_dir, args.ticket_id)
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
        f"Specialist response drafted for {row['ticket_id']} "
        f"(message_id={row['message_id']}):\n"
        f"  post-rejection?    : {context['escalation'] and 're-escalation' in (context['escalation'].get('escalation_reason') or '').lower()}\n"
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
            "action": "specialist_response_drafted",
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": row["confidence_score"],
            "notes": row["quality_check_notes"],
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
