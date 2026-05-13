"""Step 8.5: human-in-the-loop review of the specialist-response draft.

Inserts a supervisor checkpoint between :mod:`draft_specialist_response`
and :mod:`send_customer_response`. The supervisor either approves the
LLM draft (optionally editing the text) or rejects it, which sends the
ticket back to :mod:`investigate_specialist_solution` for one more
attempt before approval is forced on the second pass.

Two invocation modes, one script:

* Mode 1 — *awaiting decision*. No ``--decision`` flag. Reads the
  latest draft row and emits an ``awaiting_input`` envelope so the
  orchestrator can surface the draft to a supervisor.
* Mode 2 — *decision applied*. ``--decision approve`` or
  ``--decision reject``, with optional ``--edited-text`` and
  ``--reviewer-notes``. Writes a row to
  ``data/working/specialist_draft_reviews.csv`` and, on approve+edit,
  patches the existing draft's ``sent_text`` so the next step sends
  the supervisor's edited copy.

Run from the repo root::

    uv run python automations/review-specialist-draft/scripts/review_specialist_draft.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.ticketing_common import (  # noqa: E402
    STATUS_OK,
    append_action_log,
    append_csv_row,
    default_step_id,
    default_workflow_run_id,
    emit_envelope,
    emit_error,
    latest_working_row,
    make_envelope,
    make_skill_parser,
    now_iso,
    working_lock,
)

SKILL_NAME = "review-specialist-draft"
REVIEWS_TABLE = "specialist_draft_reviews"
DRAFTS_TABLE = "customer_response_drafts"
SELF_NEXT_ACTION = "review-specialist-draft"
APPROVE_NEXT_ACTION = "send-customer-response"
REJECT_NEXT_ACTION = "investigate-specialist-solution"
STATUS_AWAITING = "awaiting_input"
MAX_REJECTS = 1


def _prior_reject_count(out_dir: Path, ticket_id: str, workflow_run_id: str) -> int:
    """Return how many times this ticket has already been rejected in this run."""

    path = Path(out_dir) / f"{REVIEWS_TABLE}.csv"
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return sum(
            1
            for row in reader
            if row.get("ticket_id") == ticket_id
            and row.get("workflow_run_id") == workflow_run_id
            and row.get("decision") == "reject"
        )


def _patch_sent_text(out_dir: Path, draft_row: dict, edited_text: str) -> None:
    """Rewrite the matching draft row's ``sent_text`` in place."""

    path = Path(out_dir) / f"{DRAFTS_TABLE}.csv"
    if not path.exists():
        return
    message_id = draft_row.get("message_id", "")
    with working_lock(path.parent):
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = list(reader.fieldnames or [])
            rows = list(reader)
        if "sent_text" not in header or not message_id:
            return
        for row in rows:
            if row.get("message_id") == message_id:
                row["sent_text"] = edited_text
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)


def _emit_awaiting(args, draft_row: dict, retry_count: int) -> int:
    env = make_envelope(
        status=STATUS_AWAITING,
        skill_name=SKILL_NAME,
        workflow_run_id=args.workflow_run_id or default_workflow_run_id(),
        step_id=args.step_id or default_step_id(SKILL_NAME),
        ticket_id=args.ticket_id,
        next_action=SELF_NEXT_ACTION,
        review_required=True,
        outputs={
            "message_id": draft_row.get("message_id", ""),
            "draft_text": draft_row.get("sent_text") or draft_row.get("draft_text", ""),
            "customer_action_required": draft_row.get("customer_action_required", ""),
            "follow_up_request": draft_row.get("follow_up_request", ""),
            "quality_check_notes": draft_row.get("quality_check_notes", ""),
            "retry_count": retry_count,
            "max_rejects": MAX_REJECTS,
        },
        artifact_refs=[f"working/{DRAFTS_TABLE}.csv"],
    )
    emit_envelope(
        env,
        as_json=args.as_json,
        text_summary=(
            f"Specialist draft for {args.ticket_id} is awaiting supervisor review "
            f"(retry_count={retry_count}/{MAX_REJECTS}).\n"
            f"\n--- draft ---\n{draft_row.get('sent_text', '')}\n--- end draft ---"
        ),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = make_skill_parser(__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--decision", choices=["", "approve", "reject"], default="")
    parser.add_argument("--edited-text", default="")
    parser.add_argument("--reviewer-notes", default="")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).resolve()
    workflow_run_id = args.workflow_run_id or default_workflow_run_id()
    read_workflow_run_id = workflow_run_id if args.workflow_run_id else None
    step_id = args.step_id or default_step_id(SKILL_NAME)

    err_kwargs = {
        "skill_name": SKILL_NAME,
        "workflow_run_id": workflow_run_id,
        "step_id": step_id,
        "ticket_id": args.ticket_id,
        "as_json": args.as_json,
    }

    draft_row = latest_working_row(out_dir, DRAFTS_TABLE, args.ticket_id, workflow_run_id=read_workflow_run_id)
    if not draft_row or draft_row.get("message_source") != "specialist_solution":
        return emit_error(
            **err_kwargs,
            error_code="missing_upstream",
            message=(
                f"no specialist-solution draft found for ticket {args.ticket_id}. Run draft-specialist-response first."
            ),
            exit_code=3,
            next_action="draft-specialist-response",
        )

    prior_rejects = _prior_reject_count(out_dir, args.ticket_id, workflow_run_id)

    if not args.decision:
        return _emit_awaiting(args, draft_row, retry_count=prior_rejects)

    # Apply decision.
    original_text = draft_row.get("sent_text") or draft_row.get("draft_text") or ""
    edited_text = args.edited_text.strip() or original_text
    forced_approve = False
    decision = args.decision

    if decision == "reject" and prior_rejects >= MAX_REJECTS:
        # The supervisor already rejected once. The next iteration of the
        # workflow must not loop forever — force approval and record why.
        decision = "approve"
        forced_approve = True

    decision_row = {
        "ticket_id": args.ticket_id,
        "created_at": now_iso(),
        "skill_name": SKILL_NAME,
        "message_id": draft_row.get("message_id", ""),
        "decision": decision,
        "original_text": original_text,
        "edited_text": edited_text if decision == "approve" else "",
        "reviewer_notes": args.reviewer_notes,
        "retry_count": prior_rejects,
        "forced_approve": "true" if forced_approve else "false",
        "workflow_run_id": workflow_run_id,
        "step_id": step_id,
    }
    append_csv_row(out_dir / f"{REVIEWS_TABLE}.csv", decision_row)

    if decision == "approve" and edited_text != original_text:
        _patch_sent_text(out_dir, draft_row, edited_text)

    next_action = APPROVE_NEXT_ACTION if decision == "approve" else REJECT_NEXT_ACTION

    append_action_log(
        out_dir,
        {
            "ticket_id": args.ticket_id,
            "created_at": decision_row["created_at"],
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": f"draft_{decision}",
            "inputs_used": f"working/{DRAFTS_TABLE}.csv",
            "decision_summary": (
                f"decision={decision}; edited={'true' if edited_text != original_text else 'false'}; "
                f"forced_approve={forced_approve}; reviewer_notes={args.reviewer_notes[:120]}"
            ),
            "confidence_score": "",
            "needs_human_review": "false",
            "notes": "retry limit reached; forced approve" if forced_approve else "",
        },
    )

    env = make_envelope(
        status=STATUS_OK,
        skill_name=SKILL_NAME,
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        ticket_id=args.ticket_id,
        next_action=next_action,
        review_required=False,
        outputs={
            "decision": decision,
            "forced_approve": forced_approve,
            "edited": edited_text != original_text,
            "retry_count": prior_rejects,
        },
        artifact_refs=[f"working/{REVIEWS_TABLE}.csv"],
    )
    emit_envelope(
        env,
        as_json=args.as_json,
        text_summary=(
            f"Specialist draft for {args.ticket_id}: decision={decision}"
            f"{' (forced after retry limit)' if forced_approve else ''}; "
            f"next={next_action}."
        ),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
