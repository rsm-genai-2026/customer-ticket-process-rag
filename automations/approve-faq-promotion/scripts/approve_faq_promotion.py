"""Step 11: human-in-the-loop approval of an LLM-drafted FAQ candidate.

Sits between :mod:`draft_faq_candidate` and :mod:`audit_ticket_process`
on the specialist-branch close path. The supervisor reviews the
candidate, optionally edits any field, and either approves (the entry is
appended to the run's copy of ``data/raw/faq_knowledge_base.csv``) or
skips (the decision is recorded but the FAQ KB is not changed).

Two invocation modes, one script:

* Mode 1 — *awaiting decision*. No ``--decision`` flag. Reads the
  latest candidate row and emits an ``awaiting_input`` envelope so the
  orchestrator can surface the candidate to a supervisor.
* Mode 2 — *decision applied*. ``--decision approve`` or
  ``--decision skip``, with an optional ``--candidate-json`` containing
  the edited candidate as JSON. Writes a row to
  ``data/working/faq_promotion_decisions.csv``. On approve also appends
  a row to ``data/raw/faq_knowledge_base.csv`` inside the run's data
  directory (the repo source data is never modified — the orchestrator
  copies baseline data into ``/tmp`` per run).

Run from the repo root::

    uv run python automations/approve-faq-promotion/scripts/approve_faq_promotion.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lib.ticketing_common import (  # noqa: E402
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
    pipe_join,
)

SKILL_NAME = "approve-faq-promotion"
CANDIDATES_TABLE = "faq_candidates"
DECISIONS_TABLE = "faq_promotion_decisions"
SELF_NEXT_ACTION = "approve-faq-promotion"
DOWNSTREAM_NEXT_ACTION = "audit-ticket-process"
STATUS_AWAITING = "awaiting_input"

FAQ_KB_REL_PATH = Path("raw") / "faq_knowledge_base.csv"

# Columns we treat as editable. The remaining FAQ KB columns (last_updated,
# owner, active_flag) are set deterministically by this script.
EDITABLE_FIELDS = (
    "category",
    "system_name",
    "issue_pattern",
    "symptoms",
    "solution_steps",
    "required_customer_info",
)


def _parse_candidate_overrides(raw: str) -> dict:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--candidate-json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--candidate-json must decode to a JSON object")
    overrides: dict = {}
    for field in EDITABLE_FIELDS:
        if field not in parsed:
            continue
        value = parsed[field]
        if isinstance(value, list):
            overrides[field] = pipe_join(value)
        elif value is None:
            overrides[field] = ""
        else:
            overrides[field] = str(value).strip()
    return overrides


def _merge_candidate(candidate_row: dict, overrides: dict) -> dict:
    merged = {field: candidate_row.get(field, "") for field in EDITABLE_FIELDS}
    merged.update(overrides)
    return merged


def _next_faq_id(faq_kb_path: Path, ticket_id: str) -> str:
    """Pick a stable, unique FAQ id."""

    suffix = re.sub(r"[^A-Za-z0-9]", "", ticket_id) or "X"
    candidate = f"FAQ-{suffix}"
    if not faq_kb_path.exists():
        return candidate
    with faq_kb_path.open(newline="", encoding="utf-8") as f:
        existing_ids = {row.get("faq_id", "") for row in csv.DictReader(f)}
    if candidate not in existing_ids:
        return candidate
    n = 2
    while f"{candidate}-{n}" in existing_ids:
        n += 1
    return f"{candidate}-{n}"


def append_faq_row(data_dir: Path, ticket_id: str, fields: dict) -> str:
    """Append a row to the per-run FAQ KB. Returns the new ``faq_id``.

    No additional locking here — ``append_csv_row`` already serialises
    writes per-directory. The id-selection-then-write window is benign
    because the workflow never writes to the FAQ KB concurrently.
    """

    faq_kb_path = Path(data_dir) / FAQ_KB_REL_PATH
    faq_kb_path.parent.mkdir(parents=True, exist_ok=True)
    new_id = _next_faq_id(faq_kb_path, ticket_id)
    row = {
        "faq_id": new_id,
        "category": fields.get("category", ""),
        "system_name": fields.get("system_name", ""),
        "issue_pattern": fields.get("issue_pattern", ""),
        "symptoms": fields.get("symptoms", ""),
        "solution_steps": fields.get("solution_steps", ""),
        "required_customer_info": fields.get("required_customer_info", ""),
        "last_updated": date.today().isoformat(),
        "owner": "workflow_promotion",
        "active_flag": "true",
    }
    append_csv_row(faq_kb_path, row)
    return new_id


def _emit_awaiting(args, candidate_row: dict) -> int:
    env = make_envelope(
        status=STATUS_AWAITING,
        skill_name=SKILL_NAME,
        workflow_run_id=args.workflow_run_id or default_workflow_run_id(),
        step_id=args.step_id or default_step_id(SKILL_NAME),
        ticket_id=args.ticket_id,
        next_action=SELF_NEXT_ACTION,
        review_required=True,
        outputs={
            "candidate": {field: candidate_row.get(field, "") for field in EDITABLE_FIELDS},
            "confidence": candidate_row.get("confidence", ""),
            "reasoning": candidate_row.get("reasoning", ""),
        },
        artifact_refs=[f"working/{CANDIDATES_TABLE}.csv"],
    )
    text_summary = (
        f"FAQ candidate for {args.ticket_id} is awaiting supervisor approval.\n"
        f"  category           : {candidate_row.get('category', '')}\n"
        f"  system             : {candidate_row.get('system_name', '')}\n"
        f"  issue pattern      : {candidate_row.get('issue_pattern', '')}\n"
        f"  symptoms           : {candidate_row.get('symptoms', '')}\n"
        f"  solution steps     : {candidate_row.get('solution_steps', '')}\n"
        f"  required info      : {candidate_row.get('required_customer_info', '')}\n"
        f"  confidence         : {candidate_row.get('confidence', '')}\n"
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = make_skill_parser(__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--decision", choices=["", "approve", "skip"], default="")
    parser.add_argument("--candidate-json", default="")
    parser.add_argument("--reviewer-notes", default="")
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

    candidate_row = latest_working_row(out_dir, CANDIDATES_TABLE, args.ticket_id, workflow_run_id=read_workflow_run_id)
    if not candidate_row:
        return emit_error(
            **err_kwargs,
            error_code="missing_upstream",
            message=(f"no FAQ candidate found for ticket {args.ticket_id}. Run draft-faq-candidate first."),
            exit_code=3,
            next_action="draft-faq-candidate",
        )

    if not args.decision:
        return _emit_awaiting(args, candidate_row)

    try:
        overrides = _parse_candidate_overrides(args.candidate_json)
    except ValueError as exc:
        return emit_error(**err_kwargs, error_code="invalid_input", message=str(exc))

    final_fields = _merge_candidate(candidate_row, overrides)
    edited = bool(overrides) and any(final_fields.get(f, "") != candidate_row.get(f, "") for f in EDITABLE_FIELDS)
    new_faq_id = ""
    if args.decision == "approve":
        new_faq_id = append_faq_row(data_dir, args.ticket_id, final_fields)

    decision_row = {
        "ticket_id": args.ticket_id,
        "created_at": now_iso(),
        "skill_name": SKILL_NAME,
        "candidate_ticket_id": candidate_row.get("ticket_id", ""),
        "decision": args.decision,
        "new_faq_id": new_faq_id,
        "edited": "true" if edited else "false",
        "reviewer_notes": args.reviewer_notes,
        "workflow_run_id": workflow_run_id,
        "step_id": step_id,
    }
    append_csv_row(out_dir / f"{DECISIONS_TABLE}.csv", decision_row)

    append_action_log(
        out_dir,
        {
            "ticket_id": args.ticket_id,
            "created_at": decision_row["created_at"],
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": f"faq_promotion_{args.decision}",
            "inputs_used": f"working/{CANDIDATES_TABLE}.csv",
            "decision_summary": (
                f"decision={args.decision}; edited={edited}; new_faq_id={new_faq_id or '(none)'}; "
                f"reviewer_notes={args.reviewer_notes[:120]}"
            ),
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
        next_action=DOWNSTREAM_NEXT_ACTION,
        review_required=False,
        outputs={
            "decision": args.decision,
            "new_faq_id": new_faq_id,
            "edited": edited,
        },
        artifact_refs=[f"working/{DECISIONS_TABLE}.csv"] + ([f"raw/{FAQ_KB_REL_PATH.name}"] if new_faq_id else []),
    )
    text_summary = (
        f"FAQ promotion for {args.ticket_id}: decision={args.decision}; "
        f"new_faq_id={new_faq_id or '(none)'}; edited={edited}.\n"
        f"Next: {DOWNSTREAM_NEXT_ACTION}."
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
