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

SKILL_NAME = "draft-specialist-response"
DRAFTS_TABLE = "customer_response_drafts"
NEXT_ACTION = "send-customer-response"


def load_specialist_response_context(
    data_dir: Path,
    out_dir: Path,
    ticket_id: str,
    *,
    workflow_run_id: str | None = None,
) -> dict:
    """Return ticket, specialist solution, and the originating escalation.

    Raises ``LookupError`` if there is no specialist solution to draft from.
    """

    ticket = require_ticket(data_dir, ticket_id)
    solution = latest_working_row(out_dir, "specialist_solutions", ticket_id, workflow_run_id=workflow_run_id)
    if solution is None:
        raise LookupError(
            f"no specialist solution found for ticket {ticket_id}. Run the investigate-specialist-solution skill first."
        )
    escalation = latest_working_row(out_dir, "escalation_decisions", ticket_id, workflow_run_id=workflow_run_id)
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

    existing = find_step_row(out_dir, DRAFTS_TABLE, workflow_run_id, step_id)
    if existing and args.idempotency_mode == "skip":
        env = make_envelope(
            status=STATUS_SKIPPED,
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action=NEXT_ACTION,
            outputs={"existing_row": existing},
            artifact_refs=[f"working/{DRAFTS_TABLE}.csv"],
        )
        emit_envelope(
            env,
            as_json=args.as_json,
            text_summary=(
                f"specialist-response draft already recorded for "
                f"workflow_run_id={workflow_run_id} step_id={step_id} — skipping."
            ),
        )
        return 0

    try:
        context = load_specialist_response_context(
            data_dir,
            out_dir,
            args.ticket_id,
            workflow_run_id=read_workflow_run_id,
        )
    except KeyError as exc:
        env = make_envelope(
            status="error",
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            error={"code": "ticket_not_found", "message": str(exc)},
        )
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        env = make_envelope(
            status="error",
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            error={"code": "missing_data", "message": str(exc)},
        )
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except LookupError as exc:
        env = make_envelope(
            status="error",
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action="investigate-specialist-solution",
            error={"code": "missing_upstream", "message": str(exc)},
        )
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 3

    row = build_response_row(context)
    row["workflow_run_id"] = workflow_run_id
    row["step_id"] = step_id
    if args.idempotency_mode == "replace":
        replace_step_row(
            Path(out_dir) / f"{DRAFTS_TABLE}.csv",
            row,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
    else:
        write_customer_response(out_dir, row)

    quality_ok = not row["quality_check_notes"]
    is_post_rejection = bool(
        context["escalation"] and "re-escalation" in (context["escalation"].get("escalation_reason") or "").lower()
    )
    review_required = needs_human_review(row["confidence_score"], extra=not quality_ok)

    text_summary = (
        f"Specialist response drafted for {row['ticket_id']} "
        f"(message_id={row['message_id']}):\n"
        f"  post-rejection?    : {is_post_rejection}\n"
        f"  quality check ok?  : {quality_ok}\n"
        f"  review required?   : {review_required}\n"
        f"  follow-up request  : {row['follow_up_request']}\n"
        f"\n--- draft ---\n{row['sent_text']}\n--- end draft ---\n"
        f"\nNext valid action: {NEXT_ACTION}, then verify-feedback-close-or-reopen "
        f"with the customer's reply."
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": row["ticket_id"],
            "created_at": row["created_at"],
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": "specialist_response_drafted",
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": row["confidence_score"],
            "needs_human_review": "true" if review_required else "false",
            "notes": row["quality_check_notes"] or f"mode={args.mode}",
        },
    )

    env = make_envelope(
        status=STATUS_OK,
        skill_name=SKILL_NAME,
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        ticket_id=row["ticket_id"],
        next_action=NEXT_ACTION,
        confidence=row["confidence_score"],
        review_required=review_required,
        artifact_refs=[f"working/{DRAFTS_TABLE}.csv"],
        outputs={
            "message_id": row["message_id"],
            "message_source": row["message_source"],
            "sent_text": row["sent_text"],
            "customer_action_required": row["customer_action_required"],
            "follow_up_request": row["follow_up_request"],
            "quality_check_notes": row["quality_check_notes"],
            "is_post_rejection": is_post_rejection,
        },
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
