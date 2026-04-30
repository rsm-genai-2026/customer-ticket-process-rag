"""Step 0/N: tell the user what happened on a ticket and what to do next.

Reads working tables first (live skill output) and falls back to the
synthetic historical ``processed/`` tables for tickets that have only
the simulated data. Builds a concise timeline and a state machine that
maps to exactly one recommended next skill.

Run from the repo root::

    uv run python skills/audit-ticket-process/scripts/audit_ticket_process.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills.ticketing_common.ticketing_common import (  # noqa: E402
    append_action_log,
    now_iso,
    pipe_join,
    read_csv,
    require_ticket,
)

SKILL_NAME = "audit-ticket-process"


def _filter_rows(df: pl.DataFrame, ticket_id: str) -> list[dict]:
    if "ticket_id" not in df.columns:
        return []
    return df.filter(pl.col("ticket_id") == ticket_id).to_dicts()


def _read_optional(path: Path) -> pl.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pl.read_csv(path)
    except Exception:  # pragma: no cover - corrupt file
        return None


def load_ticket_history(data_dir: Path, out_dir: Path, ticket_id: str) -> dict:
    """Return all rows mentioning ``ticket_id`` across raw/processed/working.

    Each working table is loaded if present. Processed tables are loaded
    via ``read_csv`` so a missing-data-file error surfaces clearly. The
    ``customer`` field is filled in opportunistically.
    """

    ticket = require_ticket(data_dir, ticket_id)
    customers = read_csv(data_dir, "raw/customers.csv")
    customer = customers.filter(pl.col("customer_id") == ticket["customer_id"]).to_dicts()
    customer = customer[0] if customer else {}

    working = {
        "triage_decisions": _read_optional(out_dir / "triage_decisions.csv"),
        "faq_decisions": _read_optional(out_dir / "faq_decisions.csv"),
        "escalation_decisions": _read_optional(out_dir / "escalation_decisions.csv"),
        "specialist_solutions": _read_optional(out_dir / "specialist_solutions.csv"),
        "customer_response_drafts": _read_optional(out_dir / "customer_response_drafts.csv"),
        "feedback_decisions": _read_optional(out_dir / "feedback_decisions.csv"),
    }
    working_rows = {name: ([] if df is None else _filter_rows(df, ticket_id)) for name, df in working.items()}
    historical = {
        "ticket_triage": _filter_rows(read_csv(data_dir, "processed/ticket_triage.csv"), ticket_id),
        "faq_checks": _filter_rows(read_csv(data_dir, "processed/faq_checks.csv"), ticket_id),
        "specialist_escalations": _filter_rows(read_csv(data_dir, "processed/specialist_escalations.csv"), ticket_id),
        "specialist_investigations": _filter_rows(
            read_csv(data_dir, "processed/specialist_investigations.csv"), ticket_id
        ),
        "customer_messages": _filter_rows(read_csv(data_dir, "processed/customer_messages.csv"), ticket_id),
        "resolution_feedback": _filter_rows(read_csv(data_dir, "processed/resolution_feedback.csv"), ticket_id),
    }
    return {
        "ticket": ticket,
        "customer": customer,
        "working": working_rows,
        "historical": historical,
    }


def _has_step(history: dict, working_table: str, historical_table: str) -> tuple[bool, str]:
    """Return (whether the step happened, source of evidence)."""

    if history["working"][working_table]:
        return True, "working"
    if history["historical"][historical_table]:
        return True, "historical"
    return False, ""


def infer_current_state(history: dict) -> dict:
    """Walk the workflow and return ``{"state": ..., "flags": {...}}``."""

    triaged, _ = _has_step(history, "triage_decisions", "ticket_triage")
    faq_checked, _ = _has_step(history, "faq_decisions", "faq_checks")
    escalated, _ = _has_step(history, "escalation_decisions", "specialist_escalations")
    specialist_done, _ = _has_step(history, "specialist_solutions", "specialist_investigations")
    response_sent, _ = _has_step(history, "customer_response_drafts", "customer_messages")
    feedback, _ = _has_step(history, "feedback_decisions", "resolution_feedback")

    # Most recent FAQ decision (working preferred)
    faq_match_found = None
    if history["working"]["faq_decisions"]:
        last = sorted(history["working"]["faq_decisions"], key=lambda r: r.get("created_at", ""))[-1]
        faq_match_found = str(last.get("faq_match_found", "")).lower() == "true"
    elif history["historical"]["faq_checks"]:
        last = history["historical"]["faq_checks"][0]
        faq_match_found = str(last.get("faq_match_found", "")).lower() == "true"

    # Closure status from feedback table
    closed = False
    closure_reason = ""
    if history["working"]["feedback_decisions"]:
        last = sorted(history["working"]["feedback_decisions"], key=lambda r: r.get("created_at", ""))[-1]
        next_action = last.get("next_action", "")
        if next_action in {"close_ticket", "close_unresolved_vendor_followup"}:
            closed = True
            closure_reason = next_action
    elif history["historical"]["resolution_feedback"]:
        for row in history["historical"]["resolution_feedback"]:
            if row.get("closed_at"):
                closed = True
                closure_reason = row.get("closure_reason") or "closed"

    if closed:
        state = "closed"
    elif feedback:
        # Feedback exists but ticket not closed yet — we must still pick reopen / clarification
        state = "feedback_recorded_action_pending"
    elif response_sent:
        state = "response_sent_awaiting_customer"
    elif specialist_done:
        state = "specialist_solution_ready_for_relay"
    elif escalated:
        state = "escalated_awaiting_specialist"
    elif faq_checked:
        state = "faq_checked_awaiting_decision"
    elif triaged:
        state = "triaged_awaiting_faq_check"
    else:
        state = "submitted_awaiting_triage"

    return {
        "state": state,
        "flags": {
            "triaged": triaged,
            "faq_checked": faq_checked,
            "faq_match_found": faq_match_found,
            "escalated": escalated,
            "specialist_done": specialist_done,
            "response_sent": response_sent,
            "feedback_recorded": feedback,
            "closed": closed,
            "closure_reason": closure_reason,
        },
    }


def list_valid_next_actions(state: dict) -> list[str]:
    """Translate the inferred state into a concrete recommended skill set."""

    flags = state["flags"]
    s = state["state"]

    if s == "closed":
        return []
    if s == "submitted_awaiting_triage":
        return ["classify-prioritize-ticket"]
    if s == "triaged_awaiting_faq_check":
        return ["check-faq-resolution"]
    if s == "faq_checked_awaiting_decision":
        if flags["faq_match_found"]:
            return ["draft-faq-response"]
        return ["escalate-to-specialist"]
    if s == "escalated_awaiting_specialist":
        return ["investigate-specialist-solution"]
    if s == "specialist_solution_ready_for_relay":
        return ["draft-specialist-response"]
    if s == "response_sent_awaiting_customer":
        return ["verify-feedback-close-or-reopen (when the customer replies)"]
    if s == "feedback_recorded_action_pending":
        # Latest feedback decision dictates: reopen → escalate; otherwise close
        return ["escalate-to-specialist"]
    return []  # pragma: no cover


def _timeline_events(history: dict) -> list[tuple[str, str, str]]:
    """Return a chronologically sorted list of ``(timestamp, source, label)``.

    The ticket-submitted event always comes first. After that we union
    working rows (live skill-driven) and historical rows (synthetic).
    """

    events: list[tuple[str, str, str]] = []
    ticket = history["ticket"]
    events.append((ticket["submitted_at"], "raw", f"ticket_submitted (channel={ticket.get('channel', '')})"))

    def add_rows(rows: list[dict], table: str, time_col: str, label: str) -> None:
        for row in rows:
            ts = row.get(time_col, "")
            extra = ""
            if table == "faq_decisions":
                extra = f" [match={row.get('faq_match_found', '')}, faq_id={row.get('faq_id', '') or '-'}]"
            elif table == "feedback_decisions":
                extra = f" [next_action={row.get('next_action', '')}]"
            events.append((ts, table, f"{label}{extra}"))

    w = history["working"]
    add_rows(w["triage_decisions"], "triage_decisions", "created_at", "classify-prioritize-ticket")
    add_rows(w["faq_decisions"], "faq_decisions", "created_at", "check-faq-resolution")
    add_rows(w["escalation_decisions"], "escalation_decisions", "created_at", "escalate-to-specialist")
    add_rows(w["specialist_solutions"], "specialist_solutions", "created_at", "investigate-specialist-solution")
    add_rows(w["customer_response_drafts"], "customer_response_drafts", "created_at", "draft-response")
    add_rows(w["feedback_decisions"], "feedback_decisions", "created_at", "verify-feedback-close-or-reopen")

    h = history["historical"]
    add_rows(h["ticket_triage"], "processed/ticket_triage", "triaged_at", "(historical) triaged")
    add_rows(h["faq_checks"], "processed/faq_checks", "faq_checked_at", "(historical) faq_checked")
    add_rows(h["specialist_escalations"], "processed/specialist_escalations", "escalated_at", "(historical) escalated")
    add_rows(
        h["specialist_investigations"],
        "processed/specialist_investigations",
        "solution_created_at",
        "(historical) specialist_solution_created",
    )
    add_rows(h["customer_messages"], "processed/customer_messages", "sent_at", "(historical) message_sent")
    add_rows(
        h["resolution_feedback"], "processed/resolution_feedback", "customer_reply_at", "(historical) customer_reply"
    )
    events.sort(key=lambda e: (e[0], e[1]))
    return events


def build_audit_report(history: dict, state: dict) -> str:
    """Compose the printed audit report."""

    ticket = history["ticket"]
    customer = history["customer"]
    next_actions = list_valid_next_actions(state)
    next_line = (
        "Next valid action: " + " or ".join(next_actions)
        if next_actions
        else "Next valid action: (none — ticket is closed)"
    )

    lines = [
        f"{ticket['ticket_id']} ({customer.get('customer_name', '?')}, "
        f"{customer.get('account_tier', '?')}): {state['state']}",
        "",
        "Timeline:",
    ]
    for ts, source, label in _timeline_events(history):
        lines.append(f"  {ts} {source:<32} {label}")
    lines.extend(
        [
            "",
            "Workflow flags:",
            f"  triaged             : {state['flags']['triaged']}",
            f"  faq_checked         : {state['flags']['faq_checked']}",
            f"  faq_match_found     : {state['flags']['faq_match_found']}",
            f"  escalated           : {state['flags']['escalated']}",
            f"  specialist_done     : {state['flags']['specialist_done']}",
            f"  response_sent       : {state['flags']['response_sent']}",
            f"  feedback_recorded   : {state['flags']['feedback_recorded']}",
            f"  closed              : {state['flags']['closed']}"
            + (
                f" ({state['flags']['closure_reason']})"
                if state["flags"]["closed"] and state["flags"]["closure_reason"]
                else ""
            ),
            "",
            next_line,
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="data/working")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()

    try:
        history = load_ticket_history(data_dir, out_dir, args.ticket_id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    state = infer_current_state(history)
    report = build_audit_report(history, state)
    print(report)

    append_action_log(
        out_dir,
        {
            "ticket_id": args.ticket_id,
            "created_at": now_iso(),
            "skill_name": SKILL_NAME,
            "action": f"audit:{state['state']}",
            "inputs_used": pipe_join(
                [
                    "raw/submitted_tickets.csv",
                    "raw/customers.csv",
                    "all working tables",
                    "all processed tables",
                ]
            ),
            "decision_summary": (f"state={state['state']}; next={'|'.join(list_valid_next_actions(state)) or 'none'}"),
            "confidence_score": "",
            "notes": "",
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
