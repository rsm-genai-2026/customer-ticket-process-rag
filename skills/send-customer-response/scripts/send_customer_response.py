"""Egress step: deliver the latest customer-response draft and record the send.

Reads the most recent ``data/working/customer_response_drafts.csv`` row
for the ticket, derives the recipient from the original ticket
(``submitted_by_email``), and appends one row to
``data/working/sent_messages.csv`` with a fresh ``delivery_id``.

Refuses to run if no draft exists. Idempotent on
``(workflow_run_id, step_id)`` so a retry will not double-send.

In a production deployment this is where you would call your real email
or ticketing API. The skill keeps that boundary deliberately thin: any
remote-side failure should be reported back through the standard error
envelope so the orchestrator can decide whether to retry.

Run from the repo root::

    uv run python skills/send-customer-response/scripts/send_customer_response.py \\
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
    now_iso,
    pipe_join,
    replace_step_row,
    require_ticket,
)

SKILL_NAME = "send-customer-response"
SENT_TABLE = "sent_messages"
NEXT_ACTION = "verify-feedback-close-or-reopen"

VALID_CHANNELS = {"email", "portal", "phone"}


def load_send_context(
    data_dir: Path,
    out_dir: Path,
    ticket_id: str,
    *,
    workflow_run_id: str | None = None,
) -> dict:
    """Return ticket + latest draft, or raise ``LookupError`` if no draft."""

    ticket = require_ticket(data_dir, ticket_id)
    draft = latest_working_row(out_dir, "customer_response_drafts", ticket_id, workflow_run_id=workflow_run_id)
    if draft is None:
        raise LookupError(
            f"no customer response draft found for ticket {ticket_id}. "
            f"Run draft-faq-response or draft-specialist-response first."
        )
    return {"ticket": ticket, "draft": draft}


def build_send_record(context: dict, *, channel: str, workflow_run_id: str, step_id: str) -> dict:
    """Build the row that will be written to ``sent_messages.csv``.

    The ``delivery_id`` is derived from the ticket id and the current
    timestamp so it is unique per send. The recipient comes from the
    original ticket — we never invent contact details.
    """

    ticket = context["ticket"]
    draft = context["draft"]
    sent_at = now_iso()
    safe_ts = re.sub(r"[^0-9]", "", sent_at)
    delivery_id = f"DEL-{ticket['ticket_id']}-{safe_ts}"
    recipient = ticket.get("submitted_by_email") or ""
    return {
        "delivery_id": delivery_id,
        "ticket_id": ticket["ticket_id"],
        "message_id": draft.get("message_id", ""),
        "draft_skill_name": draft.get("skill_name", ""),
        "message_source": draft.get("message_source", ""),
        "channel": channel,
        "recipient_email": recipient,
        "sent_at": sent_at,
        "delivery_status": "delivered",
        "skill_name": SKILL_NAME,
        "workflow_run_id": workflow_run_id,
        "step_id": step_id,
        "inputs_used": pipe_join(["raw/submitted_tickets.csv", "working/customer_response_drafts.csv"]),
        "decision_summary": (
            f"delivered draft message_id={draft.get('message_id', '')} "
            f"to {recipient or '(no recipient on file)'} via {channel}"
        ),
    }


def write_sent_message(out_dir: Path, record: dict) -> None:
    append_csv_row(Path(out_dir) / f"{SENT_TABLE}.csv", record)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="data/working")
    parser.add_argument("--channel", default="email", choices=sorted(VALID_CHANNELS))
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

    existing = find_step_row(out_dir, SENT_TABLE, workflow_run_id, step_id)
    if existing and args.idempotency_mode == "skip":
        env = make_envelope(
            status=STATUS_SKIPPED,
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action=NEXT_ACTION,
            outputs={"existing_row": existing},
            artifact_refs=[f"working/{SENT_TABLE}.csv"],
        )
        emit_envelope(
            env,
            as_json=args.as_json,
            text_summary=(f"send already recorded for workflow_run_id={workflow_run_id} step_id={step_id} — skipping."),
        )
        return 0

    try:
        context = load_send_context(data_dir, out_dir, args.ticket_id, workflow_run_id=read_workflow_run_id)
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
            next_action="draft-faq-response",
            error={"code": "missing_upstream", "message": str(exc)},
        )
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 3

    record = build_send_record(context, channel=args.channel, workflow_run_id=workflow_run_id, step_id=step_id)
    if args.idempotency_mode == "replace":
        replace_step_row(
            Path(out_dir) / f"{SENT_TABLE}.csv",
            record,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
    else:
        write_sent_message(out_dir, record)

    text_summary = (
        f"Sent response for {record['ticket_id']}:\n"
        f"  delivery_id        : {record['delivery_id']}\n"
        f"  message_id         : {record['message_id']}\n"
        f"  channel            : {record['channel']}\n"
        f"  recipient          : {record['recipient_email'] or '(none on file)'}\n"
        f"  sent_at            : {record['sent_at']}\n"
        f"\nNext valid action: {NEXT_ACTION}."
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": record["ticket_id"],
            "created_at": record["sent_at"],
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": "customer_response_sent",
            "inputs_used": record["inputs_used"],
            "decision_summary": record["decision_summary"],
            "confidence_score": "",
            "needs_human_review": "false",
            "notes": f"channel={args.channel}",
        },
    )

    env = make_envelope(
        status=STATUS_OK,
        skill_name=SKILL_NAME,
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        ticket_id=record["ticket_id"],
        next_action=NEXT_ACTION,
        confidence=None,
        review_required=False,
        artifact_refs=[f"working/{SENT_TABLE}.csv"],
        outputs={
            "delivery_id": record["delivery_id"],
            "message_id": record["message_id"],
            "channel": record["channel"],
            "recipient_email": record["recipient_email"],
            "sent_at": record["sent_at"],
            "delivery_status": record["delivery_status"],
        },
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
