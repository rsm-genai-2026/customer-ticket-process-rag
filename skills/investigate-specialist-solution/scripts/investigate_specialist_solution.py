"""Step 7: ask an LLM to act as the IT specialist and propose a solution.

This is one of the **two** real skills in the repo (along with
``check-faq-resolution``). It runs only when the FAQ branch could not
resolve the ticket — the workflow needs genuine specialist judgement to
diagnose the issue and produce a customer-safe action.

The LLM receives:

* the original ticket (subject, description, affected system, symptom,
  steps already tried, business impact, urgency),
* the upstream triage (category, priority, recommended specialist group),
* the upstream escalation (specialist id, group, seniority, escalation
  reason, missing-information flag),
* and a request to return one structured JSON object.

The model must produce a root cause, diagnostic steps, evidence reviewed,
a plain-language ``solution_summary``, a ``customer_action_required``
string, a confidence score, and a ``requires_follow_up_flag`` (set when
engineering follow-up is needed and the immediate mitigation is partial).

Run from the repo root::

    uv run python skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import polars as pl
from dotenv import load_dotenv
from openai import OpenAIError

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.connect import DEFAULT_MODEL as DEFAULT_LLM_MODEL  # noqa: E402
from utils.connect import ask_json  # noqa: E402
from utils.ticketing_common import (  # noqa: E402
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

SKILL_NAME = "investigate-specialist-solution"
SOLUTIONS_TABLE = "specialist_solutions"
NEXT_ACTION = "draft-specialist-response"
DEFAULT_MODEL = os.environ.get("SPECIALIST_INVESTIGATION_MODEL", DEFAULT_LLM_MODEL)

# Confidence floor below which we always queue for human review even if
# the LLM reports higher. Used as the cap during normalisation when the
# upstream escalation flagged missing information.
_MISSING_INFO_CONFIDENCE_CAP = 0.60


def load_investigation_context(
    data_dir: Path,
    out_dir: Path,
    ticket_id: str,
    *,
    workflow_run_id: str | None = None,
) -> dict:
    """Return ticket, escalation, specialist, triage, and dictionaries.

    Raises ``LookupError`` if no escalation exists for this ticket or the
    named specialist is not in ``it_specialists.csv``.
    """

    ticket = require_ticket(data_dir, ticket_id)
    escalation = latest_working_row(out_dir, "escalation_decisions", ticket_id, workflow_run_id=workflow_run_id)
    if escalation is None:
        raise LookupError(f"no escalation found for ticket {ticket_id}. Run the escalate-to-specialist skill first.")
    specialists = read_csv(data_dir, "raw/it_specialists.csv")
    sp_id = escalation.get("specialist_id", "")
    matching = specialists.filter(pl.col("specialist_id") == sp_id).to_dicts()
    if not matching:
        raise LookupError(f"escalation references specialist {sp_id!r} which is not in raw/it_specialists.csv.")
    triage = latest_working_row(out_dir, "triage_decisions", ticket_id, workflow_run_id=workflow_run_id)
    return {
        "ticket": ticket,
        "escalation": escalation,
        "specialist": matching[0],
        "triage": triage or {},
        "systems": read_csv(data_dir, "dictionaries/systems.csv"),
    }


def _ticket_for_prompt(ticket: dict) -> dict:
    return {
        "ticket_id": ticket.get("ticket_id", ""),
        "subject": ticket.get("subject", ""),
        "description": ticket.get("description", ""),
        "affected_system": ticket.get("affected_system", ""),
        "customer_reported_urgency": ticket.get("customer_reported_urgency", ""),
        "business_impact_text": ticket.get("business_impact_text", ""),
        "error_or_symptom_detail": ticket.get("error_or_symptom_detail", ""),
        "steps_already_tried": ticket.get("steps_already_tried", ""),
        "expected_outcome": ticket.get("expected_outcome", ""),
    }


def _triage_for_prompt(triage: dict) -> dict:
    return {
        "assigned_category": triage.get("assigned_category", ""),
        "assigned_priority": triage.get("assigned_priority", ""),
        "recommended_specialist_group": triage.get("recommended_specialist_group", ""),
    }


def _escalation_for_prompt(escalation: dict) -> dict:
    return {
        "escalation_reason": escalation.get("escalation_reason", ""),
        "specific_question_for_specialist": escalation.get("specific_question_for_specialist", ""),
        "missing_information_flag": str(escalation.get("missing_information_flag", "")).lower() == "true",
        "handoff_summary": escalation.get("handoff_summary", ""),
    }


def _specialist_for_prompt(specialist: dict) -> dict:
    return {
        "specialist_id": specialist.get("specialist_id", ""),
        "name": specialist.get("name", ""),
        "specialist_group": specialist.get("specialist_group", ""),
        "seniority": specialist.get("seniority", ""),
        "systems_supported": specialist.get("systems_supported", ""),
    }


def build_llm_prompt(context: dict) -> str:
    """Compose the JSON prompt sent to the LLM specialist."""

    payload = {
        "task": (
            "You are an IT specialist. Diagnose the escalated ticket below and "
            "propose a customer-safe action. Use only the information present "
            "in the ticket and escalation context; do not invent customer "
            "facts. If key reproduction details are missing, return a lower "
            "confidence and flag follow-up."
        ),
        "decision_policy": [
            "Only describe diagnostic steps you can justify from the ticket text.",
            "The solution_summary must be plain language a non-technical "
            "customer can act on; never include internal log lines, credentials, "
            "or specialist-only jargon.",
            "Set requires_follow_up_flag=true only when the immediate action is "
            "a temporary mitigation and engineering work is needed for a "
            "permanent fix.",
            "If the escalation missing_information_flag is true, cap confidence "
            f"at {_MISSING_INFO_CONFIDENCE_CAP:.2f}.",
            "customer_action_required must ask the customer to confirm or "
            "reply, so the workflow gets a clean accept/reject signal.",
        ],
        "ticket": _ticket_for_prompt(context["ticket"]),
        "triage": _triage_for_prompt(context["triage"]),
        "escalation": _escalation_for_prompt(context["escalation"]),
        "specialist": _specialist_for_prompt(context["specialist"]),
        "required_json": {
            "root_cause": "one-sentence root cause hypothesis",
            "diagnostic_steps": "list of 2-5 short strings — what the specialist checked",
            "evidence_reviewed": "list of 1-4 short strings — what the specialist looked at",
            "solution_summary": "one short paragraph, customer-safe language",
            "customer_action_required": "one sentence asking the customer to confirm or reply",
            "confidence": "number between 0 and 1",
            "requires_follow_up_flag": "boolean",
            "reason": "one-sentence rationale for the confidence value",
        },
    }
    return json.dumps(payload, indent=2)


def _system_prompt() -> str:
    return (
        "You are a senior IT specialist diagnosing an escalated support ticket. "
        "Return only valid JSON. Be conservative with confidence — if the ticket "
        "lacks reproduction details, say so."
    )


def _load_env() -> None:
    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(Path.home() / ".env")


def call_llm_for_specialist_solution(
    context: dict,
    *,
    model: str = DEFAULT_MODEL,
    client: Any | None = None,
) -> dict:
    """Ask the LLM for the specialist solution and return its JSON object.

    For tests and offline demos, set ``SPECIALIST_INVESTIGATION_MOCK_JSON``
    to a JSON string and this function will return it verbatim without
    making a network call. Mirrors the pattern used by
    ``check-faq-resolution``.
    """

    mock_json = os.environ.get("SPECIALIST_INVESTIGATION_MOCK_JSON", "").strip()
    if mock_json:
        return json.loads(mock_json)

    _load_env()
    prompt = build_llm_prompt(context)
    result = ask_json(
        prompt,
        model=model,
        system=_system_prompt(),
        temperature=0,
        client=client,
    )
    if not isinstance(result, dict):
        raise TypeError("LLM did not return a JSON object.")
    return result


def _as_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return default


def _as_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return round(min(max(confidence, 0.0), 1.0), 2)


def _as_list_of_strings(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def normalize_llm_solution(raw: dict, context: dict) -> dict:
    """Validate the LLM response and apply the missing-info confidence cap."""

    root_cause = str(raw.get("root_cause") or "").strip() or "(no root cause provided)"
    diagnostic_steps = _as_list_of_strings(raw.get("diagnostic_steps")) or ["(no diagnostic steps reported)"]
    evidence_reviewed = _as_list_of_strings(raw.get("evidence_reviewed")) or ["(no evidence list reported)"]
    solution_summary = str(raw.get("solution_summary") or "").strip()
    customer_action = str(raw.get("customer_action_required") or "").strip()
    if not solution_summary:
        solution_summary = "Specialist did not produce a clear solution summary; human review needed."
    if not customer_action:
        customer_action = "Please reply and confirm whether this resolves the issue."

    confidence = _as_confidence(raw.get("confidence"))
    missing_info = str(context["escalation"].get("missing_information_flag", "")).lower() == "true"
    if missing_info and confidence > _MISSING_INFO_CONFIDENCE_CAP:
        confidence = _MISSING_INFO_CONFIDENCE_CAP

    requires_follow_up = _as_bool(raw.get("requires_follow_up_flag"))

    reason = str(raw.get("reason") or "LLM specialist judgement.").strip()
    notes = reason
    if missing_info:
        notes += " (Confidence capped because the escalation flagged missing customer information.)"

    return {
        "root_cause": root_cause,
        "diagnostic_steps": diagnostic_steps,
        "evidence_reviewed": evidence_reviewed,
        "solution_summary": solution_summary,
        "customer_action_required": customer_action,
        "confidence_score": confidence,
        "requires_follow_up_flag": requires_follow_up,
        "specialist_notes": notes,
    }


def build_solution_row(context: dict, *, model: str = DEFAULT_MODEL, client: Any | None = None) -> dict:
    """End-to-end: ask the LLM, validate, build the working CSV row."""

    raw = call_llm_for_specialist_solution(context, model=model, client=client)
    decision = normalize_llm_solution(raw, context)
    return {
        "ticket_id": context["ticket"]["ticket_id"],
        "created_at": now_iso(),
        "skill_name": SKILL_NAME,
        "specialist_id": context["specialist"]["specialist_id"],
        "specialist_name": context["specialist"].get("name", ""),
        "specialist_group": context["specialist"].get("specialist_group", ""),
        "root_cause": decision["root_cause"],
        "diagnostic_steps": pipe_join(decision["diagnostic_steps"]),
        "evidence_reviewed": pipe_join(decision["evidence_reviewed"]),
        "solution_summary": decision["solution_summary"],
        "customer_action_required": decision["customer_action_required"],
        "specialist_notes": decision["specialist_notes"],
        "requires_follow_up_flag": decision["requires_follow_up_flag"],
        "confidence_score": decision["confidence_score"],
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "raw/it_specialists.csv",
                "dictionaries/systems.csv",
                "working/escalation_decisions.csv",
                "working/triage_decisions.csv",
            ]
        ),
        "decision_summary": (
            f"llm_model={model}; "
            f"specialist={context['specialist']['specialist_id']}; "
            f"confidence={decision['confidence_score']}; "
            f"follow_up={decision['requires_follow_up_flag']}"
        ),
    }


def write_specialist_solution(out_dir: Path, solution: dict) -> None:
    append_csv_row(Path(out_dir) / f"{SOLUTIONS_TABLE}.csv", solution)


def main(argv: list[str] | None = None) -> int:
    parser = make_skill_parser(__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    workflow_run_id = args.workflow_run_id or default_workflow_run_id()
    read_workflow_run_id = workflow_run_id if args.workflow_run_id else None
    step_id = args.step_id or default_step_id(SKILL_NAME)

    existing = find_step_row(out_dir, SOLUTIONS_TABLE, workflow_run_id, step_id)
    if existing and args.idempotency_mode == "skip":
        env = make_envelope(
            status=STATUS_SKIPPED,
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action=NEXT_ACTION,
            outputs={"existing_row": existing},
            artifact_refs=[f"working/{SOLUTIONS_TABLE}.csv"],
        )
        emit_envelope(
            env,
            as_json=args.as_json,
            text_summary=(
                f"specialist solution already recorded for "
                f"workflow_run_id={workflow_run_id} step_id={step_id} — skipping."
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
        context = load_investigation_context(data_dir, out_dir, args.ticket_id, workflow_run_id=read_workflow_run_id)
        row = build_solution_row(context, model=args.model)
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
            next_action="escalate-to-specialist",
        )
    except (RuntimeError, ValueError, TypeError, json.JSONDecodeError, OpenAIError) as exc:
        return emit_error(
            **err_kwargs,
            error_code="llm_decision_failed",
            message=str(exc),
            exit_code=4,
        )

    row["workflow_run_id"] = workflow_run_id
    row["step_id"] = step_id
    if args.idempotency_mode == "replace":
        replace_step_row(
            Path(out_dir) / f"{SOLUTIONS_TABLE}.csv",
            row,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
    else:
        write_specialist_solution(out_dir, row)

    review_required = needs_human_review(row["confidence_score"], extra=bool(row["requires_follow_up_flag"]))

    text_summary = (
        f"Specialist solution for {row['ticket_id']}:\n"
        f"  LLM model         : {args.model}\n"
        f"  specialist        : {row['specialist_id']} ({row['specialist_name']})\n"
        f"  root cause        : {row['root_cause']}\n"
        f"  diagnostic steps  : {row['diagnostic_steps']}\n"
        f"  evidence reviewed : {row['evidence_reviewed']}\n"
        f"  customer summary  : {row['solution_summary']}\n"
        f"  customer action   : {row['customer_action_required']}\n"
        f"  follow-up needed? : {row['requires_follow_up_flag']}\n"
        f"  confidence        : {row['confidence_score']}\n"
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
            "action": "specialist_solution_created",
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": row["confidence_score"],
            "needs_human_review": "true" if review_required else "false",
            "notes": row["specialist_notes"],
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
        artifact_refs=[f"working/{SOLUTIONS_TABLE}.csv"],
        outputs={
            "specialist_id": row["specialist_id"],
            "root_cause": row["root_cause"],
            "diagnostic_steps": row["diagnostic_steps"],
            "evidence_reviewed": row["evidence_reviewed"],
            "solution_summary": row["solution_summary"],
            "customer_action_required": row["customer_action_required"],
            "requires_follow_up_flag": row["requires_follow_up_flag"],
        },
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
