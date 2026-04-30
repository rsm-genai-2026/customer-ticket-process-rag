"""Step 9: classify the customer's reply and pick close / reopen / close-unresolved.

Decision rules:

* Positive feedback              -> ``close_ticket``.
* Negative feedback, no prior reopen   -> ``reopen_and_escalate``.
* Negative feedback after a reopen     -> ``close_unresolved_vendor_followup``.

Requires a non-empty ``--feedback-text``. Refuses to run if no
``customer_response_drafts.csv`` or ``sent_messages.csv`` row exists
for the ticket — there must be a sent response for the customer to
be replying to.

Run from the repo root::

    uv run python skills/verify-feedback-close-or-reopen/scripts/verify_feedback.py \\
        --ticket-id TKT-00042 --feedback-text "Thanks, that fixed it!"
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
    DEFAULT_MODE,
    MODE_DEMO,
    MODE_LIVE,
    STATUS_OK,
    STATUS_SKIPPED,
    append_action_log,
    append_csv_row,
    default_step_id,
    default_workflow_run_id,
    emit_envelope,
    find_step_row,
    latest_working_row,
    make_envelope,
    needs_human_review,
    now_iso,
    pipe_join,
    replace_step_row,
    require_ticket,
)

SKILL_NAME = "verify-feedback-close-or-reopen"
FEEDBACK_TABLE = "feedback_decisions"

NEXT_ACTION_BY_DECISION = {
    "close_ticket": "audit-ticket-process",
    "reopen_and_escalate": "escalate-to-specialist",
    "close_unresolved_vendor_followup": "audit-ticket-process",
    "request_clarification": "request_clarification",
}

POSITIVE_PHRASES = [
    "fixed",
    "fixed it",
    "that fixed",
    "all set",
    "all sorted",
    "resolved",
    "works now",
    "working now",
    "no longer",
    "perfect",
    "thanks",
    "thank you",
    "appreciate",
    "great",
    "good to go",
    "looks good",
    "confirmed",
    "did the trick",
    "solved",
]
NEGATIVE_PHRASES = [
    "still broken",
    "still not",
    "still doesn't",
    "still doesn",
    "still happening",
    "didn't work",
    "did not work",
    "not working",
    "doesn't work",
    "does not work",
    "no luck",
    "no change",
    "issue persists",
    "problem persists",
    "still failing",
    "tried it, still",
]


def _classify(feedback_text: str) -> dict:
    """Return ``{"sentiment": positive|negative|ambiguous, "evidence": str}``."""

    text = feedback_text.lower().strip()
    pos_hits = [p for p in POSITIVE_PHRASES if p in text]
    neg_hits = [n for n in NEGATIVE_PHRASES if n in text]

    if neg_hits and not pos_hits:
        return {"sentiment": "negative", "evidence": pipe_join(neg_hits)}
    if pos_hits and not neg_hits:
        return {"sentiment": "positive", "evidence": pipe_join(pos_hits)}
    if pos_hits and neg_hits:
        # Negative phrases are higher signal than a polite "thanks".
        # If "still" or "not" markers exist, treat as negative.
        if any("still" in n or "not" in n or "didn" in n or "doesn" in n for n in neg_hits):
            return {
                "sentiment": "negative",
                "evidence": pipe_join(neg_hits) + " (overrode positive: " + pipe_join(pos_hits) + ")",
            }
        return {"sentiment": "positive", "evidence": pipe_join(pos_hits)}
    return {"sentiment": "ambiguous", "evidence": ""}


def classify_feedback(feedback_text: str) -> dict:
    """Public wrapper for ``_classify``. Validates non-empty input."""

    if not feedback_text or not feedback_text.strip():
        raise ValueError("feedback_text must not be empty")
    return _classify(feedback_text)


def load_feedback_context(
    data_dir: Path,
    out_dir: Path,
    ticket_id: str,
    feedback_text: str,
    *,
    workflow_run_id: str | None = None,
) -> dict:
    """Load ticket, latest sent draft, and any prior feedback for this run."""

    if not feedback_text or not feedback_text.strip():
        raise ValueError("feedback_text must not be empty")
    ticket = require_ticket(data_dir, ticket_id)
    draft = latest_working_row(out_dir, "customer_response_drafts", ticket_id, workflow_run_id=workflow_run_id)
    if draft is None:
        raise LookupError(
            f"no customer response draft found for ticket {ticket_id}. "
            f"Run draft-faq-response or draft-specialist-response first."
        )
    sent = latest_working_row(out_dir, "sent_messages", ticket_id, workflow_run_id=workflow_run_id)
    if sent is None:
        raise LookupError(
            f"no sent customer response found for ticket {ticket_id}. "
            f"Run send-customer-response before verifying customer feedback."
        )
    prior_feedback = latest_working_row(out_dir, "feedback_decisions", ticket_id, workflow_run_id=workflow_run_id)
    return {
        "ticket": ticket,
        "draft": draft,
        "sent": sent,
        "prior_feedback": prior_feedback,
        "feedback_text": feedback_text.strip(),
    }


def decide_next_action(context: dict, classification: dict) -> dict:
    """Pick ``close_ticket`` / ``reopen_and_escalate`` / ``close_unresolved_vendor_followup``.

    The reopen guard prevents infinite loops: if the prior feedback row
    for this ticket already had ``reopened_flag=true``, a second negative
    reply closes-as-unresolved instead of reopening again.
    """

    sentiment = classification["sentiment"]
    prior = context["prior_feedback"] or {}
    prior_reopened = str(prior.get("reopened_flag", "")).lower() == "true"

    if sentiment == "positive":
        return {
            "resolution_accepted": True,
            "verified_rejection": False,
            "reopened_flag": False,
            "rejection_reason": "",
            "next_action": "close_ticket",
            "closure_reason": "customer confirmed resolution",
            "verification_notes": (f"customer feedback classified positive based on: {classification['evidence']}"),
        }
    if sentiment == "ambiguous":
        return {
            "resolution_accepted": False,
            "verified_rejection": False,
            "reopened_flag": False,
            "rejection_reason": "",
            "next_action": "request_clarification",
            "closure_reason": "",
            "verification_notes": (
                "customer feedback was ambiguous — neither clearly positive nor negative; "
                "ask the customer to confirm whether the issue is resolved"
            ),
        }
    # Negative path
    rejection_reason = "customer reports issue still present"
    if prior_reopened:
        return {
            "resolution_accepted": False,
            "verified_rejection": True,
            "reopened_flag": False,
            "rejection_reason": rejection_reason,
            "next_action": "close_unresolved_vendor_followup",
            "closure_reason": (
                "second rejection after prior reopen; closing unresolved and tracking external follow-up"
            ),
            "verification_notes": (
                f"customer feedback classified negative based on: "
                f"{classification['evidence']}; ticket already reopened once, "
                f"so the loop is closed"
            ),
        }
    return {
        "resolution_accepted": False,
        "verified_rejection": True,
        "reopened_flag": True,
        "rejection_reason": rejection_reason,
        "next_action": "reopen_and_escalate",
        "closure_reason": "",
        "verification_notes": (
            f"customer feedback classified negative based on: "
            f"{classification['evidence']}; reopening and routing to specialist"
        ),
    }


def build_feedback_decision(context: dict, it_member_id: str) -> dict:
    """End-to-end build of a row for ``feedback_decisions.csv``."""

    classification = classify_feedback(context["feedback_text"])
    decision = decide_next_action(context, classification)
    return {
        "ticket_id": context["ticket"]["ticket_id"],
        "created_at": now_iso(),
        "skill_name": SKILL_NAME,
        "resolution_accepted": decision["resolution_accepted"],
        "customer_feedback_text": context["feedback_text"],
        "rejection_reason": decision["rejection_reason"],
        "verified_rejection": decision["verified_rejection"],
        "reopened_flag": decision["reopened_flag"],
        "verified_by_it_member_id": it_member_id,
        "verification_notes": decision["verification_notes"],
        "next_action": decision["next_action"],
        "closure_reason": decision["closure_reason"],
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "working/customer_response_drafts.csv",
                "working/sent_messages.csv",
                "working/feedback_decisions.csv (prior)",
            ]
        ),
        "decision_summary": (f"sentiment={classification['sentiment']}; next={decision['next_action']}"),
    }


def write_feedback_decision(out_dir: Path, decision: dict) -> None:
    append_csv_row(Path(out_dir) / f"{FEEDBACK_TABLE}.csv", decision)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--feedback-text", required=True)
    parser.add_argument("--it-member-id", default="")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="data/working")
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument("--step-id", default="")
    parser.add_argument("--mode", choices=[MODE_LIVE, MODE_DEMO], default=DEFAULT_MODE)
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--idempotency-mode", choices=["skip", "replace"], default="skip")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    workflow_run_id = args.workflow_run_id or default_workflow_run_id()
    read_workflow_run_id = workflow_run_id if args.workflow_run_id else None
    step_id = args.step_id or default_step_id(SKILL_NAME)

    def _err_envelope(code: str, message: str, *, next_action: str = "") -> dict:
        return make_envelope(
            status="error",
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action=next_action,
            error={"code": code, "message": message},
        )

    if not args.feedback_text.strip():
        env = _err_envelope("empty_feedback", "--feedback-text must not be empty")
        emit_envelope(env, as_json=args.as_json, text_summary="error: --feedback-text must not be empty")
        if not args.as_json:
            print("error: --feedback-text must not be empty", file=sys.stderr)
        return 2

    existing = find_step_row(out_dir, FEEDBACK_TABLE, workflow_run_id, step_id)
    if existing and args.idempotency_mode == "skip":
        next_action = NEXT_ACTION_BY_DECISION.get(existing.get("next_action", ""), "audit-ticket-process")
        env = make_envelope(
            status=STATUS_SKIPPED,
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action=next_action,
            outputs={"existing_row": existing},
            artifact_refs=[f"working/{FEEDBACK_TABLE}.csv"],
        )
        emit_envelope(
            env,
            as_json=args.as_json,
            text_summary=(
                f"feedback decision already recorded for "
                f"workflow_run_id={workflow_run_id} step_id={step_id} — skipping."
            ),
        )
        return 0

    try:
        context = load_feedback_context(
            data_dir,
            out_dir,
            args.ticket_id,
            args.feedback_text,
            workflow_run_id=read_workflow_run_id,
        )
    except KeyError as exc:
        env = _err_envelope("ticket_not_found", str(exc))
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        env = _err_envelope("missing_data", str(exc))
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except LookupError as exc:
        env = _err_envelope(
            "missing_upstream",
            str(exc),
            next_action="draft-faq-response",
        )
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        env = _err_envelope("invalid_input", str(exc))
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    row = build_feedback_decision(context, args.it_member_id)
    row["workflow_run_id"] = workflow_run_id
    row["step_id"] = step_id
    if args.idempotency_mode == "replace":
        replace_step_row(
            Path(out_dir) / f"{FEEDBACK_TABLE}.csv",
            row,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
    else:
        write_feedback_decision(out_dir, row)

    review_required = needs_human_review(
        None, extra=row["next_action"] in {"request_clarification", "close_unresolved_vendor_followup"}
    )

    next_action_for_orchestrator = NEXT_ACTION_BY_DECISION.get(row["next_action"], "audit-ticket-process")
    next_hint_text = {
        "close_ticket": "Run audit-ticket-process to confirm the closed state.",
        "reopen_and_escalate": "Run escalate-to-specialist for re-investigation.",
        "close_unresolved_vendor_followup": "Run audit-ticket-process; this ticket is closed unresolved.",
        "request_clarification": "Reply to the customer asking for a clearer accept/reject signal.",
    }.get(row["next_action"], "")

    text_summary = (
        f"Feedback verified for {row['ticket_id']}:\n"
        f"  resolution accepted? : {row['resolution_accepted']}\n"
        f"  verified rejection?  : {row['verified_rejection']}\n"
        f"  reopened?            : {row['reopened_flag']}\n"
        f"  next action          : {row['next_action']}\n"
        f"  closure reason       : {row['closure_reason'] or '(n/a)'}\n"
        f"  verification notes   : {row['verification_notes']}\n"
        f"  review required?     : {review_required}\n"
        f"\nNext valid action: {next_hint_text}"
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": row["ticket_id"],
            "created_at": row["created_at"],
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": row["next_action"],
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": "",
            "needs_human_review": "true" if review_required else "false",
            "notes": row["verification_notes"],
        },
    )

    env = make_envelope(
        status=STATUS_OK,
        skill_name=SKILL_NAME,
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        ticket_id=row["ticket_id"],
        next_action=next_action_for_orchestrator,
        confidence=None,
        review_required=review_required,
        artifact_refs=[f"working/{FEEDBACK_TABLE}.csv"],
        outputs={
            "resolution_accepted": row["resolution_accepted"],
            "verified_rejection": row["verified_rejection"],
            "reopened_flag": row["reopened_flag"],
            "feedback_decision": row["next_action"],
            "closure_reason": row["closure_reason"],
            "verification_notes": row["verification_notes"],
        },
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
