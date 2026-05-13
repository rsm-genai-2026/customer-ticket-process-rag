"""Step 0/N: tell the user what happened on a ticket and what to do next.

Reads working tables first (live skill output). In live mode, it does
not inspect synthetic historical ``processed/`` tables. In demo mode,
it also includes historical rows for tutorial narration. Builds a
concise timeline and a state machine that maps to exactly one
recommended next skill.

Run from the repo root::

    uv run python automations/audit-ticket-process/scripts/audit_ticket_process.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.ticketing_common import (  # noqa: E402
    MODE_DEMO,
    STATUS_OK,
    append_action_log,
    default_step_id,
    default_workflow_run_id,
    emit_envelope,
    emit_error,
    make_envelope,
    make_skill_parser,
    now_iso,
    pipe_join,
    read_csv,
    require_ticket,
)

SKILL_NAME = "audit-ticket-process"


def _filter_rows(df: pl.DataFrame, ticket_id: str, *, workflow_run_id: str | None = None) -> list[dict]:
    if "ticket_id" not in df.columns:
        return []
    filtered = df.filter(pl.col("ticket_id") == ticket_id)
    if workflow_run_id:
        if "workflow_run_id" not in filtered.columns:
            return []
        filtered = filtered.filter(pl.col("workflow_run_id") == workflow_run_id)
    return filtered.to_dicts()


def _read_optional(path: Path) -> pl.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pl.read_csv(path)
    except Exception:  # pragma: no cover - corrupt file
        return None


def load_ticket_history(
    data_dir: Path,
    out_dir: Path,
    ticket_id: str,
    *,
    mode: str = MODE_DEMO,
    workflow_run_id: str | None = None,
) -> dict:
    """Return all rows mentioning ``ticket_id`` across the ticket data.

    Working tables in ``data/working/`` are always loaded if present.

    * ``mode="live"`` — the production-safe choice. The synthetic
      ``data/processed/`` tables are NOT included in the timeline. The
      audit reflects only the live workflow state.
    * ``mode="demo"`` — also include rows from ``data/processed/``
      tables so the audit can narrate a closed historical ticket end-to-end
      against the seeded dataset. The historical rows in the timeline
      are clearly tagged ``(historical)``.
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
        "sent_messages": _read_optional(out_dir / "sent_messages.csv"),
    }
    working_rows = {
        name: ([] if df is None else _filter_rows(df, ticket_id, workflow_run_id=workflow_run_id))
        for name, df in working.items()
    }

    if mode == MODE_DEMO:
        historical = {
            "ticket_triage": _filter_rows(read_csv(data_dir, "processed/ticket_triage.csv"), ticket_id),
            "faq_checks": _filter_rows(read_csv(data_dir, "processed/faq_checks.csv"), ticket_id),
            "specialist_escalations": _filter_rows(
                read_csv(data_dir, "processed/specialist_escalations.csv"), ticket_id
            ),
            "specialist_investigations": _filter_rows(
                read_csv(data_dir, "processed/specialist_investigations.csv"), ticket_id
            ),
            "customer_messages": _filter_rows(read_csv(data_dir, "processed/customer_messages.csv"), ticket_id),
            "resolution_feedback": _filter_rows(read_csv(data_dir, "processed/resolution_feedback.csv"), ticket_id),
        }
    else:
        # Live mode: do not look at the synthetic historical tables at all.
        historical = {
            "ticket_triage": [],
            "faq_checks": [],
            "specialist_escalations": [],
            "specialist_investigations": [],
            "customer_messages": [],
            "resolution_feedback": [],
        }

    return {
        "ticket": ticket,
        "customer": customer,
        "working": working_rows,
        "historical": historical,
        "mode": mode,
        "workflow_run_id": workflow_run_id or "",
    }


def _has_step(history: dict, working_table: str, historical_table: str) -> tuple[bool, str]:
    """Return (whether the step happened, source of evidence)."""

    if history["working"][working_table]:
        return True, "working"
    if history["historical"][historical_table]:
        return True, "historical"
    return False, ""


_STEP_NAME_TO_STATE = {
    "triaged": "triaged_awaiting_faq_check",
    "faq_checked": "faq_checked_awaiting_decision",
    "escalated": "escalated_awaiting_specialist",
    "specialist_done": "specialist_solution_ready_for_relay",
    "response_drafted": "response_drafted_awaiting_send",
    "response_sent": "response_sent_awaiting_customer",
    "feedback": "feedback_recorded_action_pending",
}

# Logical step order. Used to break ties when two events share a timestamp
# (the action log uses second precision, so back-to-back skills can collide).
# Later entries win; e.g. ``faq_checked`` beats ``triaged`` on a tie.
_STEP_ORDER: list[str] = [
    "triaged",
    "faq_checked",
    "escalated",
    "specialist_done",
    "response_drafted",
    "response_sent",
    "feedback",
]


def _step_rank(step: str) -> int:
    try:
        return _STEP_ORDER.index(step)
    except ValueError:  # pragma: no cover - defensive
        return -1


def _collect_step_events(history: dict) -> list[tuple[str, str, dict]]:
    """Return ``[(timestamp, step_name, row)]`` across working + historical.

    Working rows win on ties because their timestamp comparison sorts
    string-equal but lists are processed in working-then-historical
    order; the orchestrator and the operator both expect live data to
    take precedence over the synthetic seed.
    """

    events: list[tuple[str, str, dict]] = []
    w = history["working"]
    h = history["historical"]

    def add(rows: list[dict], step: str, time_col: str) -> None:
        for r in rows or []:
            ts = r.get(time_col, "") or ""
            if ts:
                events.append((ts, step, r))

    add(w["triage_decisions"], "triaged", "created_at")
    add(w["faq_decisions"], "faq_checked", "created_at")
    add(w["escalation_decisions"], "escalated", "created_at")
    add(w["specialist_solutions"], "specialist_done", "created_at")
    add(w["customer_response_drafts"], "response_drafted", "created_at")
    add(w.get("sent_messages") or [], "response_sent", "sent_at")
    add(w["feedback_decisions"], "feedback", "created_at")

    add(h["ticket_triage"], "triaged", "triaged_at")
    add(h["faq_checks"], "faq_checked", "faq_checked_at")
    add(h["specialist_escalations"], "escalated", "escalated_at")
    add(h["specialist_investigations"], "specialist_done", "solution_created_at")
    # In demo mode a historical customer_messages row implies the message
    # was actually sent — the historical data did not have a separate egress
    # step in the seeded dataset.
    add(h["customer_messages"], "response_sent", "sent_at")
    add(h["resolution_feedback"], "feedback", "customer_reply_at")

    return events


def infer_current_state(history: dict) -> dict:
    """Return ``{"state": ..., "flags": {...}}`` based on the most recent step.

    State is determined by the **timestamp-latest** workflow event that
    has been recorded for the ticket (working data preferred). This is
    the only way the audit can advance correctly through a reopen
    cycle: a stale ``feedback_recorded`` row no longer pins the state
    once a fresh re-escalation, investigation, or send has happened.
    """

    events = _collect_step_events(history)

    triaged = any(e[1] == "triaged" for e in events)
    faq_checked = any(e[1] == "faq_checked" for e in events)
    escalated = any(e[1] == "escalated" for e in events)
    specialist_done = any(e[1] == "specialist_done" for e in events)
    response_drafted = any(e[1] == "response_drafted" for e in events)
    response_sent_to_customer = any(e[1] == "response_sent" for e in events)
    feedback = any(e[1] == "feedback" for e in events)

    # faq_match_found from the most-recent FAQ decision (working > historical)
    faq_match_found = None
    if history["working"]["faq_decisions"]:
        last = sorted(history["working"]["faq_decisions"], key=lambda r: r.get("created_at", ""))[-1]
        faq_match_found = str(last.get("faq_match_found", "")).lower() == "true"
    elif history["historical"]["faq_checks"]:
        last = history["historical"]["faq_checks"][0]
        faq_match_found = str(last.get("faq_match_found", "")).lower() == "true"

    # Closure status: only the LATEST feedback row counts. Any older
    # reopen row is by definition no longer the current decision.
    closed = False
    closure_reason = ""
    latest_feedback_next_action = ""
    if history["working"]["feedback_decisions"]:
        last = sorted(history["working"]["feedback_decisions"], key=lambda r: r.get("created_at", ""))[-1]
        next_action = last.get("next_action", "")
        latest_feedback_next_action = next_action
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
    elif not events:
        state = "submitted_awaiting_triage"
    else:
        # Break ties on equal timestamps with logical step order so a
        # workflow that ran back-to-back inside one wall-clock second
        # still reports the latest step. Timestamps in this project use
        # second precision (see ``now_iso``).
        latest_step = max(events, key=lambda e: (e[0], _step_rank(e[1])))[1]
        # An old reopened feedback should not block forward progress: if
        # the LATEST feedback row exists and is the timestamp-latest
        # event, but it says "reopen", we still report
        # ``feedback_recorded_action_pending`` so the orchestrator
        # routes to escalate. Once a new escalation/specialist/send
        # event arrives with a later timestamp, that step wins.
        state = _STEP_NAME_TO_STATE.get(latest_step, "submitted_awaiting_triage")

    return {
        "state": state,
        "flags": {
            "triaged": triaged,
            "faq_checked": faq_checked,
            "faq_match_found": faq_match_found,
            "escalated": escalated,
            "specialist_done": specialist_done,
            "response_drafted": response_drafted,
            "response_sent_to_customer": response_sent_to_customer,
            "feedback_recorded": feedback,
            "latest_feedback_next_action": latest_feedback_next_action,
            "closed": closed,
            "closure_reason": closure_reason,
            "mode": history["mode"],
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
    if s == "response_drafted_awaiting_send":
        return ["send-customer-response"]
    if s == "response_sent_awaiting_customer":
        return ["verify-feedback-close-or-reopen (when the customer replies)"]
    if s == "feedback_recorded_action_pending":
        # Latest feedback decision dictates: reopen → escalate; ambiguous → wait for clarification.
        if flags.get("latest_feedback_next_action") == "request_clarification":
            return ["request_clarification"]
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
    add_rows(w.get("sent_messages") or [], "sent_messages", "sent_at", "send-customer-response")
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
            f"  response_drafted    : {state['flags']['response_drafted']}",
            f"  response_sent       : {state['flags']['response_sent_to_customer']}",
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
    parser = make_skill_parser(__doc__.splitlines()[0] if __doc__ else "")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
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
    try:
        history = load_ticket_history(
            data_dir,
            out_dir,
            args.ticket_id,
            mode=args.mode,
            workflow_run_id=read_workflow_run_id,
        )
    except KeyError as exc:
        return emit_error(**err_kwargs, error_code="ticket_not_found", message=str(exc))
    except FileNotFoundError as exc:
        return emit_error(**err_kwargs, error_code="missing_data", message=str(exc))

    state = infer_current_state(history)
    report = build_audit_report(history, state)
    next_actions = list_valid_next_actions(state)
    next_action_str = next_actions[0] if next_actions else ""
    # Strip the "(when the customer replies)" suffix for orchestrator routing.
    if " (" in next_action_str:
        next_action_str = next_action_str.split(" (", 1)[0]

    append_action_log(
        out_dir,
        {
            "ticket_id": args.ticket_id,
            "created_at": now_iso(),
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": f"audit:{state['state']}",
            "inputs_used": pipe_join(
                [
                    "raw/submitted_tickets.csv",
                    "raw/customers.csv",
                    "all working tables",
                    ("all processed tables" if args.mode == MODE_DEMO else "(processed tables omitted: live mode)"),
                ]
            ),
            "decision_summary": (f"state={state['state']}; next={'|'.join(next_actions) or 'none'}; mode={args.mode}"),
            "confidence_score": "",
            "needs_human_review": "false",
            "notes": "",
        },
    )

    env = make_envelope(
        status=STATUS_OK,
        skill_name=SKILL_NAME,
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        ticket_id=args.ticket_id,
        next_action=next_action_str,
        confidence=None,
        review_required=False,
        artifact_refs=["working/ticket_action_log.csv"],
        outputs={
            "state": state["state"],
            "flags": state["flags"],
            "valid_next_actions": next_actions,
            "report": report,
            "mode": args.mode,
        },
    )
    emit_envelope(env, as_json=args.as_json, text_summary=report)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
