"""Step 10: ask an LLM to draft a candidate FAQ entry from an accepted specialist solution.

Runs only on the specialist branch after positive customer feedback.
Reads the ticket, the specialist solution, and the customer's accept
reply, and asks an LLM to propose a knowledge-base entry shaped like
``data/raw/faq_knowledge_base.csv``. Writes one row to
``data/working/faq_candidates.csv``; the downstream
``approve-faq-promotion`` automation surfaces it for human review.

Run from the repo root::

    uv run python skills/draft-faq-candidate/scripts/draft_faq_candidate.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

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

SKILL_NAME = "draft-faq-candidate"
CANDIDATES_TABLE = "faq_candidates"
NEXT_ACTION = "approve-faq-promotion"
DEFAULT_MODEL = os.environ.get("FAQ_CANDIDATE_MODEL", DEFAULT_LLM_MODEL)


def load_candidate_context(
    data_dir: Path,
    out_dir: Path,
    ticket_id: str,
    *,
    workflow_run_id: str | None = None,
) -> dict:
    """Return ticket, specialist solution, customer feedback, and dictionaries.

    Raises ``LookupError`` if there is no specialist solution or no
    ``close_ticket`` feedback decision for this ticket in the run.
    """

    ticket = require_ticket(data_dir, ticket_id)
    solution = latest_working_row(out_dir, "specialist_solutions", ticket_id, workflow_run_id=workflow_run_id)
    if solution is None:
        raise LookupError(
            f"no specialist solution found for ticket {ticket_id}. The FAQ candidate skill "
            f"only runs on solutions the customer has accepted."
        )
    feedback = latest_working_row(out_dir, "feedback_decisions", ticket_id, workflow_run_id=workflow_run_id)
    if feedback is None or feedback.get("next_action") != "close_ticket":
        raise LookupError(
            f"no positive customer feedback decision found for ticket {ticket_id}. "
            f"Only accepted solutions become FAQ candidates."
        )
    categories = read_csv(data_dir, "dictionaries/categories.csv")["category"].to_list()
    systems = read_csv(data_dir, "dictionaries/systems.csv")
    system_names = systems.columns and systems["system_name"].to_list() if "system_name" in systems.columns else []
    return {
        "ticket": ticket,
        "solution": solution,
        "feedback": feedback,
        "valid_categories": categories,
        "valid_systems": system_names,
    }


def _ticket_for_prompt(ticket: dict) -> dict:
    return {
        "ticket_id": ticket.get("ticket_id", ""),
        "subject": ticket.get("subject", ""),
        "description": ticket.get("description", ""),
        "affected_system": ticket.get("affected_system", ""),
        "error_or_symptom_detail": ticket.get("error_or_symptom_detail", ""),
        "steps_already_tried": ticket.get("steps_already_tried", ""),
    }


def _solution_for_prompt(solution: dict) -> dict:
    return {
        "root_cause": solution.get("root_cause", ""),
        "diagnostic_steps": solution.get("diagnostic_steps", ""),
        "evidence_reviewed": solution.get("evidence_reviewed", ""),
        "solution_summary": solution.get("solution_summary", ""),
        "customer_action_required": solution.get("customer_action_required", ""),
    }


def build_llm_prompt(context: dict) -> str:
    """Compose the JSON prompt sent to the LLM."""

    payload = {
        "task": (
            "Draft a candidate FAQ entry from this resolved support ticket. "
            "The customer has confirmed the specialist's solution worked. "
            "Capture the issue and fix at a level of generality that will "
            "match similar future tickets — not just this one customer's wording."
        ),
        "decision_policy": [
            "Choose category and system_name from the provided lists; if nothing fits exactly, pick 'other'.",
            "issue_pattern is one short snake_case phrase that future ticket triagers can search for.",
            "symptoms is a JSON list of 1–4 short observable user-visible behaviors.",
            "solution_steps is a JSON list of 1–6 imperative steps the customer can perform.",
            "required_customer_info is a JSON list of facts the IT team needs from the customer to apply this FAQ.",
            "Do not include the original customer's name, email, or company. Generalize.",
            "Set confidence high (>0.75) only if the solution is broadly reusable, not customer-specific.",
        ],
        "ticket": _ticket_for_prompt(context["ticket"]),
        "specialist_solution": _solution_for_prompt(context["solution"]),
        "customer_feedback_text": context["feedback"].get("customer_feedback_text", ""),
        "valid_categories": context["valid_categories"],
        "valid_systems": context["valid_systems"],
        "required_json": {
            "category": "one of valid_categories",
            "system_name": "one of valid_systems, or empty if not system-specific",
            "issue_pattern": "snake_case short phrase",
            "symptoms": "list of short strings",
            "solution_steps": "list of short strings",
            "required_customer_info": "list of short strings",
            "confidence": "number between 0 and 1",
            "reasoning": "one-paragraph explanation",
        },
    }
    return json.dumps(payload, indent=2)


def _system_prompt() -> str:
    return (
        "You are a knowledge-base curator for an IT support team. "
        "Return only valid JSON. Generalize a single ticket's solution into a "
        "reusable FAQ entry while keeping the wording neutral and customer-safe."
    )


def _load_env() -> None:
    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(Path.home() / ".env")


def call_llm_for_candidate(context: dict, *, model: str = DEFAULT_MODEL, client: Any | None = None) -> dict:
    """Ask the LLM for the FAQ candidate and return its raw JSON object."""

    mock_json = os.environ.get("FAQ_CANDIDATE_MOCK_JSON", "").strip()
    if mock_json:
        return json.loads(mock_json)

    _load_env()
    prompt = build_llm_prompt(context)
    result = ask_json(
        prompt,
        model=model,
        system=_system_prompt(),
        temperature=0,
        max_tokens=900,
        client=client,
    )
    if not isinstance(result, dict):
        raise TypeError("LLM did not return a JSON object.")
    return result


def _as_confidence(value: object) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        c = 0.0
    return round(min(max(c, 0.0), 1.0), 2)


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split("|") if part.strip()]
    return []


def normalize_candidate(raw: dict, *, valid_categories: list[str], valid_systems: list[str]) -> dict:
    """Convert the LLM response into the row written to ``faq_candidates.csv``."""

    category = str(raw.get("category") or "").strip().lower() or "other"
    if valid_categories and category not in {c.lower() for c in valid_categories}:
        category = "other"

    system_name = str(raw.get("system_name") or "").strip()
    if system_name and valid_systems and system_name not in valid_systems:
        # The LLM proposed a system that isn't in the dictionary. Keep
        # the proposed name but leave a note for the supervisor.
        system_name = system_name
    return {
        "category": category,
        "system_name": system_name,
        "issue_pattern": str(raw.get("issue_pattern") or "").strip(),
        "symptoms": pipe_join(_as_list(raw.get("symptoms"))),
        "solution_steps": pipe_join(_as_list(raw.get("solution_steps"))),
        "required_customer_info": pipe_join(_as_list(raw.get("required_customer_info"))),
        "confidence": _as_confidence(raw.get("confidence")),
        "reasoning": str(raw.get("reasoning") or "").strip(),
    }


def build_candidate_row(context: dict, *, model: str = DEFAULT_MODEL, client: Any | None = None) -> dict:
    """Build a ``faq_candidates.csv`` row from the LLM proposal."""

    raw = call_llm_for_candidate(context, model=model, client=client)
    normalized = normalize_candidate(
        raw,
        valid_categories=context["valid_categories"],
        valid_systems=context["valid_systems"],
    )
    return {
        "ticket_id": context["ticket"]["ticket_id"],
        "created_at": now_iso(),
        "skill_name": SKILL_NAME,
        "source_solution_skill_name": context["solution"].get("skill_name", ""),
        "category": normalized["category"],
        "system_name": normalized["system_name"],
        "issue_pattern": normalized["issue_pattern"],
        "symptoms": normalized["symptoms"],
        "solution_steps": normalized["solution_steps"],
        "required_customer_info": normalized["required_customer_info"],
        "confidence": normalized["confidence"],
        "reasoning": normalized["reasoning"],
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "working/specialist_solutions.csv",
                "working/feedback_decisions.csv",
                "dictionaries/categories.csv",
                "dictionaries/systems.csv",
            ]
        ),
        "decision_summary": (
            f"llm_model={model}; category={normalized['category']}; "
            f"system={normalized['system_name'] or '(none)'}; "
            f"issue_pattern={normalized['issue_pattern'] or '(none)'}; "
            f"confidence={normalized['confidence']}"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_skill_parser(__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    workflow_run_id = args.workflow_run_id or default_workflow_run_id()
    read_workflow_run_id = workflow_run_id if args.workflow_run_id else None
    step_id = args.step_id or default_step_id(SKILL_NAME)

    existing = find_step_row(out_dir, CANDIDATES_TABLE, workflow_run_id, step_id)
    if existing and args.idempotency_mode == "skip":
        env = make_envelope(
            status=STATUS_SKIPPED,
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action=NEXT_ACTION,
            outputs={"existing_row": existing},
            artifact_refs=[f"working/{CANDIDATES_TABLE}.csv"],
        )
        emit_envelope(
            env,
            as_json=args.as_json,
            text_summary=f"FAQ candidate already recorded for step_id={step_id} — skipping.",
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
        context = load_candidate_context(
            data_dir,
            out_dir,
            args.ticket_id,
            workflow_run_id=read_workflow_run_id,
        )
        row = build_candidate_row(context, model=args.model)
    except KeyError as exc:
        return emit_error(**err_kwargs, error_code="ticket_not_found", message=str(exc))
    except LookupError as exc:
        return emit_error(
            **err_kwargs,
            error_code="missing_upstream",
            message=str(exc),
            exit_code=3,
            next_action="verify-feedback-close-or-reopen",
        )
    except FileNotFoundError as exc:
        return emit_error(**err_kwargs, error_code="missing_data", message=str(exc))
    except (RuntimeError, ValueError, TypeError, json.JSONDecodeError, OpenAIError) as exc:
        return emit_error(**err_kwargs, error_code="llm_decision_failed", message=str(exc), exit_code=4)

    row["workflow_run_id"] = workflow_run_id
    row["step_id"] = step_id
    if args.idempotency_mode == "replace":
        replace_step_row(
            Path(out_dir) / f"{CANDIDATES_TABLE}.csv",
            row,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
    else:
        append_csv_row(Path(out_dir) / f"{CANDIDATES_TABLE}.csv", row)

    review_required = needs_human_review(row["confidence"])

    text_summary = (
        f"FAQ candidate drafted for {row['ticket_id']}:\n"
        f"  category           : {row['category']}\n"
        f"  system             : {row['system_name'] or '(none)'}\n"
        f"  issue pattern      : {row['issue_pattern']}\n"
        f"  symptoms           : {row['symptoms']}\n"
        f"  solution steps     : {row['solution_steps']}\n"
        f"  required info      : {row['required_customer_info']}\n"
        f"  confidence         : {row['confidence']}\n"
        f"  review required?   : {review_required}\n"
        f"\nNext valid action: {NEXT_ACTION} (supervisor approves or skips)."
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": row["ticket_id"],
            "created_at": row["created_at"],
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": "faq_candidate_drafted",
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": row["confidence"],
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
        next_action=NEXT_ACTION,
        confidence=row["confidence"],
        review_required=review_required,
        artifact_refs=[f"working/{CANDIDATES_TABLE}.csv"],
        outputs={
            "category": row["category"],
            "system_name": row["system_name"],
            "issue_pattern": row["issue_pattern"],
            "symptoms": row["symptoms"],
            "solution_steps": row["solution_steps"],
            "required_customer_info": row["required_customer_info"],
            "confidence": row["confidence"],
            "reasoning": row["reasoning"],
        },
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
