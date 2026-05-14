"""Step 3: ask an LLM whether an existing FAQ resolves a ticket.

This skill is intentionally simple for teaching:

1. Load the submitted ticket, the triage decision, and all active FAQs.
2. Send that context to an LLM.
3. Ask for one JSON decision: best FAQ match, confidence, and reason.
4. Write the same ``faq_decisions.csv`` row the rest of the workflow expects.

Run from the repo root::

    uv run python skills/check-faq-resolution/scripts/check_faq_resolution.py \\
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

SKILL_NAME = "check-faq-resolution"
FAQ_DECISIONS_TABLE = "faq_decisions"
DEFAULT_MODEL = os.environ.get("FAQ_RESOLUTION_MODEL", DEFAULT_LLM_MODEL)
DRAFT_CONFIDENCE_THRESHOLD = 0.70


def _load_faq_kb(data_dir: Path) -> pl.DataFrame:
    """Return the FAQ knowledge base, local CSV by default.

    When ``FAQ_KB_URI`` is set in the environment (or in a ``.env`` file
    next to this repo or in ``~/.env``), the function reads from that
    URI instead — e.g. a Postgres database the course already shares.
    The URI is anything ``polars.read_database_uri`` accepts.

    This is the only place in the skill that knows where the FAQ data
    lives. Everything downstream still gets the same polars DataFrame.
    """

    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(Path.home() / ".env")
    uri = os.environ.get("FAQ_KB_URI", "").strip()
    if uri:
        return pl.read_database_uri("SELECT * FROM faq_knowledge_base", uri)
    return read_csv(data_dir, "raw/faq_knowledge_base.csv")


def load_faq_context(
    data_dir: Path,
    out_dir: Path,
    ticket_id: str,
    *,
    mode: str = MODE_DEMO,
    workflow_run_id: str | None = None,
) -> dict:
    """Load the ticket, active FAQs, and upstream triage decision."""

    ticket = require_ticket(data_dir, ticket_id)
    faqs = _load_faq_kb(data_dir).filter(pl.col("active_flag"))

    triage = latest_working_row(out_dir, "triage_decisions", ticket_id, workflow_run_id=workflow_run_id)
    triage_source = "working/triage_decisions.csv"
    if triage is None:
        if mode != MODE_DEMO:
            raise LookupError(
                f"no triage decision available for ticket {ticket_id} in working/. "
                f"Run the classify-prioritize-ticket skill first, or pass --mode demo "
                f"to fall back to processed/ticket_triage.csv."
            )
        historical = read_csv(data_dir, "processed/ticket_triage.csv")
        rows = historical.filter(pl.col("ticket_id") == ticket_id).to_dicts()
        if not rows:
            raise LookupError(
                f"no triage decision available for ticket {ticket_id}. Run the classify-prioritize-ticket skill first."
            )
        triage = rows[0]
        triage_source = "processed/ticket_triage.csv (demo fallback)"

    return {
        "ticket": ticket,
        "faqs": faqs,
        "triage": triage,
        "triage_source": triage_source,
    }


def _ticket_for_prompt(ticket: dict, triage: dict) -> dict:
    """Keep only fields the model needs for the FAQ decision."""

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
        "assigned_category": triage.get("assigned_category", ""),
        "assigned_priority": triage.get("assigned_priority", ""),
        "recommended_specialist_group": triage.get("recommended_specialist_group", ""),
    }


def _faq_for_prompt(faq: dict) -> dict:
    return {
        "faq_id": faq.get("faq_id", ""),
        "category": faq.get("category", ""),
        "system_name": faq.get("system_name", ""),
        "issue_pattern": faq.get("issue_pattern", ""),
        "symptoms": faq.get("symptoms", ""),
        "solution_steps": faq.get("solution_steps", ""),
        "required_customer_info": faq.get("required_customer_info", ""),
    }


def build_llm_prompt(context: dict) -> str:
    """Create the full ticket + FAQ prompt sent to the LLM."""

    faqs = [_faq_for_prompt(faq) for faq in context["faqs"].to_dicts()]
    payload = {
        "task": "Choose the single FAQ that directly resolves this support ticket, or choose no_match.",
        "decision_policy": [
            "Use the FAQ only when its symptoms and solution steps directly address the ticket.",
            "Do not match merely because the category or affected system is similar.",
            "If no FAQ directly applies, set faq_match_found to false and faq_id to an empty string.",
            "Set required_customer_info_available to true only when the ticket contains "
            "the information needed by the chosen FAQ.",
            f"Use confidence >= {DRAFT_CONFIDENCE_THRESHOLD:.2f} only for strong, directly supported matches.",
        ],
        "ticket": _ticket_for_prompt(context["ticket"], context["triage"]),
        "faqs": faqs,
        "required_json": {
            "faq_match_found": "boolean",
            "faq_id": "FAQ id string, or empty string when no FAQ directly applies",
            "confidence": "number between 0 and 1",
            "required_customer_info_available": "boolean",
            "reason": "brief business-readable explanation",
            "ticket_evidence": "short quote or paraphrase from the ticket",
            "faq_evidence": "short quote or paraphrase from the FAQ, or empty string for no_match",
        },
    }
    return json.dumps(payload, indent=2)


def _system_prompt() -> str:
    return (
        "You are an FAQ matching assistant for an IT support workflow. "
        "Return only valid JSON. Be conservative: choose an FAQ only when it directly resolves the ticket."
    )


def _load_env() -> None:
    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(Path.home() / ".env")


def call_llm_for_faq_decision(context: dict, *, model: str = DEFAULT_MODEL, client: Any | None = None) -> dict:
    """Ask the LLM for the FAQ decision and return its raw JSON object."""

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


def normalize_llm_decision(raw: dict, valid_faq_ids: set[str]) -> dict:
    """Convert the LLM response into the workflow's decision fields."""

    faq_id = str(raw.get("faq_id") or "").strip()
    if faq_id.lower() in {"none", "no_match", "no match", "null"}:
        faq_id = ""

    confidence = _as_confidence(raw.get("confidence"))
    match_found = _as_bool(raw.get("faq_match_found")) and bool(faq_id)

    if faq_id and faq_id not in valid_faq_ids:
        match_found = False
        reason = f"LLM returned unknown FAQ id {faq_id}; treating as no match."
        faq_id = ""
        confidence = min(confidence, 0.40)
    else:
        reason = str(raw.get("reason") or "LLM evaluated the ticket against the active FAQ table.").strip()

    required_info_ok = _as_bool(raw.get("required_customer_info_available"), default=match_found)
    if not match_found:
        required_info_ok = False

    recommended_next_step = (
        "draft-faq-response"
        if match_found and required_info_ok and confidence >= DRAFT_CONFIDENCE_THRESHOLD
        else "escalate-to-specialist"
    )

    ticket_evidence = str(raw.get("ticket_evidence") or "").strip()
    faq_evidence = str(raw.get("faq_evidence") or "").strip()
    evidence = []
    if ticket_evidence:
        evidence.append(f"ticket: {ticket_evidence}")
    if faq_evidence:
        evidence.append(f"faq: {faq_evidence}")
    if evidence:
        reason = f"{reason} Evidence: {'; '.join(evidence)}"

    return {
        "faq_match_found": match_found,
        "faq_id": faq_id,
        "match_confidence": confidence,
        "required_customer_info_available": required_info_ok,
        "faq_application_reason": reason,
        "recommended_next_step": recommended_next_step,
    }


def build_faq_decision_row(context: dict, *, model: str = DEFAULT_MODEL, client: Any | None = None) -> dict:
    """Ask the LLM and build a row for ``faq_decisions.csv``."""

    raw = call_llm_for_faq_decision(context, model=model, client=client)
    valid_faq_ids = set(context["faqs"]["faq_id"].to_list()) if not context["faqs"].is_empty() else set()
    decision = normalize_llm_decision(raw, valid_faq_ids)
    candidate_ids = pipe_join(sorted(valid_faq_ids))

    return {
        "ticket_id": context["ticket"]["ticket_id"],
        "created_at": now_iso(),
        "skill_name": SKILL_NAME,
        "faq_match_found": decision["faq_match_found"],
        "faq_id": decision["faq_id"],
        "match_confidence": decision["match_confidence"],
        "search_terms": "llm_full_faq_review",
        "candidate_faq_ids": candidate_ids,
        "required_customer_info_available": decision["required_customer_info_available"],
        "faq_application_reason": decision["faq_application_reason"],
        "recommended_next_step": decision["recommended_next_step"],
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "raw/faq_knowledge_base.csv",
                context["triage_source"],
            ]
        ),
        "decision_summary": (
            f"llm_model={model}; "
            f"match={decision['faq_match_found']}; "
            f"faq_id={decision['faq_id'] or '(none)'}; "
            f"next={decision['recommended_next_step']}"
        ),
    }


def write_faq_decision(out_dir: Path, decision: dict) -> None:
    append_csv_row(Path(out_dir) / f"{FAQ_DECISIONS_TABLE}.csv", decision)


def main(argv: list[str] | None = None) -> int:
    parser = make_skill_parser(__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    workflow_run_id = args.workflow_run_id or default_workflow_run_id()
    read_workflow_run_id = workflow_run_id if args.workflow_run_id else None
    step_id = args.step_id or default_step_id(SKILL_NAME)

    existing = find_step_row(out_dir, FAQ_DECISIONS_TABLE, workflow_run_id, step_id)
    if existing and args.idempotency_mode == "skip":
        env = make_envelope(
            status=STATUS_SKIPPED,
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action=existing.get("recommended_next_step", ""),
            outputs={"existing_row": existing},
            artifact_refs=[f"working/{FAQ_DECISIONS_TABLE}.csv"],
        )
        emit_envelope(
            env,
            as_json=args.as_json,
            text_summary=(
                f"faq decision already recorded for workflow_run_id={workflow_run_id} step_id={step_id} — skipping."
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
        context = load_faq_context(
            data_dir,
            out_dir,
            args.ticket_id,
            mode=args.mode,
            workflow_run_id=read_workflow_run_id,
        )
        row = build_faq_decision_row(context, model=args.model)
    except KeyError as exc:
        return emit_error(**err_kwargs, error_code="ticket_not_found", message=str(exc))
    except LookupError as exc:
        return emit_error(
            **err_kwargs,
            error_code="missing_upstream",
            message=str(exc),
            exit_code=3,
            next_action="classify-prioritize-ticket",
        )
    except FileNotFoundError as exc:
        return emit_error(**err_kwargs, error_code="missing_data", message=str(exc))
    except (RuntimeError, ValueError, TypeError, json.JSONDecodeError, OpenAIError) as exc:
        return emit_error(**err_kwargs, error_code="llm_decision_failed", message=str(exc), exit_code=4)

    row["workflow_run_id"] = workflow_run_id
    row["step_id"] = step_id
    if args.idempotency_mode == "replace":
        replace_step_row(
            Path(out_dir) / f"{FAQ_DECISIONS_TABLE}.csv",
            row,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
    else:
        write_faq_decision(out_dir, row)

    review_required = needs_human_review(
        row["match_confidence"],
        extra=(row["faq_match_found"] and not row["required_customer_info_available"]),
    )

    text_summary = (
        f"FAQ check for {row['ticket_id']}:\n"
        f"  LLM model          : {args.model}\n"
        f"  match found        : {row['faq_match_found']}\n"
        f"  matched FAQ        : {row['faq_id'] or '(none)'}\n"
        f"  match confidence   : {row['match_confidence']}\n"
        f"  required info ok?  : {row['required_customer_info_available']}\n"
        f"  FAQ ids reviewed   : {row['candidate_faq_ids'] or '(none)'}\n"
        f"  reason             : {row['faq_application_reason']}\n"
        f"  review required?   : {review_required}\n"
        f"\nNext valid action: {row['recommended_next_step']}."
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": row["ticket_id"],
            "created_at": row["created_at"],
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": "faq_decision",
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": row["match_confidence"],
            "needs_human_review": "true" if review_required else "false",
            "notes": f"mode={args.mode}",
        },
    )

    env = make_envelope(
        status=STATUS_OK,
        skill_name=SKILL_NAME,
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        ticket_id=row["ticket_id"],
        next_action=row["recommended_next_step"],
        confidence=row["match_confidence"],
        review_required=review_required,
        artifact_refs=[f"working/{FAQ_DECISIONS_TABLE}.csv"],
        outputs={
            "faq_match_found": row["faq_match_found"],
            "faq_id": row["faq_id"],
            "match_confidence": row["match_confidence"],
            "search_terms": row["search_terms"],
            "candidate_faq_ids": row["candidate_faq_ids"],
            "required_customer_info_available": row["required_customer_info_available"],
            "faq_application_reason": row["faq_application_reason"],
        },
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
