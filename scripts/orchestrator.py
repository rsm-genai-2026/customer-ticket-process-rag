"""Local web demo + workflow orchestrator for the customer-ticket process.

Run from the repo root:

    uv run python scripts/orchestrator.py

The server provides a small browser UI for submitting a ticket, then
drives one step at a time — either a deterministic automation under
``automations/`` or a real LLM skill under ``skills/`` — until a customer
response has been drafted and recorded as sent. The orchestrator is not
an LLM agent: it does not load SKILL.md files at request time. It is
boring control-flow code on purpose; the work and the AI judgement live
inside each step script. Demo runs use an isolated copy of the baseline
data under /tmp by default; data/raw is never modified.

The HTML/CSS/JS for the browser UI lives next door in
``scripts/orchestrator_ui.html`` and is loaded at request time by
:func:`render_index`.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
import string
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.examples.ticket_scenarios import browser_example_tickets  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
DEFAULT_WORK_ROOT = Path(os.environ.get("TICKET_WEB_DEMO_WORK_ROOT", "/tmp/customer-ticket-process-web-demo"))
MAX_BODY_BYTES = 64_000
MAX_WORKFLOW_STEPS = 16

# This table is the only place the web orchestrator knows how to execute a
# workflow step. The two LLM-based skills live under skills/ (with a SKILL.md
# the agent path reads); the seven deterministic steps live under
# automations/ with only a README.md.
STEP_SCRIPTS = {
    "receive-ticket": "automations/receive-ticket/scripts/receive_ticket.py",
    "classify-prioritize-ticket": "automations/classify-prioritize-ticket/scripts/classify_prioritize_ticket.py",
    "check-faq-resolution": "skills/check-faq-resolution/scripts/check_faq_resolution.py",
    "draft-faq-response": "automations/draft-faq-response/scripts/draft_faq_response.py",
    "escalate-to-specialist": "automations/escalate-to-specialist/scripts/escalate_to_specialist.py",
    "investigate-specialist-solution": (
        "skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py"
    ),
    "draft-specialist-response": "automations/draft-specialist-response/scripts/draft_specialist_response.py",
    "review-specialist-draft": "automations/review-specialist-draft/scripts/review_specialist_draft.py",
    "send-customer-response": "automations/send-customer-response/scripts/send_customer_response.py",
    "verify-feedback-close-or-reopen": "automations/verify-feedback-close-or-reopen/scripts/verify_feedback.py",
    "draft-faq-candidate": "skills/draft-faq-candidate/scripts/draft_faq_candidate.py",
    "approve-faq-promotion": "automations/approve-faq-promotion/scripts/approve_faq_promotion.py",
}


STEP_LABELS = {
    "receive-ticket": "Receive ticket",
    "classify-prioritize-ticket": "Classify + prioritize",
    "check-faq-resolution": "Check FAQ",
    "draft-faq-response": "Draft FAQ response",
    "escalate-to-specialist": "Escalate",
    "investigate-specialist-solution": "Investigate",
    "draft-specialist-response": "Draft specialist response",
    "review-specialist-draft": "Review specialist draft",
    "send-customer-response": "Send response",
    "verify-feedback-close-or-reopen": "Verify feedback",
    "draft-faq-candidate": "Draft FAQ candidate",
    "approve-faq-promotion": "Approve FAQ promotion",
}

TERMINAL_NEXT_ACTIONS = {"", "audit-ticket-process", "close_ticket", "close_unresolved_vendor_followup"}

# Steps that require human input before they can run. The orchestrator
# stops driving when one of these is the next action, and the browser
# surfaces a panel for the supervisor / customer to act on. Each entry
# matches a key in STEP_SCRIPTS, so the step *is* a runnable script —
# the orchestrator simply does not invoke it until the human has
# provided the necessary payload.
PAUSE_STEPS = {
    "verify-feedback-close-or-reopen",
    "review-specialist-draft",
    "approve-faq-promotion",
}

SYSTEM_OPTIONS = [
    "Customer Portal",
    "Identity Provider",
    "CRM",
    "Billing System",
    "Analytics Dashboard",
    "Inventory App",
    "VPN",
    "Email",
]
URGENCY_OPTIONS = ["low", "medium", "high", "critical"]
TIER_OPTIONS = ["standard", "premium", "enterprise"]
SLA_BY_TIER = {"standard": "basic", "premium": "business", "enterprise": "critical"}


class WorkflowError(RuntimeError):
    """Raised when a skill or web request cannot proceed."""

    def __init__(self, message: str, *, status: HTTPStatus = HTTPStatus.BAD_REQUEST, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class RunPaths:
    workflow_run_id: str
    ticket_id: str
    run_dir: Path
    data_dir: Path
    out_dir: Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_text(value: object, *, default: str = "", max_len: int = 4000) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return default
    return text[:max_len]


def _choice(value: object, allowed: list[str], default: str) -> str:
    text = _clean_text(value, default=default, max_len=80)
    return text if text in allowed else default


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 1), 100_000)


def _require_text(payload: dict, key: str, label: str, *, max_len: int = 4000) -> str:
    value = _clean_text(payload.get(key), max_len=max_len)
    if not value:
        raise WorkflowError(f"{label} is required.")
    return value


def normalize_submission(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise WorkflowError("Request body must be a JSON object.")

    subject = _require_text(payload, "subject", "Subject", max_len=160)
    description = _require_text(payload, "description", "Description", max_len=4000)
    requester_email = _require_text(payload, "requester_email", "Email", max_len=180)
    if "@" not in requester_email:
        raise WorkflowError("Email must contain @.")

    account_tier = _choice(payload.get("account_tier"), TIER_OPTIONS, "standard")
    requester_name = _clean_text(payload.get("requester_name"), default="Web Demo User", max_len=120)
    company = _clean_text(payload.get("company"), default="Web Demo Company", max_len=140)

    return {
        "requester_name": requester_name,
        "requester_email": requester_email,
        "company": company,
        "account_tier": account_tier,
        "sla_plan": SLA_BY_TIER[account_tier],
        "active_users": _positive_int(payload.get("active_users"), 25),
        "industry": _clean_text(payload.get("industry"), default="technology", max_len=80),
        "region": _clean_text(payload.get("region"), default="NA-West", max_len=80),
        "subject": subject,
        "description": description,
        "affected_system": _choice(payload.get("affected_system"), SYSTEM_OPTIONS, "Customer Portal"),
        "customer_reported_urgency": _choice(payload.get("customer_reported_urgency"), URGENCY_OPTIONS, "medium"),
        "business_impact_text": _clean_text(
            payload.get("business_impact_text"), default="Business impact not specified.", max_len=500
        ),
        "error_or_symptom_detail": _clean_text(
            payload.get("error_or_symptom_detail"), default=description, max_len=800
        ),
        "steps_already_tried": _clean_text(payload.get("steps_already_tried"), default="None reported.", max_len=500),
        "expected_outcome": _clean_text(
            payload.get("expected_outcome"),
            default="The affected service works as expected.",
            max_len=500,
        ),
        "availability_window": _clean_text(payload.get("availability_window"), default="Business hours", max_len=160),
    }


def build_customer_row(form: dict, customer_id: str) -> dict:
    return {
        "customer_id": customer_id,
        "customer_name": form["company"],
        "account_tier": form["account_tier"],
        "industry": form["industry"],
        "region": form["region"],
        "sla_plan": form["sla_plan"],
        "active_users": str(form["active_users"]),
        "relationship_start_date": date.today().isoformat(),
    }


def build_ticket_row(form: dict, ticket_id: str, customer_id: str) -> dict:
    return {
        "ticket_id": ticket_id,
        "submitted_at": utc_now_iso(),
        "customer_id": customer_id,
        "submitted_by_name": form["requester_name"],
        "submitted_by_email": form["requester_email"],
        "channel": "web",
        "subject": form["subject"],
        "description": form["description"],
        "affected_system": form["affected_system"],
        "customer_reported_urgency": form["customer_reported_urgency"],
        "business_impact_text": form["business_impact_text"],
        "attachment_flag": "false",
        "error_or_symptom_detail": form["error_or_symptom_detail"],
        "steps_already_tried": form["steps_already_tried"],
        "expected_outcome": form["expected_outcome"],
        "availability_window": form["availability_window"],
        "attachment_description": "",
    }


def append_csv_row(path: Path, row: dict) -> None:
    with path.open(newline="", encoding="utf-8") as f:
        fieldnames = next(csv.reader(f))
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writerow({field: row.get(field, "") for field in fieldnames})


def prepare_run_data(source_data_dir: Path, run_dir: Path, ticket_row: dict, customer_row: dict) -> RunPaths:
    workflow_run_id = run_dir.name
    data_dir = run_dir / "data"
    out_dir = run_dir / "working"
    out_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(source_data_dir / "raw", data_dir / "raw", dirs_exist_ok=True)
    shutil.copytree(source_data_dir / "dictionaries", data_dir / "dictionaries", dirs_exist_ok=True)
    append_csv_row(data_dir / "raw" / "customers.csv", customer_row)
    append_csv_row(data_dir / "raw" / "submitted_tickets.csv", ticket_row)

    metadata = {
        "workflow_run_id": workflow_run_id,
        "ticket_id": ticket_row["ticket_id"],
        "created_at": utc_now_iso(),
        "data_dir": str(data_dir),
        "out_dir": str(out_dir),
        "next_step": "receive-ticket",
        "last_step_number": 0,
        "terminal": False,
        "last_step": {},
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return RunPaths(workflow_run_id, ticket_row["ticket_id"], run_dir, data_dir, out_dir)


def create_web_ticket(payload: dict, *, work_root: Path, source_data_dir: Path = DATA_DIR) -> RunPaths:
    form = normalize_submission(payload)
    suffix = uuid.uuid4().hex[:8].upper()
    workflow_run_id = f"wf-web-{suffix.lower()}"
    ticket_id = f"WEB-{suffix}"
    customer_id = f"WEB-CUST-{suffix}"
    run_dir = work_root / workflow_run_id
    ticket_row = build_ticket_row(form, ticket_id, customer_id)
    customer_row = build_customer_row(form, customer_id)
    return prepare_run_data(source_data_dir, run_dir, ticket_row, customer_row)


def run_step(
    skill_name: str,
    paths: RunPaths,
    *,
    step_number: int,
    extra: list[str] | None = None,
    timeout: int = 45,
) -> dict:
    """Run one Python script stored inside a skill or automation folder.

    The orchestrator uses Python code here because orchestration is control
    flow: choose the next step, pass stable ids, enforce timeouts, and
    surface failures. The domain work lives in the scripts under ``skills/``
    (the two LLM-based steps) and ``automations/`` (the seven deterministic
    steps). ``SKILL.md`` is loaded by an LLM agent — not by this web server.
    """

    script = STEP_SCRIPTS[skill_name]
    step_id = f"{skill_name}-{step_number:02d}-{uuid.uuid4().hex[:6]}"
    cmd = [
        sys.executable,
        str(REPO_ROOT / script),
        "--ticket-id",
        paths.ticket_id,
        "--data-dir",
        str(paths.data_dir),
        "--out-dir",
        str(paths.out_dir),
        "--workflow-run-id",
        paths.workflow_run_id,
        "--step-id",
        step_id,
        "--json",
    ]
    if extra:
        cmd.extend(extra)

    # Skills are subprocesses instead of imported functions so their command
    # line contract is tested exactly the way a real agent or scheduler would
    # call them. The JSON envelope is the handoff back to the orchestrator.
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
    stdout = result.stdout.strip()
    if not stdout:
        raise WorkflowError(
            f"{skill_name} produced no output.",
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            details={"stderr": result.stderr[-1000:], "returncode": result.returncode},
        )
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowError(
            f"{skill_name} returned invalid JSON.",
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            details={"stdout": stdout[-1000:], "stderr": result.stderr[-1000:]},
        ) from exc

    if result.returncode not in {0, 2, 3} or envelope.get("status") == "error":
        message = envelope.get("error", {}).get("message") or f"{skill_name} failed."
        raise WorkflowError(
            message,
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            details={"skill": skill_name, "envelope": envelope, "stderr": result.stderr[-1000:]},
        )
    return envelope


def drive_until_pause(
    paths: RunPaths, *, start_skill: str = "receive-ticket", first_step_number: int = 1
) -> list[dict]:
    """Run skills until a step requires human input.

    Stops on any step in :data:`PAUSE_STEPS` (customer feedback, supervisor
    draft review, FAQ-promotion approval). The web demo's "Run All" button
    uses this path, as does the reopen continuation after negative
    feedback. Step mode uses the same transition rules one skill at a
    time through ``TicketWorkflowOrchestrator.step``.
    """

    envelopes: list[dict] = []
    skill = start_skill
    for step in range(first_step_number, first_step_number + MAX_WORKFLOW_STEPS):
        envelope = run_step(skill, paths, step_number=step)
        envelopes.append(envelope)
        next_action = envelope.get("next_action") or ""
        # If the next action requires human input, hand control back to the
        # browser instead of running the script blind.
        if next_action in PAUSE_STEPS:
            return envelopes
        if next_action not in STEP_SCRIPTS:
            raise WorkflowError(
                f"Workflow stopped before reaching a human-input gate. Next action was {next_action or '(none)'}."
            )
        skill = next_action
    raise WorkflowError("Workflow did not reach a human-input gate within the step limit.")


# Back-compat alias for any external caller. Internally everything now
# uses drive_until_pause.
drive_until_response = drive_until_pause


def metadata_path(paths: RunPaths) -> Path:
    return paths.run_dir / "metadata.json"


def load_run_metadata(paths: RunPaths) -> dict:
    path = metadata_path(paths)
    if not path.exists():
        return {
            "workflow_run_id": paths.workflow_run_id,
            "ticket_id": paths.ticket_id,
            "data_dir": str(paths.data_dir),
            "out_dir": str(paths.out_dir),
            "next_step": "receive-ticket",
            "last_step_number": 0,
            "terminal": False,
            "last_step": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_run_metadata(paths: RunPaths, metadata: dict) -> None:
    metadata_path(paths).write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def next_step_from_action(next_action: object) -> str:
    """Translate a skill envelope's next_action into a runnable skill name.

    Unknown actions intentionally do not run. That makes stalls visible:
    students can see when the workflow is waiting on a human, an external
    system, or an action that has not been implemented as a skill yet.
    """

    action = "" if next_action is None else str(next_action)
    if action in STEP_SCRIPTS:
        return action
    if action in TERMINAL_NEXT_ACTIONS:
        return ""
    return ""


def _latest_action_log_row(paths: RunPaths, skill_name: str) -> dict:
    rows = [
        row
        for row in _read_rows(paths.out_dir / "ticket_action_log.csv")
        if row.get("ticket_id") == paths.ticket_id
        and row.get("workflow_run_id") == paths.workflow_run_id
        and row.get("skill_name") == skill_name
    ]
    if not rows:
        return {}
    return sorted(rows, key=lambda row: row.get("created_at", ""))[-1]


def summarize_step(paths: RunPaths, envelope: dict, next_step: str) -> dict:
    skill = envelope.get("skill_name", "")
    row = _latest_action_log_row(paths, skill)
    next_label = STEP_LABELS.get(next_step, "Workflow complete" if not next_step else next_step)
    summary = row.get("decision_summary") or envelope.get("error", {}).get("message") or "Step completed."
    return {
        "skill": skill,
        "label": STEP_LABELS.get(skill, skill),
        "status": envelope.get("status", ""),
        "summary": summary,
        "nextStep": next_step,
        "nextStepLabel": next_label,
        "nextAction": envelope.get("next_action", ""),
        "reviewRequired": bool(envelope.get("review_required")),
    }


def read_metadata(work_root: Path, workflow_run_id: str) -> RunPaths:
    safe_id = _clean_text(workflow_run_id, max_len=120)
    if not re.fullmatch(r"wf-web-[a-f0-9]{8}", safe_id):
        raise WorkflowError("Unknown workflow run.")
    run_dir = work_root / safe_id
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.exists():
        raise WorkflowError("Workflow run was not found.", status=HTTPStatus.NOT_FOUND)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return RunPaths(
        metadata["workflow_run_id"],
        metadata["ticket_id"],
        run_dir,
        Path(metadata["data_dir"]),
        Path(metadata["out_dir"]),
    )


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def latest_row(out_dir: Path, table: str, ticket_id: str, workflow_run_id: str) -> dict:
    rows = [
        row
        for row in _read_rows(out_dir / f"{table}.csv")
        if row.get("ticket_id") == ticket_id and row.get("workflow_run_id") == workflow_run_id
    ]
    if not rows:
        return {}
    return sorted(rows, key=lambda row: row.get("created_at") or row.get("sent_at") or "")[-1]


def ticket_row(data_dir: Path, ticket_id: str) -> dict:
    for row in _read_rows(data_dir / "raw" / "submitted_tickets.csv"):
        if row.get("ticket_id") == ticket_id:
            return row
    return {}


def summarize_envelope(envelope: dict) -> dict:
    skill = envelope.get("skill_name", "")
    return {
        "skill": skill,
        "label": STEP_LABELS.get(skill, skill),
        "status": envelope.get("status", ""),
        "nextAction": envelope.get("next_action", ""),
        "reviewRequired": bool(envelope.get("review_required")),
    }


def action_log_steps(paths: RunPaths, envelopes: list[dict]) -> list[dict]:
    rows = [
        row
        for row in _read_rows(paths.out_dir / "ticket_action_log.csv")
        if row.get("ticket_id") == paths.ticket_id and row.get("workflow_run_id") == paths.workflow_run_id
    ]
    rows = sorted(rows, key=lambda row: row.get("created_at", ""))
    steps = [
        {
            "skill": row.get("skill_name", ""),
            "label": STEP_LABELS.get(row.get("skill_name", ""), row.get("skill_name", "")),
            "status": "completed",
            "createdAt": row.get("created_at", ""),
            "action": row.get("action", ""),
            "summary": row.get("decision_summary", ""),
            "reviewRequired": str(row.get("needs_human_review", "")).lower() == "true",
        }
        for row in rows
    ]
    if steps:
        return steps
    return [summarize_envelope(envelope) for envelope in envelopes]


def _truthy(value: object) -> bool:
    return str(value).lower() == "true"


def _event_time(row: dict, *keys: str) -> str:
    for key in keys:
        if row.get(key):
            return row[key]
    return ""


def _node(node_id: str, label: str, state: str, detail: str = "") -> dict:
    return {"id": node_id, "label": label, "state": state, "detail": detail}


def build_flow_nodes(
    *,
    received: bool,
    ticket: dict,
    triage: dict,
    faq: dict,
    escalation: dict,
    solution: dict,
    draft: dict,
    sent: dict,
    feedback: dict,
) -> list[dict]:
    triaged = bool(triage)
    faq_checked = bool(faq)
    faq_match = _truthy(faq.get("faq_match_found", ""))
    escalated = bool(escalation)
    investigated = bool(solution)
    drafted = bool(draft)
    sent_response = bool(sent)
    feedback_recorded = bool(feedback)
    response_source = draft.get("message_source", "")
    feedback_next = feedback.get("next_action", "")
    closed = feedback_next in {"close_ticket", "close_unresolved_vendor_followup"}
    sent_at = _event_time(sent, "sent_at", "created_at")
    feedback_at = _event_time(feedback, "created_at")
    awaiting_customer = sent_response and (not feedback_recorded or sent_at > feedback_at)

    current = ""
    if not received:
        current = "receive"
    elif not triaged:
        current = "triage"
    elif not faq_checked:
        current = "faq"
    elif faq_match and response_source != "faq":
        current = "faq_response"
    elif not faq_match and not escalated:
        current = "escalate"
    elif escalated and not investigated:
        current = "investigate"
    elif investigated and response_source != "specialist_solution":
        current = "specialist_response"
    elif drafted and not sent_response:
        current = "send"
    elif awaiting_customer:
        current = "feedback"
    elif feedback_next == "reopen_and_escalate":
        current = "escalate"
    elif closed:
        current = ""

    faq_response_state = "pending"
    if response_source == "faq":
        faq_response_state = "completed"
    elif faq_checked and not faq_match:
        faq_response_state = "skipped"
    elif current == "faq_response":
        faq_response_state = "current"

    specialist_branch_state = "pending"
    if faq_checked and faq_match and response_source != "specialist_solution":
        specialist_branch_state = "skipped"

    def state_for(node_id: str, done: bool, fallback: str = "pending") -> str:
        if current == node_id:
            return "current"
        if done:
            return "completed"
        return fallback

    close_detail = ""
    if feedback_next == "close_ticket":
        close_detail = "Customer accepted"
    elif feedback_next == "close_unresolved_vendor_followup":
        close_detail = "Closed unresolved"
    elif feedback_next == "reopen_and_escalate":
        close_detail = "Reopened for another pass"

    return [
        _node("submit", "Submit", "completed", ticket.get("subject", "")),
        _node("receive", "Receive", state_for("receive", received), "Intake summary recorded" if received else ""),
        _node(
            "triage",
            "Classify + priority",
            state_for("triage", triaged),
            " / ".join(filter(None, [triage.get("assigned_category", ""), triage.get("assigned_priority", "")])),
        ),
        _node(
            "faq",
            "FAQ check",
            state_for("faq", faq_checked),
            f"Matched {faq.get('faq_id')}" if faq_match else ("No FAQ match" if faq_checked else ""),
        ),
        _node("faq_response", "Draft FAQ response", faq_response_state, "Used knowledge base article"),
        _node(
            "escalate",
            "Escalate",
            state_for("escalate", escalated, specialist_branch_state),
            escalation.get("specialist_group", ""),
        ),
        _node(
            "investigate",
            "Specialist investigates",
            state_for("investigate", investigated, specialist_branch_state),
            solution.get("root_cause", ""),
        ),
        _node(
            "specialist_response",
            "Draft specialist response",
            state_for("specialist_response", response_source == "specialist_solution", specialist_branch_state),
            "Converted specialist solution for customer",
        ),
        _node("send", "Send", state_for("send", sent_response), sent.get("delivery_status", "")),
        _node(
            "feedback",
            "Customer feedback",
            state_for("feedback", feedback_recorded and not awaiting_customer),
            "Awaiting reply" if awaiting_customer else feedback.get("verification_notes", ""),
        ),
        _node("close", "Close / reopen", state_for("close", closed), close_detail),
    ]


def build_branch_cards(faq: dict, escalation: dict, solution: dict, draft: dict) -> list[dict]:
    faq_checked = bool(faq)
    faq_match = _truthy(faq.get("faq_match_found", ""))
    response_source = draft.get("message_source", "")
    specialist_started = bool(escalation or solution or response_source == "specialist_solution")

    if not faq_checked:
        faq_state = "pending"
        specialist_state = "pending"
        faq_reason = "Waiting for check-faq-resolution."
        specialist_reason = "Waiting for FAQ decision."
    elif faq_match:
        faq_state = "selected" if response_source != "faq" else "completed"
        specialist_state = "skipped" if not specialist_started else "completed"
        faq_reason = faq.get("faq_application_reason", "")
        specialist_reason = "Skipped because a usable FAQ match was found."
    else:
        faq_state = "skipped"
        specialist_state = "selected" if not specialist_started else "completed"
        faq_reason = faq.get("faq_application_reason", "No FAQ matched the ticket strongly enough.")
        specialist_reason = escalation.get("escalation_reason", "Selected because the FAQ branch could not resolve it.")

    return [
        {
            "id": "faq",
            "label": "FAQ branch",
            "state": faq_state,
            "steps": ["draft-faq-response", "send-customer-response"],
            "reason": faq_reason,
        },
        {
            "id": "specialist",
            "label": "Specialist branch",
            "state": specialist_state,
            "steps": [
                "escalate-to-specialist",
                "investigate-specialist-solution",
                "draft-specialist-response",
                "send-customer-response",
            ],
            "reason": specialist_reason,
        },
    ]


def _split_pipe(value: object) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def _field(label: str, value: object) -> dict:
    text = "" if value is None else str(value)
    return {"label": label, "value": text}


def _step_io_record(skill: str, inputs: list[dict], outputs: list[dict], artifacts: list[str] | None = None) -> dict:
    return {
        "skill": skill,
        "label": STEP_LABELS.get(skill, skill),
        "inputs": inputs,
        "outputs": outputs,
        "artifacts": artifacts or [],
    }


def build_step_io(paths: RunPaths) -> list[dict]:
    """Build the teaching view of what each completed skill read and wrote.

    These records are not used for routing. They are deliberately redundant
    with the working CSVs so students can inspect the workflow as data moving
    from one skill to the next.
    """

    ticket = ticket_row(paths.data_dir, paths.ticket_id)
    triage = latest_row(paths.out_dir, "triage_decisions", paths.ticket_id, paths.workflow_run_id)
    faq = latest_row(paths.out_dir, "faq_decisions", paths.ticket_id, paths.workflow_run_id)
    escalation = latest_row(paths.out_dir, "escalation_decisions", paths.ticket_id, paths.workflow_run_id)
    solution = latest_row(paths.out_dir, "specialist_solutions", paths.ticket_id, paths.workflow_run_id)
    draft = latest_row(paths.out_dir, "customer_response_drafts", paths.ticket_id, paths.workflow_run_id)
    sent = latest_row(paths.out_dir, "sent_messages", paths.ticket_id, paths.workflow_run_id)
    feedback = latest_row(paths.out_dir, "feedback_decisions", paths.ticket_id, paths.workflow_run_id)
    steps = {step["skill"] for step in action_log_steps(paths, [])}
    records: list[dict] = []

    if "receive-ticket" in steps:
        records.append(
            _step_io_record(
                "receive-ticket",
                [
                    _field("ticket_id", ticket.get("ticket_id", "")),
                    _field("subject", ticket.get("subject", "")),
                    _field("affected_system", ticket.get("affected_system", "")),
                    _field("urgency", ticket.get("customer_reported_urgency", "")),
                ],
                [
                    _field("summary", f"{ticket.get('subject', '')} / {ticket.get('business_impact_text', '')}"),
                    _field("next_action", "classify-prioritize-ticket"),
                ],
                ["working/ticket_action_log.csv"],
            )
        )

    if triage:
        records.append(
            _step_io_record(
                "classify-prioritize-ticket",
                [_field("inputs_used", ", ".join(_split_pipe(triage.get("inputs_used"))))],
                [
                    _field("category", triage.get("assigned_category", "")),
                    _field("priority", triage.get("assigned_priority", "")),
                    _field("specialist_group", triage.get("recommended_specialist_group", "")),
                    _field("evidence", triage.get("classification_evidence", "")),
                    _field("confidence", triage.get("confidence_score", "")),
                ],
                ["working/triage_decisions.csv"],
            )
        )

    if faq:
        records.append(
            _step_io_record(
                "check-faq-resolution",
                [
                    _field("triage_category", triage.get("assigned_category", "")),
                    _field("search_terms", faq.get("search_terms", "")),
                    _field("candidate_faq_ids", faq.get("candidate_faq_ids", "")),
                ],
                [
                    _field("faq_match_found", faq.get("faq_match_found", "")),
                    _field("faq_id", faq.get("faq_id", "")),
                    _field("match_confidence", faq.get("match_confidence", "")),
                    _field("recommended_next_step", faq.get("recommended_next_step", "")),
                    _field("reason", faq.get("faq_application_reason", "")),
                ],
                ["working/faq_decisions.csv"],
            )
        )

    if draft and draft.get("message_source") == "faq":
        records.append(
            _step_io_record(
                "draft-faq-response",
                [_field("faq_id", faq.get("faq_id", "")), _field("message_source", "faq")],
                [
                    _field("message_id", draft.get("message_id", "")),
                    _field("customer_action_required", draft.get("customer_action_required", "")),
                    _field("follow_up_request", draft.get("follow_up_request", "")),
                ],
                ["working/customer_response_drafts.csv"],
            )
        )

    if escalation:
        records.append(
            _step_io_record(
                "escalate-to-specialist",
                [
                    _field("category", triage.get("assigned_category", "")),
                    _field("faq_next_step", faq.get("recommended_next_step", "")),
                ],
                [
                    _field("specialist_id", escalation.get("specialist_id", "")),
                    _field("specialist_group", escalation.get("specialist_group", "")),
                    _field("reason", escalation.get("escalation_reason", "")),
                ],
                ["working/escalation_decisions.csv"],
            )
        )

    if solution:
        records.append(
            _step_io_record(
                "investigate-specialist-solution",
                [
                    _field("specialist_id", solution.get("specialist_id", "")),
                    _field("system", ticket.get("affected_system", "")),
                ],
                [
                    _field("root_cause", solution.get("root_cause", "")),
                    _field("solution_summary", solution.get("solution_summary", "")),
                    _field("customer_action_required", solution.get("customer_action_required", "")),
                ],
                ["working/specialist_solutions.csv"],
            )
        )

    if draft and draft.get("message_source") == "specialist_solution":
        records.append(
            _step_io_record(
                "draft-specialist-response",
                [_field("root_cause", solution.get("root_cause", "")), _field("message_source", "specialist_solution")],
                [
                    _field("message_id", draft.get("message_id", "")),
                    _field("customer_action_required", draft.get("customer_action_required", "")),
                    _field("follow_up_request", draft.get("follow_up_request", "")),
                ],
                ["working/customer_response_drafts.csv"],
            )
        )

    if sent:
        records.append(
            _step_io_record(
                "send-customer-response",
                [
                    _field("message_id", sent.get("message_id", "")),
                    _field("recipient", sent.get("recipient_email", "")),
                ],
                [
                    _field("delivery_id", sent.get("delivery_id", "")),
                    _field("delivery_status", sent.get("delivery_status", "")),
                    _field("channel", sent.get("channel", "")),
                ],
                ["working/sent_messages.csv"],
            )
        )

    if feedback:
        records.append(
            _step_io_record(
                "verify-feedback-close-or-reopen",
                [_field("customer_feedback_text", feedback.get("customer_feedback_text", ""))],
                [
                    _field("resolution_accepted", feedback.get("resolution_accepted", "")),
                    _field("verified_rejection", feedback.get("verified_rejection", "")),
                    _field("next_action", feedback.get("next_action", "")),
                    _field("verification_notes", feedback.get("verification_notes", "")),
                ],
                ["working/feedback_decisions.csv"],
            )
        )

    return records


def _text(value: str) -> dict:
    return {"text": value}


def _code(value: object) -> dict:
    return {"code": "" if value is None else str(value)}


def _sentence(parts: list[dict]) -> dict:
    return {"parts": parts}


def build_narrative(
    *,
    ticket: dict,
    triage: dict,
    faq: dict,
    escalation: dict,
    solution: dict,
    draft: dict,
    sent: dict,
    feedback: dict,
    metadata: dict,
    step_count: int,
) -> dict:
    """Return a human-readable narrative with inline dynamic values.

    The browser renders ``{"code": ...}`` parts as inline-code chips, similar
    to RMarkdown inline code. Keeping this as structured data makes the text
    easy to test and keeps the UI from hard-coding workflow facts.
    """

    next_step = metadata.get("next_step") or ""
    last_step = metadata.get("last_step") or {}
    sentences = [
        _sentence(
            [
                _text("The submitted ticket "),
                _code(ticket.get("ticket_id", "")),
                _text(" is about "),
                _code(ticket.get("subject", "")),
                _text(" in "),
                _code(ticket.get("affected_system", "")),
                _text("."),
            ]
        ),
        _sentence(
            [
                _text("The orchestrator has completed "),
                _code(step_count),
                _text(" skill step"),
                _text("" if step_count == 1 else "s"),
                _text("."),
            ]
        ),
    ]
    if last_step.get("skill"):
        sentences.append(
            _sentence(
                [
                    _text("Most recently, "),
                    _code(last_step.get("skill", "")),
                    _text(" did: "),
                    _code(last_step.get("summary", "")),
                    _text("."),
                ]
            )
        )
    if triage:
        sentences.append(
            _sentence(
                [
                    _text("Triage assigned category "),
                    _code(triage.get("assigned_category", "")),
                    _text(" with priority "),
                    _code(triage.get("assigned_priority", "")),
                    _text(" and specialist group "),
                    _code(triage.get("recommended_specialist_group", "")),
                    _text("."),
                ]
            )
        )
    if faq:
        faq_phrase = "matched FAQ" if _truthy(faq.get("faq_match_found")) else "did not find a usable FAQ"
        sentences.append(
            _sentence(
                [
                    _code("check-faq-resolution"),
                    _text(f" {faq_phrase} "),
                    _code(faq.get("faq_id", "none") or "none"),
                    _text(" and recommended "),
                    _code(faq.get("recommended_next_step", "")),
                    _text("."),
                ]
            )
        )
    if escalation:
        sentences.append(
            _sentence(
                [
                    _text("The specialist branch routed the ticket to "),
                    _code(escalation.get("specialist_id", "")),
                    _text(" in "),
                    _code(escalation.get("specialist_group", "")),
                    _text(" because "),
                    _code(escalation.get("escalation_reason", "")),
                    _text("."),
                ]
            )
        )
    if solution:
        sentences.append(
            _sentence(
                [
                    _code("investigate-specialist-solution"),
                    _text(" produced root cause "),
                    _code(solution.get("root_cause", "")),
                    _text("."),
                ]
            )
        )
    if draft:
        sentences.append(
            _sentence(
                [
                    _text("A customer response draft was created from "),
                    _code(draft.get("message_source", "")),
                    _text(" as message "),
                    _code(draft.get("message_id", "")),
                    _text("."),
                ]
            )
        )
    if sent:
        sentences.append(
            _sentence(
                [
                    _text("The response was sent to "),
                    _code(sent.get("recipient_email", "")),
                    _text(" with delivery id "),
                    _code(sent.get("delivery_id", "")),
                    _text("."),
                ]
            )
        )
    if feedback:
        sentences.append(
            _sentence(
                [
                    _text("Customer feedback led to next action "),
                    _code(feedback.get("next_action", "")),
                    _text("."),
                ]
            )
        )
    if next_step:
        sentences.append(
            _sentence(
                [
                    _text("Next, the orchestrator will run "),
                    _code(next_step),
                    _text("."),
                ]
            )
        )
    else:
        sentences.append(_sentence([_text("There is no next runnable skill for the current state.")]))
    return {"title": "Ticket narrative", "sentences": sentences}


def workflow_summary(paths: RunPaths, envelopes: list[dict]) -> dict:
    """Return the full state model consumed by the browser.

    This is the adapter between file-based skill artifacts and the web page:
    it reads the latest rows from the isolated working directory and shapes
    them into cards, flow nodes, narrative text, and skill I/O records.
    """

    ticket = ticket_row(paths.data_dir, paths.ticket_id)
    triage = latest_row(paths.out_dir, "triage_decisions", paths.ticket_id, paths.workflow_run_id)
    faq = latest_row(paths.out_dir, "faq_decisions", paths.ticket_id, paths.workflow_run_id)
    escalation = latest_row(paths.out_dir, "escalation_decisions", paths.ticket_id, paths.workflow_run_id)
    solution = latest_row(paths.out_dir, "specialist_solutions", paths.ticket_id, paths.workflow_run_id)
    draft = latest_row(paths.out_dir, "customer_response_drafts", paths.ticket_id, paths.workflow_run_id)
    sent = latest_row(paths.out_dir, "sent_messages", paths.ticket_id, paths.workflow_run_id)
    feedback = latest_row(paths.out_dir, "feedback_decisions", paths.ticket_id, paths.workflow_run_id)
    faq_candidate = latest_row(paths.out_dir, "faq_candidates", paths.ticket_id, paths.workflow_run_id)
    draft_review = latest_row(paths.out_dir, "specialist_draft_reviews", paths.ticket_id, paths.workflow_run_id)
    faq_promotion = latest_row(paths.out_dir, "faq_promotion_decisions", paths.ticket_id, paths.workflow_run_id)
    response_text = draft.get("sent_text") or draft.get("draft_text") or ""

    steps = action_log_steps(paths, envelopes)
    metadata = load_run_metadata(paths)
    received = any(step.get("skill") == "receive-ticket" for step in steps)
    next_step = metadata.get("next_step") or ""
    return {
        "workflowRunId": paths.workflow_run_id,
        "ticketId": paths.ticket_id,
        "ticket": {
            "subject": ticket.get("subject", ""),
            "affectedSystem": ticket.get("affected_system", ""),
            "urgency": ticket.get("customer_reported_urgency", ""),
            "requester": ticket.get("submitted_by_name", ""),
            "email": ticket.get("submitted_by_email", ""),
            "impact": ticket.get("business_impact_text", ""),
        },
        "triage": {
            "category": triage.get("assigned_category", ""),
            "priority": triage.get("assigned_priority", ""),
            "specialistGroup": triage.get("recommended_specialist_group", ""),
            "confidence": triage.get("confidence_score", ""),
        },
        "faq": {
            "matchFound": faq.get("faq_match_found", ""),
            "faqId": faq.get("faq_id", ""),
            "nextStep": faq.get("recommended_next_step", ""),
            "reason": faq.get("faq_application_reason", ""),
        },
        "escalation": {
            "specialistId": escalation.get("specialist_id", ""),
            "specialistGroup": escalation.get("specialist_group", ""),
            "reason": escalation.get("escalation_reason", ""),
        },
        "solution": {
            "rootCause": solution.get("root_cause", ""),
            "summary": solution.get("solution_summary", ""),
            "customerActionRequired": solution.get("customer_action_required", ""),
        },
        "response": {
            "messageId": draft.get("message_id", ""),
            "source": draft.get("message_source", ""),
            "text": response_text,
            "customerActionRequired": draft.get("customer_action_required", ""),
            "followUpRequest": draft.get("follow_up_request", ""),
            "deliveryId": sent.get("delivery_id", ""),
            "recipient": sent.get("recipient_email", ""),
            "status": sent.get("delivery_status", ""),
        },
        "feedback": {
            "accepted": feedback.get("resolution_accepted", ""),
            "nextAction": feedback.get("next_action", ""),
            "reopened": feedback.get("reopened_flag", ""),
            "notes": feedback.get("verification_notes", ""),
        },
        "specialistDraftReview": {
            "messageId": draft.get("message_id", "") if draft.get("message_source") == "specialist_solution" else "",
            "draftText": response_text if draft.get("message_source") == "specialist_solution" else "",
            "qualityCheckNotes": draft.get("quality_check_notes", ""),
            "lastDecision": draft_review.get("decision", ""),
            "retryCount": int(draft_review.get("retry_count") or 0)
            + (1 if draft_review.get("decision") == "reject" else 0),
            "forcedApprove": str(draft_review.get("forced_approve", "")).lower() == "true",
            "reviewerNotes": draft_review.get("reviewer_notes", ""),
        },
        "faqCandidate": {
            "category": faq_candidate.get("category", ""),
            "systemName": faq_candidate.get("system_name", ""),
            "issuePattern": faq_candidate.get("issue_pattern", ""),
            "symptoms": faq_candidate.get("symptoms", ""),
            "solutionSteps": faq_candidate.get("solution_steps", ""),
            "requiredCustomerInfo": faq_candidate.get("required_customer_info", ""),
            "confidence": faq_candidate.get("confidence", ""),
            "reasoning": faq_candidate.get("reasoning", ""),
            "decision": faq_promotion.get("decision", ""),
            "newFaqId": faq_promotion.get("new_faq_id", ""),
        },
        "flow": {
            "nodes": build_flow_nodes(
                received=received,
                ticket=ticket,
                triage=triage,
                faq=faq,
                escalation=escalation,
                solution=solution,
                draft=draft,
                sent=sent,
                feedback=feedback,
            ),
            "branches": build_branch_cards(faq, escalation, solution, draft),
        },
        "orchestrator": {
            "name": "TicketWorkflowOrchestrator",
            "nextStep": next_step,
            "nextStepLabel": STEP_LABELS.get(next_step, "Workflow complete" if not next_step else next_step),
            "stepNumber": int(metadata.get("last_step_number") or 0),
            "terminal": bool(metadata.get("terminal")),
            "lastStep": metadata.get("last_step") or {},
        },
        "narrative": build_narrative(
            ticket=ticket,
            triage=triage,
            faq=faq,
            escalation=escalation,
            solution=solution,
            draft=draft,
            sent=sent,
            feedback=feedback,
            metadata=metadata,
            step_count=len(steps),
        ),
        "stepIO": build_step_io(paths),
        "steps": steps,
    }


class TicketWorkflowOrchestrator:
    """Small teaching orchestrator that routes tickets through skill scripts.

    The orchestrator is code, not a skill, because it is infrastructure:
    it creates isolated run folders, calls skills, stores the next runnable
    skill, and stops when the workflow needs outside input. It does not
    decide categories, FAQ matches, specialist assignments, draft text, or
    feedback outcomes. Those decisions remain inside the individual skills.

    Each demo run persists ``next_step`` in ``metadata.json`` so the browser
    can execute one skill at a time, like stepping through a debugger. Every
    transition comes from the previous skill's JSON envelope ``next_action``.
    """

    def __init__(self, *, work_root: Path = DEFAULT_WORK_ROOT, source_data_dir: Path = DATA_DIR):
        self.work_root = Path(work_root)
        self.source_data_dir = Path(source_data_dir)

    def start_ticket(self, payload: dict) -> dict:
        """Create a run and stop before the first skill.

        Used by step mode. The first browser click after this will run
        ``receive-ticket``.
        """

        paths = create_web_ticket(payload, work_root=self.work_root, source_data_dir=self.source_data_dir)
        return workflow_summary(paths, [])

    def run_until_response(self, payload: dict) -> dict:
        """Create a run and execute skills until the customer must respond."""

        paths = create_web_ticket(payload, work_root=self.work_root, source_data_dir=self.source_data_dir)
        envelopes = drive_until_response(paths)
        self._record_envelopes(paths, envelopes)
        return workflow_summary(paths, envelopes)

    def step(self, workflow_run_id: str, *, feedback_text: str = "") -> dict:
        """Run exactly one next skill for an existing demo run.

        Refuses to run when the next step is a human-input gate
        (:data:`PAUSE_STEPS`); those have dedicated endpoints that supply
        the required decision payload.
        """

        paths = read_metadata(self.work_root, workflow_run_id)
        metadata = load_run_metadata(paths)
        next_step = metadata.get("next_step") or ""
        if not next_step:
            return workflow_summary(paths, [])
        if next_step in PAUSE_STEPS:
            if next_step == "verify-feedback-close-or-reopen":
                if not feedback_text.strip():
                    raise WorkflowError("Customer feedback is required before running verify-feedback-close-or-reopen.")
            else:
                raise WorkflowError(
                    f"Step {next_step} requires a supervisor decision. Use the matching review panel.",
                )

        step_number = int(metadata.get("last_step_number") or 0) + 1
        extra = ["--feedback-text", feedback_text] if next_step == "verify-feedback-close-or-reopen" else None
        envelope = run_step(next_step, paths, step_number=step_number, extra=extra)
        self._record_envelopes(paths, [envelope], step_number=step_number)
        return workflow_summary(paths, [envelope])

    def process_feedback(self, workflow_run_id: str, feedback_text: str) -> dict:
        """Apply customer feedback, then continue to the next pause point."""

        paths = read_metadata(self.work_root, workflow_run_id)
        metadata = load_run_metadata(paths)
        step_number = int(metadata.get("last_step_number") or 0) + 1
        envelopes = [
            run_step(
                "verify-feedback-close-or-reopen",
                paths,
                step_number=step_number,
                extra=["--feedback-text", feedback_text],
            )
        ]
        self._record_envelopes(paths, envelopes, step_number=step_number)

        next_step = next_step_from_action(envelopes[-1].get("next_action"))
        if next_step in {"escalate-to-specialist", "draft-faq-candidate"}:
            follow_on = drive_until_pause(paths, start_skill=next_step, first_step_number=step_number + 1)
            envelopes.extend(follow_on)
            self._record_envelopes(paths, follow_on, step_number=step_number + len(follow_on))
        return workflow_summary(paths, envelopes)

    def review_specialist_draft(
        self,
        workflow_run_id: str,
        *,
        decision: str,
        edited_text: str = "",
        reviewer_notes: str = "",
    ) -> dict:
        """Apply a supervisor decision on the specialist-draft review gate.

        Approve → continue to send-customer-response. Reject → continue
        through investigate-specialist-solution and draft-specialist-response
        back to the review gate (bounded to one retry; the script
        force-approves on the second reject).
        """

        if decision not in {"approve", "reject"}:
            raise WorkflowError("decision must be 'approve' or 'reject'.")

        paths = read_metadata(self.work_root, workflow_run_id)
        metadata = load_run_metadata(paths)
        if metadata.get("next_step") != "review-specialist-draft":
            raise WorkflowError("This workflow is not waiting on a specialist-draft review.")

        step_number = int(metadata.get("last_step_number") or 0) + 1
        extra = [
            "--decision",
            decision,
            "--edited-text",
            edited_text,
            "--reviewer-notes",
            reviewer_notes,
        ]
        envelope = run_step("review-specialist-draft", paths, step_number=step_number, extra=extra)
        envelopes = [envelope]
        envelopes.extend(self._drive_past_autonomous_steps(paths, envelope, step_number))
        self._record_envelopes(paths, envelopes, step_number=step_number + len(envelopes) - 1)
        return workflow_summary(paths, envelopes)

    def approve_faq_promotion(
        self,
        workflow_run_id: str,
        *,
        decision: str,
        candidate_json: str = "",
        reviewer_notes: str = "",
    ) -> dict:
        """Apply a supervisor decision on the FAQ-promotion gate."""

        if decision not in {"approve", "skip"}:
            raise WorkflowError("decision must be 'approve' or 'skip'.")

        paths = read_metadata(self.work_root, workflow_run_id)
        metadata = load_run_metadata(paths)
        if metadata.get("next_step") != "approve-faq-promotion":
            raise WorkflowError("This workflow is not waiting on a FAQ-promotion approval.")

        step_number = int(metadata.get("last_step_number") or 0) + 1
        extra = [
            "--decision",
            decision,
            "--candidate-json",
            candidate_json,
            "--reviewer-notes",
            reviewer_notes,
        ]
        envelope = run_step("approve-faq-promotion", paths, step_number=step_number, extra=extra)
        envelopes = [envelope]
        self._record_envelopes(paths, envelopes, step_number=step_number)
        return workflow_summary(paths, envelopes)

    def _drive_past_autonomous_steps(self, paths: RunPaths, last: dict, step_number: int) -> list[dict]:
        """Continue running steps until the next pause point or terminal state.

        Used after a step that may route into another autonomous step
        (e.g. review-specialist-draft → send-customer-response →
        verify-feedback-close-or-reopen). Returns the additional
        envelopes that ran. Empty if the next action is already a pause
        point or a terminal action.
        """

        next_step = next_step_from_action(last.get("next_action"))
        if not next_step or next_step in PAUSE_STEPS:
            return []
        return drive_until_pause(paths, start_skill=next_step, first_step_number=step_number + 1)

    def _record_envelopes(
        self,
        paths: RunPaths,
        envelopes: list[dict],
        *,
        step_number: int | None = None,
    ) -> None:
        if not envelopes:
            return
        metadata = load_run_metadata(paths)
        if step_number is None:
            step_number = int(metadata.get("last_step_number") or 0) + len(envelopes)
        last = envelopes[-1]
        next_step = next_step_from_action(last.get("next_action"))
        # This is the debugger checkpoint. The browser reads these fields to
        # show "what just happened" and "which skill will run next".
        metadata.update(
            {
                "last_step_number": step_number,
                "next_step": next_step,
                "terminal": not bool(next_step),
                "last_step": summarize_step(paths, last, next_step),
                "updated_at": utc_now_iso(),
            }
        )
        save_run_metadata(paths, metadata)


def process_submission(payload: dict, *, work_root: Path = DEFAULT_WORK_ROOT) -> dict:
    return TicketWorkflowOrchestrator(work_root=work_root).run_until_response(payload)


def start_submission(payload: dict, *, work_root: Path = DEFAULT_WORK_ROOT) -> dict:
    return TicketWorkflowOrchestrator(work_root=work_root).start_ticket(payload)


def process_step(payload: dict, *, work_root: Path = DEFAULT_WORK_ROOT) -> dict:
    workflow_run_id = _require_text(payload, "workflow_run_id", "Workflow run id", max_len=120)
    feedback_text = _clean_text(payload.get("feedback_text"), max_len=1000)
    return TicketWorkflowOrchestrator(work_root=work_root).step(workflow_run_id, feedback_text=feedback_text)


def process_feedback(payload: dict, *, work_root: Path = DEFAULT_WORK_ROOT) -> dict:
    workflow_run_id = _require_text(payload, "workflow_run_id", "Workflow run id", max_len=120)
    feedback_text = _require_text(payload, "feedback_text", "Feedback", max_len=1000)
    return TicketWorkflowOrchestrator(work_root=work_root).process_feedback(workflow_run_id, feedback_text)


def process_specialist_draft_review(payload: dict, *, work_root: Path = DEFAULT_WORK_ROOT) -> dict:
    workflow_run_id = _require_text(payload, "workflow_run_id", "Workflow run id", max_len=120)
    decision = _clean_text(payload.get("decision"), max_len=20)
    edited_text = _clean_text(payload.get("edited_text"), max_len=4000)
    reviewer_notes = _clean_text(payload.get("reviewer_notes"), max_len=1000)
    return TicketWorkflowOrchestrator(work_root=work_root).review_specialist_draft(
        workflow_run_id,
        decision=decision,
        edited_text=edited_text,
        reviewer_notes=reviewer_notes,
    )


def process_faq_promotion(payload: dict, *, work_root: Path = DEFAULT_WORK_ROOT) -> dict:
    workflow_run_id = _require_text(payload, "workflow_run_id", "Workflow run id", max_len=120)
    decision = _clean_text(payload.get("decision"), max_len=20)
    reviewer_notes = _clean_text(payload.get("reviewer_notes"), max_len=1000)
    candidate = payload.get("candidate")
    if candidate is None:
        candidate_json = ""
    elif isinstance(candidate, dict):
        candidate_json = json.dumps(candidate)
    elif isinstance(candidate, str):
        candidate_json = candidate
    else:
        raise WorkflowError("candidate must be a JSON object or string.")
    if len(candidate_json) > 8000:
        raise WorkflowError("candidate payload is too large.")
    return TicketWorkflowOrchestrator(work_root=work_root).approve_faq_promotion(
        workflow_run_id,
        decision=decision,
        candidate_json=candidate_json,
        reviewer_notes=reviewer_notes,
    )


def _json_response(handler: BaseHTTPRequestHandler, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status.value)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, body: str) -> None:
    encoded = body.encode("utf-8")
    handler.send_response(HTTPStatus.OK.value)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    content_length = int(handler.headers.get("Content-Length") or "0")
    if content_length > MAX_BODY_BYTES:
        raise WorkflowError("Request body is too large.", status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    raw = handler.rfile.read(content_length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowError("Request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise WorkflowError("Request body must be a JSON object.")
    return payload


def make_handler(work_root: Path) -> type[BaseHTTPRequestHandler]:
    class TicketDemoHandler(BaseHTTPRequestHandler):
        server_version = "TicketWorkflowDemo/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                _html_response(self, render_index())
                return
            if path == "/health":
                _json_response(self, {"status": "ok"})
                return
            if path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT.value)
                self.end_headers()
                return
            self.send_error(HTTPStatus.NOT_FOUND.value, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = _read_json(self)
                if path == "/api/tickets":
                    _json_response(self, process_submission(payload, work_root=work_root))
                    return
                if path == "/api/tickets/start":
                    _json_response(self, start_submission(payload, work_root=work_root))
                    return
                if path == "/api/step":
                    _json_response(self, process_step(payload, work_root=work_root))
                    return
                if path == "/api/feedback":
                    _json_response(self, process_feedback(payload, work_root=work_root))
                    return
                if path == "/api/specialist-draft-review":
                    _json_response(self, process_specialist_draft_review(payload, work_root=work_root))
                    return
                if path == "/api/faq-promotion":
                    _json_response(self, process_faq_promotion(payload, work_root=work_root))
                    return
                self.send_error(HTTPStatus.NOT_FOUND.value, "Not found")
            except WorkflowError as exc:
                _json_response(self, {"error": str(exc), "details": exc.details}, status=exc.status)
            except Exception as exc:  # pragma: no cover - defensive boundary for local demo server
                _json_response(
                    self,
                    {"error": "Unexpected server error.", "details": {"message": str(exc)}},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

    return TicketDemoHandler


def render_index() -> str:
    systems = "\n".join(
        f'<option value="{html.escape(system)}">{html.escape(system)}</option>' for system in SYSTEM_OPTIONS
    )
    urgencies = "\n".join(f'<option value="{urgency}">{urgency.title()}</option>' for urgency in URGENCY_OPTIONS)
    tiers = "\n".join(f'<option value="{tier}">{tier.title()}</option>' for tier in TIER_OPTIONS)
    examples = browser_example_tickets()
    example_options = "\n".join(
        f'<option value="{html.escape(example["id"])}">{html.escape(example["label"])}</option>' for example in examples
    )
    examples_json = json.dumps(examples).replace("</", "<\\/")
    template_path = Path(__file__).with_name("orchestrator_ui.html")
    template = string.Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        systems=systems,
        urgencies=urgencies,
        tiers=tiers,
        example_options=example_options,
        examples_json=examples_json,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local customer-ticket workflow web demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    args = parser.parse_args(argv)

    args.work_root.mkdir(parents=True, exist_ok=True)
    handler = make_handler(args.work_root.resolve())
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Ticket workflow demo running at http://{args.host}:{args.port}")
    print(f"Demo run files: {args.work_root.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ticket workflow demo.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
