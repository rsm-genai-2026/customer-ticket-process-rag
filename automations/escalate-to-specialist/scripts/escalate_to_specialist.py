"""Step 6: route a ticket to an IT specialist with a clean handoff.

The escalation reason comes from the upstream working tables:

* ``faq_decisions.csv`` — match=false → "no FAQ match found".
* ``faq_decisions.csv`` — match=true, recommended=escalate → "FAQ matched
  but required customer info missing".
* ``feedback_decisions.csv`` — reopened_flag=true → "re-escalation after
  customer rejection".

Specialist selection prefers (in order): the recommended specialist
group, then specialists whose ``systems_supported`` includes the ticket's
affected system, then any specialist. Within candidates, higher seniority
wins. Ties break on ``specialist_id`` for determinism.

Run from the repo root::

    uv run python automations/escalate-to-specialist/scripts/escalate_to_specialist.py \\
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
    STATUS_SKIPPED,
    append_action_log,
    append_csv_row,
    default_step_id,
    default_workflow_run_id,
    emit_envelope,
    emit_error,
    find_step_row,
    latest_working_row,
    make_envelope,
    make_skill_parser,
    needs_human_review,
    now_iso,
    pipe_join,
    read_csv,
    replace_step_row,
    require_ticket,
)

SKILL_NAME = "escalate-to-specialist"
ESCALATIONS_TABLE = "escalation_decisions"
NEXT_ACTION = "investigate-specialist-solution"

SENIORITY_RANK = {"junior": 0, "mid": 1, "senior": 2, "principal": 3}

CATEGORY_QUESTIONS: dict[str, str] = {
    "login_access": (
        "Can you confirm whether the user's session/MFA enrollment is current and "
        "whether SSO group membership has propagated to the affected system?"
    ),
    "password_reset": (
        "Can you reset the privileged account using the elevated workflow and confirm the user can sign back in?"
    ),
    "billing_account": (
        "Can you verify the tax engine cache and the account's billing region, then "
        "advise whether to reissue the affected invoice?"
    ),
    "software_bug": (
        "Can you reproduce in staging and identify any recent changes that affect "
        "this code path? An engineering follow-up ticket may be needed."
    ),
    "hardware_issue": (
        "Can you advise on driver/firmware updates for this device class and confirm whether a swap is appropriate?"
    ),
    "network_connectivity": (
        "Can you check the VPN gateway, DNS resolver, and routing tables for this "
        "user's region, and apply any corrective config?"
    ),
    "email_calendar": (
        "Can you reauthorize OAuth tokens for this user's mail/calendar provider and "
        "verify shared mailbox / calendar permissions?"
    ),
    "data_reporting": (
        "Can you confirm warehouse jobs ran on schedule and check for recent schema changes that affect this report?"
    ),
    "security_request": (
        "Can you review audit logs for recent authentication events on this account "
        "and apply the access change with appropriate approvals?"
    ),
    "other": "Can you advise on next steps for this request?",
}


def _triage_for(
    data_dir: Path,
    out_dir: Path,
    ticket_id: str,
    *,
    mode: str = MODE_DEMO,
    workflow_run_id: str | None = None,
) -> dict:
    """Return the latest triage row for ``ticket_id``.

    ``mode="live"`` (production-safe): only consult ``data/working/``.
    Raises ``LookupError`` if no working row exists.

    ``mode="demo"``: fall back to ``data/processed/ticket_triage.csv``
    for tutorial use against the seeded dataset.
    """

    triage = latest_working_row(out_dir, "triage_decisions", ticket_id, workflow_run_id=workflow_run_id)
    if triage is not None:
        return triage
    if mode != MODE_DEMO:
        raise LookupError(
            f"no triage decision available for ticket {ticket_id} in working/. "
            f"Run classify-prioritize-ticket first, or pass --mode demo."
        )
    historical = read_csv(data_dir, "processed/ticket_triage.csv")
    rows = historical.filter(pl.col("ticket_id") == ticket_id).to_dicts()
    if not rows:
        raise LookupError(f"no triage decision available for ticket {ticket_id}. Run classify-prioritize-ticket first.")
    return rows[0]


def _determine_escalation_reason(faq_decision: dict | None, feedback_decision: dict | None) -> str:
    """Pick the escalation reason from upstream working rows.

    Priority order: a freshly reopened ticket trumps an older FAQ
    decision (so re-escalations are recognised correctly even when the
    original FAQ row is still on disk).
    """

    if feedback_decision is not None and str(feedback_decision.get("reopened_flag", "")).lower() == "true":
        return "re-escalation after customer rejection"
    if faq_decision is not None:
        match = str(faq_decision.get("faq_match_found", "")).lower() == "true"
        rec = faq_decision.get("recommended_next_step", "")
        if not match:
            return "no FAQ match found"
        if rec == "escalate-to-specialist":
            return "FAQ matched but required customer information not available"
    return ""


def load_escalation_context(
    data_dir: Path,
    out_dir: Path,
    ticket_id: str,
    *,
    mode: str = MODE_DEMO,
    workflow_run_id: str | None = None,
) -> dict:
    """Return ticket, specialists, triage, and the escalation trigger.

    Raises ``LookupError`` if no upstream signal authorizes an escalation.
    ``mode`` controls whether the synthetic ``processed/`` tables are
    accepted as a triage fallback (see ``_triage_for``).
    """

    ticket = require_ticket(data_dir, ticket_id)
    specialists = read_csv(data_dir, "raw/it_specialists.csv")
    triage = _triage_for(data_dir, out_dir, ticket_id, mode=mode, workflow_run_id=workflow_run_id)
    faq_decision = latest_working_row(out_dir, "faq_decisions", ticket_id, workflow_run_id=workflow_run_id)
    feedback_decision = latest_working_row(out_dir, "feedback_decisions", ticket_id, workflow_run_id=workflow_run_id)

    reason = _determine_escalation_reason(faq_decision, feedback_decision)
    if not reason:
        raise LookupError(
            f"no upstream signal authorizes an escalation for {ticket_id}. "
            f"Run check-faq-resolution first, or verify-feedback-close-or-reopen "
            f"if the customer rejected a prior resolution."
        )

    return {
        "ticket": ticket,
        "specialists": specialists,
        "triage": triage,
        "faq_decision": faq_decision,
        "feedback_decision": feedback_decision,
        "escalation_reason": reason,
    }


def select_specialist(context: dict, specialists: pl.DataFrame) -> dict:
    """Return the chosen specialist row.

    Prefers a same-group + same-system match, then same-group only,
    then a same-system match across any group, then any specialist.
    Within each tier, higher seniority wins; ties break on
    ``specialist_id`` for determinism.
    """

    triage = context["triage"]
    affected = context["ticket"].get("affected_system") or ""
    group = triage.get("recommended_specialist_group") or ""

    rows = specialists.to_dicts()
    if not rows:
        raise LookupError("no specialists available in raw/it_specialists.csv")

    def supports_system(s: dict) -> bool:
        return affected and affected in (s.get("systems_supported") or "").split("|")

    def in_group(s: dict) -> bool:
        return s.get("specialist_group") == group

    tiers = [
        [s for s in rows if in_group(s) and supports_system(s)],
        [s for s in rows if in_group(s)],
        [s for s in rows if supports_system(s)],
        rows,
    ]

    for tier in tiers:
        if tier:
            tier.sort(
                key=lambda s: (
                    -SENIORITY_RANK.get(s.get("seniority", ""), 0),
                    s.get("specialist_id", ""),
                )
            )
            return tier[0]
    raise LookupError("no specialist could be selected")  # pragma: no cover


def build_handoff(context: dict, specialist: dict) -> dict:
    """Compose the handoff summary, evidence, specific question, and flags."""

    ticket = context["ticket"]
    triage = context["triage"]
    feedback = context["feedback_decision"]

    impact = (ticket.get("business_impact_text") or "(not provided)").strip()
    symptom = (ticket.get("error_or_symptom_detail") or ticket.get("description") or "").strip()
    steps_tried = (ticket.get("steps_already_tried") or "(none reported)").strip()

    handoff = (
        f"Ticket {ticket['ticket_id']} on {ticket.get('affected_system', '?')}: "
        f"{ticket.get('subject', '?')}. "
        f"Impact: {impact}. "
        f"Symptom: {symptom}. "
        f"Customer already tried: {steps_tried}."
    )
    if feedback is not None and str(feedback.get("reopened_flag", "")).lower() == "true":
        handoff += (
            f" REOPEN: customer rejected the prior resolution — "
            f"reason: {feedback.get('rejection_reason') or '(unspecified)'}."
        )

    evidence_bits = [
        f"subject={ticket.get('subject', '')!r}",
        f"description_first_140={(ticket.get('description', '') or '')[:140]!r}",
        f"steps_tried={steps_tried!r}",
        f"reported_urgency={ticket.get('customer_reported_urgency', '')}",
    ]
    if str(ticket.get("attachment_flag", "")).lower() == "true":
        evidence_bits.append(f"attachment={ticket.get('attachment_description', '')!r}")
    customer_evidence = pipe_join(evidence_bits)

    category = triage.get("assigned_category", "other")
    specific_question = CATEGORY_QUESTIONS.get(category, CATEGORY_QUESTIONS["other"])

    missing_information_flag = not bool(ticket.get("error_or_symptom_detail") and ticket.get("steps_already_tried"))

    return {
        "handoff_summary": handoff,
        "customer_evidence_included": customer_evidence,
        "specific_question_for_specialist": specific_question,
        "missing_information_flag": missing_information_flag,
    }


def build_escalation_row(context: dict) -> dict:
    specialist = select_specialist(context, context["specialists"])
    handoff = build_handoff(context, specialist)
    triage = context["triage"]
    return {
        "ticket_id": context["ticket"]["ticket_id"],
        "created_at": now_iso(),
        "skill_name": SKILL_NAME,
        "specialist_id": specialist["specialist_id"],
        "specialist_name": specialist.get("name", ""),
        "specialist_group": specialist.get("specialist_group", ""),
        "specialist_seniority": specialist.get("seniority", ""),
        "specialist_supports_affected_system": context["ticket"].get("affected_system", "")
        in (specialist.get("systems_supported") or "").split("|"),
        "requested_specialist_group": triage.get("recommended_specialist_group", ""),
        "escalation_reason": context["escalation_reason"],
        "handoff_summary": handoff["handoff_summary"],
        "specific_question_for_specialist": handoff["specific_question_for_specialist"],
        "customer_evidence_included": handoff["customer_evidence_included"],
        "missing_information_flag": handoff["missing_information_flag"],
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "raw/it_specialists.csv",
                "working/triage_decisions.csv",
                "working/faq_decisions.csv",
                "working/feedback_decisions.csv",
            ]
        ),
        "decision_summary": (
            f"escalated to {specialist['specialist_id']} "
            f"({specialist.get('specialist_group', '')}); "
            f"reason={context['escalation_reason']}"
        ),
    }


def write_escalation_decision(out_dir: Path, decision: dict) -> None:
    append_csv_row(Path(out_dir) / f"{ESCALATIONS_TABLE}.csv", decision)


def main(argv: list[str] | None = None) -> int:
    parser = make_skill_parser(__doc__.splitlines()[0] if __doc__ else "")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    workflow_run_id = args.workflow_run_id or default_workflow_run_id()
    read_workflow_run_id = workflow_run_id if args.workflow_run_id else None
    step_id = args.step_id or default_step_id(SKILL_NAME)

    existing = find_step_row(out_dir, ESCALATIONS_TABLE, workflow_run_id, step_id)
    if existing and args.idempotency_mode == "skip":
        env = make_envelope(
            status=STATUS_SKIPPED,
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action=NEXT_ACTION,
            outputs={"existing_row": existing},
            artifact_refs=[f"working/{ESCALATIONS_TABLE}.csv"],
        )
        emit_envelope(
            env,
            as_json=args.as_json,
            text_summary=(
                f"escalation already recorded for workflow_run_id={workflow_run_id} step_id={step_id} — skipping."
            ),
        )
        return 0

    err_kwargs = {
        "skill_name": SKILL_NAME,
        "workflow_run_id": workflow_run_id,
        "step_id": step_id,
        "ticket_id": args.ticket_id,
        "as_json": args.as_json,
    }
    try:
        context = load_escalation_context(
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
    except LookupError as exc:
        return emit_error(
            **err_kwargs,
            error_code="missing_upstream",
            message=str(exc),
            exit_code=3,
            next_action="check-faq-resolution",
        )

    row = build_escalation_row(context)
    row["workflow_run_id"] = workflow_run_id
    row["step_id"] = step_id
    if args.idempotency_mode == "replace":
        replace_step_row(
            Path(out_dir) / f"{ESCALATIONS_TABLE}.csv",
            row,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
    else:
        write_escalation_decision(out_dir, row)

    review_required = needs_human_review(None, extra=bool(row["missing_information_flag"]))

    text_summary = (
        f"Escalation for {row['ticket_id']}:\n"
        f"  reason            : {row['escalation_reason']}\n"
        f"  requested group   : {row['requested_specialist_group']}\n"
        f"  specialist        : {row['specialist_id']} "
        f"({row['specialist_name']}, "
        f"group={row['specialist_group']}, "
        f"seniority={row['specialist_seniority']})\n"
        f"  supports system?  : {row['specialist_supports_affected_system']}\n"
        f"  handoff           : {row['handoff_summary']}\n"
        f"  specific question : {row['specific_question_for_specialist']}\n"
        f"  missing info?     : {row['missing_information_flag']}\n"
        f"  review required?  : {review_required}\n"
        f"\nNext valid action: {NEXT_ACTION}."
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": row["ticket_id"],
            "created_at": row["created_at"],
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": "ticket_escalated",
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": "",
            "needs_human_review": "true" if review_required else "false",
            "notes": ("missing_information=true" if row["missing_information_flag"] else f"mode={args.mode}"),
        },
    )

    env = make_envelope(
        status=STATUS_OK,
        skill_name=SKILL_NAME,
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        ticket_id=row["ticket_id"],
        next_action=NEXT_ACTION,
        confidence=None,
        review_required=review_required,
        artifact_refs=[f"working/{ESCALATIONS_TABLE}.csv"],
        outputs={
            "specialist_id": row["specialist_id"],
            "specialist_group": row["specialist_group"],
            "specialist_seniority": row["specialist_seniority"],
            "specialist_supports_affected_system": row["specialist_supports_affected_system"],
            "escalation_reason": row["escalation_reason"],
            "handoff_summary": row["handoff_summary"],
            "specific_question_for_specialist": row["specific_question_for_specialist"],
            "missing_information_flag": row["missing_information_flag"],
        },
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
