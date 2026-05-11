"""Local web demo for the customer ticket workflow.

Run from the repo root:

    uv run python scripts/ticket_web_demo.py

The server provides a small browser UI for submitting a ticket, then
drives the Python scripts stored inside each skill folder until a
customer response has been drafted and recorded as sent. The browser
demo is not an LLM skill runtime: it does not load SKILL.md files at
request time. Demo runs use an isolated copy of the baseline data under
/tmp by default; data/raw is never modified.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
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

from scripts.ticket_scenarios import browser_example_tickets  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
DEFAULT_WORK_ROOT = Path(os.environ.get("TICKET_WEB_DEMO_WORK_ROOT", "/tmp/customer-ticket-process-web-demo"))
MAX_BODY_BYTES = 64_000
MAX_WORKFLOW_STEPS = 16

# This table is the only place the web orchestrator knows how to execute a
# workflow step. Each entry points at the Python script stored inside a skill
# folder. SKILL.md is still meaningful for an LLM agent, but the browser demo
# is not an LLM agent and does not load SKILL.md at runtime.
SKILL_SCRIPTS = {
    "receive-ticket": "skills/receive-ticket/scripts/receive_ticket.py",
    "classify-prioritize-ticket": "skills/classify-prioritize-ticket/scripts/classify_prioritize_ticket.py",
    "check-faq-resolution": "skills/check-faq-resolution/scripts/check_faq_resolution.py",
    "draft-faq-response": "skills/draft-faq-response/scripts/draft_faq_response.py",
    "escalate-to-specialist": "skills/escalate-to-specialist/scripts/escalate_to_specialist.py",
    "investigate-specialist-solution": (
        "skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py"
    ),
    "draft-specialist-response": "skills/draft-specialist-response/scripts/draft_specialist_response.py",
    "send-customer-response": "skills/send-customer-response/scripts/send_customer_response.py",
    "verify-feedback-close-or-reopen": "skills/verify-feedback-close-or-reopen/scripts/verify_feedback.py",
}

SKILL_LABELS = {
    "receive-ticket": "Receive ticket",
    "classify-prioritize-ticket": "Classify + prioritize",
    "check-faq-resolution": "Check FAQ",
    "draft-faq-response": "Draft FAQ response",
    "escalate-to-specialist": "Escalate",
    "investigate-specialist-solution": "Investigate",
    "draft-specialist-response": "Draft specialist response",
    "send-customer-response": "Send response",
    "verify-feedback-close-or-reopen": "Verify feedback",
}

TERMINAL_NEXT_ACTIONS = {"", "audit-ticket-process", "close_ticket", "close_unresolved_vendor_followup"}

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
        "next_skill": "receive-ticket",
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


def run_skill(
    skill_name: str,
    paths: RunPaths,
    *,
    step_number: int,
    extra: list[str] | None = None,
    timeout: int = 45,
) -> dict:
    """Run one Python script stored inside a skill folder.

    The orchestrator uses Python code here because orchestration is control
    flow: choose the next skill, pass stable ids, enforce timeouts, and surface
    failures. The domain work still lives in the scripts under ``skills/``.
    ``SKILL.md`` is loaded by an LLM agent, not by this web server.
    """

    script = SKILL_SCRIPTS[skill_name]
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


def drive_until_response(
    paths: RunPaths, *, start_skill: str = "receive-ticket", first_step_number: int = 1
) -> list[dict]:
    """Run skills until a response has been sent and customer feedback is next.

    The web demo's "Run All" button uses this path. Step mode uses the same
    transition rules one skill at a time through ``TicketWorkflowOrchestrator.step``.
    """

    envelopes: list[dict] = []
    skill = start_skill
    for step in range(first_step_number, first_step_number + MAX_WORKFLOW_STEPS):
        envelope = run_skill(skill, paths, step_number=step)
        envelopes.append(envelope)
        next_action = envelope.get("next_action") or ""
        # A sent response means the machine workflow must pause. The next
        # event is external customer feedback, so the browser asks the user for
        # it instead of pretending the AI knows whether the customer is happy.
        if next_action == "verify-feedback-close-or-reopen":
            return envelopes
        if next_action not in SKILL_SCRIPTS:
            raise WorkflowError(
                f"Workflow stopped before a customer response. Next action was {next_action or '(none)'}."
            )
        skill = next_action
    raise WorkflowError("Workflow did not reach a customer response within the step limit.")


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
            "next_skill": "receive-ticket",
            "last_step_number": 0,
            "terminal": False,
            "last_step": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_run_metadata(paths: RunPaths, metadata: dict) -> None:
    metadata_path(paths).write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def next_skill_from_action(next_action: object) -> str:
    """Translate a skill envelope's next_action into a runnable skill name.

    Unknown actions intentionally do not run. That makes stalls visible:
    students can see when the workflow is waiting on a human, an external
    system, or an action that has not been implemented as a skill yet.
    """

    action = "" if next_action is None else str(next_action)
    if action in SKILL_SCRIPTS:
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


def summarize_skill_step(paths: RunPaths, envelope: dict, next_skill: str) -> dict:
    skill = envelope.get("skill_name", "")
    row = _latest_action_log_row(paths, skill)
    next_label = SKILL_LABELS.get(next_skill, "Workflow complete" if not next_skill else next_skill)
    summary = row.get("decision_summary") or envelope.get("error", {}).get("message") or "Step completed."
    return {
        "skill": skill,
        "label": SKILL_LABELS.get(skill, skill),
        "status": envelope.get("status", ""),
        "summary": summary,
        "nextSkill": next_skill,
        "nextSkillLabel": next_label,
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
        "label": SKILL_LABELS.get(skill, skill),
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
            "label": SKILL_LABELS.get(row.get("skill_name", ""), row.get("skill_name", "")),
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


def _skill_io_record(skill: str, inputs: list[dict], outputs: list[dict], artifacts: list[str] | None = None) -> dict:
    return {
        "skill": skill,
        "label": SKILL_LABELS.get(skill, skill),
        "inputs": inputs,
        "outputs": outputs,
        "artifacts": artifacts or [],
    }


def build_skill_io(paths: RunPaths) -> list[dict]:
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
            _skill_io_record(
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
            _skill_io_record(
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
            _skill_io_record(
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
            _skill_io_record(
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
            _skill_io_record(
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
            _skill_io_record(
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
            _skill_io_record(
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
            _skill_io_record(
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
            _skill_io_record(
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

    next_skill = metadata.get("next_skill") or ""
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
    if next_skill:
        sentences.append(
            _sentence(
                [
                    _text("Next, the orchestrator will run "),
                    _code(next_skill),
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
    response_text = draft.get("sent_text") or draft.get("draft_text") or ""

    steps = action_log_steps(paths, envelopes)
    metadata = load_run_metadata(paths)
    received = any(step.get("skill") == "receive-ticket" for step in steps)
    next_skill = metadata.get("next_skill") or ""
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
            "nextSkill": next_skill,
            "nextSkillLabel": SKILL_LABELS.get(next_skill, "Workflow complete" if not next_skill else next_skill),
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
        "skillIO": build_skill_io(paths),
        "steps": steps,
    }


class TicketWorkflowOrchestrator:
    """Small teaching orchestrator that routes tickets through skill scripts.

    The orchestrator is code, not a skill, because it is infrastructure:
    it creates isolated run folders, calls skills, stores the next runnable
    skill, and stops when the workflow needs outside input. It does not
    decide categories, FAQ matches, specialist assignments, draft text, or
    feedback outcomes. Those decisions remain inside the individual skills.

    Each demo run persists ``next_skill`` in ``metadata.json`` so the browser
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
        """Run exactly one next skill for an existing demo run."""

        paths = read_metadata(self.work_root, workflow_run_id)
        metadata = load_run_metadata(paths)
        next_skill = metadata.get("next_skill") or ""
        if not next_skill:
            return workflow_summary(paths, [])
        if next_skill == "verify-feedback-close-or-reopen" and not feedback_text.strip():
            raise WorkflowError("Customer feedback is required before running verify-feedback-close-or-reopen.")

        step_number = int(metadata.get("last_step_number") or 0) + 1
        extra = ["--feedback-text", feedback_text] if next_skill == "verify-feedback-close-or-reopen" else None
        envelope = run_skill(next_skill, paths, step_number=step_number, extra=extra)
        self._record_envelopes(paths, [envelope], step_number=step_number)
        return workflow_summary(paths, [envelope])

    def process_feedback(self, workflow_run_id: str, feedback_text: str) -> dict:
        """Apply customer feedback, then continue only if a reopen path is needed."""

        paths = read_metadata(self.work_root, workflow_run_id)
        metadata = load_run_metadata(paths)
        step_number = int(metadata.get("last_step_number") or 0) + 1
        envelopes = [
            run_skill(
                "verify-feedback-close-or-reopen",
                paths,
                step_number=step_number,
                extra=["--feedback-text", feedback_text],
            )
        ]
        self._record_envelopes(paths, envelopes, step_number=step_number)

        next_skill = next_skill_from_action(envelopes[-1].get("next_action"))
        if next_skill == "escalate-to-specialist":
            follow_on = drive_until_response(paths, start_skill=next_skill, first_step_number=step_number + 1)
            envelopes.extend(follow_on)
            self._record_envelopes(paths, follow_on, step_number=step_number + len(follow_on))
        return workflow_summary(paths, envelopes)

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
        next_skill = next_skill_from_action(last.get("next_action"))
        # This is the debugger checkpoint. The browser reads these fields to
        # show "what just happened" and "which skill will run next".
        metadata.update(
            {
                "last_step_number": step_number,
                "next_skill": next_skill,
                "terminal": not bool(next_skill),
                "last_step": summarize_skill_step(paths, last, next_skill),
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
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ticket Workflow Demo</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f4;
      --surface: #ffffff;
      --surface-2: #eef4f0;
      --text: #17201b;
      --muted: #5b665f;
      --line: #d8dfd8;
      --accent: #0f766e;
      --accent-dark: #0b514c;
      --warn: #a85400;
      --error: #a23a3a;
      --shadow: 0 12px 28px rgba(23, 32, 27, 0.09);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }}
    .topbar {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 20px;
      letter-spacing: 0;
    }}
    .status-pill {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      color: var(--muted);
      background: var(--surface-2);
      font-size: 13px;
      white-space: nowrap;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 22px 20px 40px;
      display: grid;
      grid-template-columns: minmax(340px, 460px) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }}
    section {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .panel-header {{
      padding: 16px 16px 12px;
      border-bottom: 1px solid var(--line);
    }}
    .panel-header h2 {{
      margin: 0;
      font-size: 16px;
      letter-spacing: 0;
    }}
    form, .results-body {{
      padding: 16px;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 650;
      margin: 0 0 6px;
    }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid #c9d2ca;
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
      font-size: 14px;
      padding: 9px 10px;
      min-height: 38px;
    }}
    textarea {{
      min-height: 92px;
      resize: vertical;
      line-height: 1.4;
    }}
    .grid-2 {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .field {{
      margin-bottom: 12px;
    }}
    button {{
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 700;
      padding: 10px 13px;
      min-height: 40px;
      cursor: pointer;
    }}
    button:hover {{ background: var(--accent-dark); }}
    button:disabled {{
      cursor: not-allowed;
      opacity: 0.6;
    }}
    .secondary {{
      background: #2f5d50;
    }}
    .secondary:hover {{
      background: #25483f;
    }}
    .danger {{
      background: var(--warn);
    }}
    .danger:hover {{
      background: #7d3e00;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}
    .muted {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--surface-2);
      min-width: 0;
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .metric strong {{
      display: block;
      font-size: 14px;
      overflow-wrap: anywhere;
    }}
    .response {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfb;
      padding: 14px;
      white-space: pre-wrap;
      line-height: 1.45;
      font-size: 14px;
      min-height: 180px;
    }}
    .flow-chart {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfb;
      padding: 12px;
      margin-bottom: 14px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
      gap: 10px;
    }}
    .flow-node {{
      border: 1px solid #cbd5ce;
      border-radius: 8px;
      padding: 10px;
      min-height: 82px;
      background: #fff;
      position: relative;
    }}
    .flow-node::after {{
      content: "";
      position: absolute;
      top: 50%;
      right: -10px;
      width: 10px;
      border-top: 2px solid #cbd5ce;
    }}
    .flow-node:last-child::after {{
      display: none;
    }}
    .flow-node strong {{
      display: block;
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .flow-node span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }}
    .flow-node.completed {{
      border-color: #5aa16b;
      background: #edf8ef;
    }}
    .flow-node.current {{
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(15, 118, 110, 0.18);
      background: #eefbf8;
    }}
    .flow-node.pending {{
      color: #66716b;
      background: #f7f8f6;
    }}
    .flow-node.skipped {{
      color: #7b827d;
      background: #f2f0ec;
      border-style: dashed;
    }}
    .flow-state {{
      margin-top: 8px;
      display: inline-block;
      border-radius: 999px;
      padding: 3px 7px;
      background: rgba(23, 32, 27, 0.08);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    .section-label {{
      margin: 14px 0 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    .debug-panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      padding: 12px;
      margin-bottom: 14px;
      display: grid;
      gap: 10px;
    }}
    .debug-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }}
    .debug-row span {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }}
    .debug-row strong {{
      font-size: 14px;
      text-align: right;
    }}
    .step-summary {{
      border-left: 3px solid var(--accent);
      padding-left: 10px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }}
    .branch-map {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .branch-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
      min-width: 0;
    }}
    .branch-card strong {{
      display: block;
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .branch-card span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}
    .branch-card.selected {{
      border-color: var(--accent);
      background: #eefbf8;
    }}
    .branch-card.completed {{
      border-color: #5aa16b;
      background: #edf8ef;
    }}
    .branch-card.skipped {{
      border-style: dashed;
      background: #f2f0ec;
    }}
    .branch-steps {{
      margin-top: 8px;
      font-size: 11px;
      color: var(--muted);
    }}
    .narrative {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px 14px;
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
      line-height: 1.45;
      font-size: 14px;
    }}
    .narrative p {{
      margin: 0;
    }}
    .inline-code {{
      display: inline;
      border: 1px solid #cdd8d2;
      border-radius: 5px;
      background: #eef4f0;
      color: #123b35;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.92em;
      padding: 1px 5px;
      overflow-wrap: anywhere;
    }}
    .skill-io {{
      display: grid;
      gap: 10px;
      margin-bottom: 14px;
    }}
    .io-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
    }}
    .io-card h3 {{
      margin: 0 0 8px;
      font-size: 14px;
      letter-spacing: 0;
    }}
    .io-columns {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }}
    .io-list {{
      display: grid;
      gap: 6px;
      align-content: start;
    }}
    .io-list strong {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
    }}
    .io-field {{
      border: 1px solid #e1e6e1;
      border-radius: 6px;
      padding: 7px;
      background: #fbfcfb;
      min-width: 0;
    }}
    .io-field span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      margin-bottom: 3px;
    }}
    .io-field code {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }}
    .timeline {{
      margin: 14px 0 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 8px;
    }}
    .timeline li {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px 10px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      font-size: 13px;
      background: #fff;
    }}
    .timeline .next {{
      color: var(--muted);
      text-align: right;
      overflow-wrap: anywhere;
    }}
    .feedback {{
      margin-top: 14px;
      border-top: 1px solid var(--line);
      padding-top: 14px;
    }}
    .feedback textarea {{
      min-height: 70px;
      margin-bottom: 10px;
    }}
    .error {{
      border: 1px solid #efb4b4;
      background: #fff5f5;
      color: var(--error);
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 12px;
      font-size: 14px;
    }}
    .hidden {{ display: none; }}
    @media (max-width: 900px) {{
      main {{
        grid-template-columns: 1fr;
      }}
      .summary-grid {{
        grid-template-columns: 1fr;
      }}
      .branch-map {{
        grid-template-columns: 1fr;
      }}
      .io-columns {{
        grid-template-columns: 1fr;
      }}
    }}
    @media (max-width: 560px) {{
      .topbar {{
        align-items: flex-start;
        flex-direction: column;
      }}
      .grid-2 {{
        grid-template-columns: 1fr;
      }}
      .actions button {{
        width: 100%;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <h1>Ticket Workflow Demo</h1>
      <div class="status-pill" id="run-status">Ready</div>
    </div>
  </header>
  <main>
    <section>
      <div class="panel-header"><h2>Submit Ticket</h2></div>
      <form id="ticket-form">
        <div class="field">
          <label for="example-ticket">Example Ticket</label>
          <select id="example-ticket" name="example-ticket">
            <option value="">Custom ticket</option>
            {example_options}
          </select>
          <div class="muted" id="example-description">
            Pick an example to load a known scenario, then step through it or run all skills.
          </div>
        </div>
        <div class="grid-2">
          <div class="field">
            <label for="requester_name">Name</label>
            <input id="requester_name" name="requester_name" value="Avery Chen" autocomplete="name">
          </div>
          <div class="field">
            <label for="requester_email">Email</label>
            <input id="requester_email" name="requester_email" value="avery.chen@example.com" autocomplete="email">
          </div>
        </div>
        <div class="grid-2">
          <div class="field">
            <label for="company">Company</label>
            <input id="company" name="company" value="Northstar Retail">
          </div>
          <div class="field">
            <label for="account_tier">Account Tier</label>
            <select id="account_tier" name="account_tier">{tiers}</select>
          </div>
        </div>
        <div class="field">
          <label for="subject">Subject</label>
          <input id="subject" name="subject" value="Dashboard export is missing records" required>
        </div>
        <div class="field">
          <label for="description">Description</label>
          <textarea id="description" name="description" required>
The Analytics Dashboard export is missing records for this week's QBR. The on-screen dashboard has the right filters,
but the CSV export leaves out several accounts.</textarea>
        </div>
        <div class="grid-2">
          <div class="field">
            <label for="affected_system">Affected System</label>
            <select id="affected_system" name="affected_system">{systems}</select>
          </div>
          <div class="field">
            <label for="customer_reported_urgency">Urgency</label>
            <select id="customer_reported_urgency" name="customer_reported_urgency">{urgencies}</select>
          </div>
        </div>
        <div class="field">
          <label for="business_impact_text">Business Impact</label>
          <input id="business_impact_text" name="business_impact_text"
            value="QBR deck is blocked until the export is complete.">
        </div>
        <div class="field">
          <label for="steps_already_tried">Steps Already Tried</label>
          <input id="steps_already_tried" name="steps_already_tried"
            value="Refreshed the dashboard and re-ran the export twice.">
        </div>
        <div class="actions">
          <button type="submit" id="submit-button">Start Step Mode</button>
          <button type="button" class="secondary" id="run-all-button">Run All</button>
          <span class="muted">The workflow writes to an isolated /tmp demo run.</span>
        </div>
      </form>
    </section>
    <section>
      <div class="panel-header"><h2>Workflow Response</h2></div>
      <div class="results-body">
        <div id="error-box" class="error hidden"></div>
        <div class="summary-grid">
          <div class="metric"><span>Ticket</span><strong id="ticket-id">-</strong></div>
          <div class="metric"><span>Category</span><strong id="category">-</strong></div>
          <div class="metric"><span>Priority</span><strong id="priority">-</strong></div>
        </div>
        <div class="debug-panel" id="debug-panel">
          <div class="debug-row">
            <span>Next skill</span>
            <strong id="next-skill">Submit a ticket</strong>
          </div>
          <div class="step-summary" id="step-summary">
            Start step mode to run one skill at a time.
          </div>
          <div class="actions">
            <button type="button" class="secondary" id="previous-button" disabled>Previous</button>
            <button type="button" id="step-button" disabled>Next</button>
          </div>
        </div>
        <div class="section-label">Narrative</div>
        <div class="narrative" id="narrative"></div>
        <div class="section-label">Flow chart</div>
        <div class="flow-chart" id="flow-chart"></div>
        <div class="section-label">Branch decision</div>
        <div class="branch-map" id="branch-map"></div>
        <div class="section-label">Customer response</div>
        <div class="response" id="response-text">
Submit a ticket to run the skill workflow and generate the customer response.</div>
        <div class="feedback hidden" id="feedback-panel">
          <label for="feedback_text">Customer Feedback</label>
          <textarea id="feedback_text">Thanks, that fixed it!</textarea>
          <div class="actions">
            <button class="secondary" id="fixed-button" type="button">Mark Fixed</button>
            <button class="danger" id="broken-button" type="button">Still Broken</button>
          </div>
        </div>
        <div class="section-label">Skill input / output</div>
        <div class="skill-io" id="skill-io"></div>
        <div class="section-label">Step log</div>
        <ul class="timeline" id="timeline"></ul>
      </div>
    </section>
  </main>
  <script>
    const exampleTickets = {examples_json};
    const form = document.querySelector("#ticket-form");
    const exampleSelect = document.querySelector("#example-ticket");
    const exampleDescription = document.querySelector("#example-description");
    const submitButton = document.querySelector("#submit-button");
    const runAllButton = document.querySelector("#run-all-button");
    const stepButton = document.querySelector("#step-button");
    const previousButton = document.querySelector("#previous-button");
    const statusEl = document.querySelector("#run-status");
    const errorBox = document.querySelector("#error-box");
    const responseText = document.querySelector("#response-text");
    const feedbackPanel = document.querySelector("#feedback-panel");
    const feedbackText = document.querySelector("#feedback_text");
    const fixedButton = document.querySelector("#fixed-button");
    const brokenButton = document.querySelector("#broken-button");
    const flowChart = document.querySelector("#flow-chart");
    const branchMap = document.querySelector("#branch-map");
    const narrative = document.querySelector("#narrative");
    const skillIo = document.querySelector("#skill-io");
    const timeline = document.querySelector("#timeline");
    const nextSkill = document.querySelector("#next-skill");
    const stepSummary = document.querySelector("#step-summary");
    let activeRunId = "";
    let activeNextSkill = "";
    let snapshots = [];
    let snapshotIndex = -1;

    function formPayload() {{
      return Object.fromEntries(new FormData(form).entries());
    }}

    function setBusy(isBusy, label = "Running") {{
      submitButton.disabled = isBusy;
      runAllButton.disabled = isBusy;
      previousButton.disabled = isBusy || snapshotIndex <= 0;
      stepButton.disabled = isBusy || !canMoveNext();
      fixedButton.disabled = isBusy || isHistoricalSnapshot();
      brokenButton.disabled = isBusy || isHistoricalSnapshot();
      statusEl.textContent = isBusy ? label : "Ready";
    }}

    function isHistoricalSnapshot() {{
      return snapshotIndex >= 0 && snapshotIndex < snapshots.length - 1;
    }}

    function canMoveNext() {{
      if (snapshotIndex >= 0 && snapshotIndex < snapshots.length - 1) return true;
      return Boolean(activeRunId && activeNextSkill);
    }}

    function refreshStepperButtons() {{
      previousButton.disabled = snapshotIndex <= 0;
      stepButton.disabled = !canMoveNext();
      fixedButton.disabled = isHistoricalSnapshot();
      brokenButton.disabled = isHistoricalSnapshot();
    }}

    function resetWorkflowView(
      message = "Submit a ticket to run the skill workflow and generate the customer response."
    ) {{
      showError("");
      activeRunId = "";
      activeNextSkill = "";
      resetSnapshots();
      document.querySelector("#ticket-id").textContent = "-";
      document.querySelector("#category").textContent = "-";
      document.querySelector("#priority").textContent = "-";
      nextSkill.textContent = "Submit a ticket";
      stepSummary.textContent = "Start step mode to run one skill at a time.";
      responseText.textContent = message;
      feedbackPanel.classList.add("hidden");
      timeline.innerHTML = "";
      renderFlow([]);
      renderBranches([]);
      renderNarrative();
      renderSkillIo();
      refreshStepperButtons();
      statusEl.textContent = "Ready";
    }}

    function applyExampleTicket(example) {{
      for (const [field, value] of Object.entries(example.payload || {{}})) {{
        if (form.elements[field]) {{
          form.elements[field].value = value;
        }}
      }}
      const expected = example.expected_initial_branch
        ? `Expected first branch: ${{example.expected_initial_branch}}.`
        : "";
      exampleDescription.textContent = `${{example.scenario || ""}} ${{expected}}`;
      resetWorkflowView(`Loaded example: ${{example.label}}. Start step mode or run all skills.`);
    }}

    function showError(message) {{
      errorBox.textContent = message;
      errorBox.classList.toggle("hidden", !message);
    }}

    function valueOrDash(value) {{
      return value === undefined || value === null || String(value).trim() === "" ? "-" : value;
    }}

    function resetSnapshots() {{
      snapshots = [];
      snapshotIndex = -1;
    }}

    function storeSnapshot(data) {{
      if (snapshotIndex < snapshots.length - 1) {{
        snapshots = snapshots.slice(0, snapshotIndex + 1);
      }}
      snapshots.push(data);
      snapshotIndex = snapshots.length - 1;
    }}

    function showSnapshot(index) {{
      if (index < 0 || index >= snapshots.length) return;
      snapshotIndex = index;
      renderResult(snapshots[snapshotIndex], {{ store: false }});
    }}

    function renderNarrative(model) {{
      narrative.innerHTML = "";
      const sentences = model?.sentences || [
        {{ parts: [{{ text: "Start step mode to build a ticket narrative." }}] }}
      ];
      for (const sentence of sentences) {{
        const p = document.createElement("p");
        for (const part of sentence.parts || []) {{
          if (part.code !== undefined) {{
            const code = document.createElement("code");
            code.className = "inline-code";
            code.textContent = valueOrDash(part.code);
            p.append(code);
          }} else {{
            p.append(document.createTextNode(part.text || ""));
          }}
        }}
        narrative.append(p);
      }}
    }}

    function renderSkillIo(records) {{
      skillIo.innerHTML = "";
      const items = records || [];
      if (!items.length) {{
        const empty = document.createElement("div");
        empty.className = "io-card";
        empty.textContent = "Run a skill to see the exact inputs and outputs recorded for it.";
        skillIo.append(empty);
        return;
      }}
      for (const record of items) {{
        const card = document.createElement("div");
        card.className = "io-card";
        const title = document.createElement("h3");
        title.textContent = `${{record.label || record.skill}} (${{record.skill}})`;
        const columns = document.createElement("div");
        columns.className = "io-columns";
        columns.append(renderIoList("Inputs", record.inputs || []));
        columns.append(renderIoList("Outputs", record.outputs || []));
        card.append(title, columns);
        if (record.artifacts?.length) {{
          const artifacts = document.createElement("div");
          artifacts.className = "branch-steps";
          artifacts.textContent = "Artifacts: " + record.artifacts.join(", ");
          card.append(artifacts);
        }}
        skillIo.append(card);
      }}
    }}

    function renderIoList(title, fields) {{
      const list = document.createElement("div");
      list.className = "io-list";
      const heading = document.createElement("strong");
      heading.textContent = title;
      list.append(heading);
      for (const field of fields) {{
        const item = document.createElement("div");
        item.className = "io-field";
        const label = document.createElement("span");
        label.textContent = field.label || "-";
        const value = document.createElement("code");
        value.textContent = valueOrDash(field.value);
        item.append(label, value);
        list.append(item);
      }}
      return list;
    }}

    function renderBranches(branches) {{
      branchMap.innerHTML = "";
      const branchCards = branches && branches.length ? branches : [
        {{
          id: "faq",
          label: "FAQ branch",
          state: "pending",
          steps: ["draft-faq-response", "send-customer-response"],
          reason: "Waiting for FAQ decision."
        }},
        {{
          id: "specialist",
          label: "Specialist branch",
          state: "pending",
          steps: ["escalate-to-specialist", "investigate-specialist-solution", "draft-specialist-response"],
          reason: "Waiting for FAQ decision."
        }}
      ];
      for (const branch of branchCards) {{
        const card = document.createElement("div");
        card.className = "branch-card " + (branch.state || "pending");
        const title = document.createElement("strong");
        title.textContent = `${{branch.label || branch.id}} · ${{branch.state || "pending"}}`;
        const reason = document.createElement("span");
        reason.textContent = branch.reason || "";
        const steps = document.createElement("div");
        steps.className = "branch-steps";
        steps.textContent = (branch.steps || []).join(" → ");
        card.append(title, reason, steps);
        branchMap.append(card);
      }}
    }}

    function renderFlow(nodes) {{
      flowChart.innerHTML = "";
      const flowNodes = nodes && nodes.length ? nodes : [
        {{ id: "submit", label: "Submit", state: "current", detail: "Waiting for ticket" }},
        {{ id: "triage", label: "Classify + priority", state: "pending", detail: "" }},
        {{ id: "faq", label: "FAQ check", state: "pending", detail: "" }},
        {{ id: "response", label: "Respond", state: "pending", detail: "" }},
        {{ id: "feedback", label: "Feedback", state: "pending", detail: "" }}
      ];
      for (const node of flowNodes) {{
        const item = document.createElement("div");
        item.className = "flow-node " + (node.state || "pending");
        item.dataset.flowState = node.state || "pending";
        const label = document.createElement("strong");
        label.textContent = node.label || node.id || "-";
        const detail = document.createElement("span");
        detail.textContent = valueOrDash(node.detail);
        const state = document.createElement("span");
        state.className = "flow-state";
        state.textContent = node.state || "pending";
        item.append(label, detail, state);
        flowChart.append(item);
      }}
    }}

    function renderOrchestrator(data) {{
      const state = data.orchestrator || {{}};
      activeNextSkill = state.nextSkill || "";
      const lastStep = state.lastStep || {{}};
      nextSkill.textContent = activeNextSkill
        ? `${{state.nextSkillLabel || activeNextSkill}} (${{activeNextSkill}})`
        : "Workflow complete";
      if (lastStep.skill) {{
        const nextText = activeNextSkill
          ? `Next skill: ${{state.nextSkillLabel || activeNextSkill}} (${{activeNextSkill}}).`
          : "No next skill. The workflow is complete or waiting for a non-skill action.";
        stepSummary.textContent = `${{lastStep.label}} (${{lastStep.skill}}) did: ${{lastStep.summary}} ${{nextText}}`;
      }} else {{
        stepSummary.textContent = activeNextSkill
          ? `Ready to run skill: ${{state.nextSkillLabel || activeNextSkill}} (${{activeNextSkill}}).`
          : "Start step mode to run one skill at a time.";
      }}
      refreshStepperButtons();
    }}

    function renderResult(data, options = {{}}) {{
      if (options.store !== false) {{
        storeSnapshot(data);
      }}
      activeRunId = data.workflowRunId || activeRunId;
      document.querySelector("#ticket-id").textContent = valueOrDash(data.ticketId);
      document.querySelector("#category").textContent = valueOrDash(data.triage?.category);
      document.querySelector("#priority").textContent = valueOrDash(data.triage?.priority);
      responseText.textContent = data.response?.text || "No customer response was generated.";
      renderOrchestrator(data);
      const needsFeedback = data.response?.text || activeNextSkill === "verify-feedback-close-or-reopen";
      feedbackPanel.classList.toggle("hidden", !needsFeedback);
      renderNarrative(data.narrative);
      renderFlow(data.flow?.nodes);
      renderBranches(data.flow?.branches);
      renderSkillIo(data.skillIO);
      timeline.innerHTML = "";
      for (const step of data.steps || []) {{
        const li = document.createElement("li");
        const left = document.createElement("strong");
        left.textContent = step.label || step.skill || "-";
        const right = document.createElement("span");
        right.className = "next";
        right.textContent = step.summary || step.action || step.status || "";
        li.append(left, right);
        timeline.append(li);
      }}
      if (data.feedback?.nextAction) {{
        const li = document.createElement("li");
        const left = document.createElement("strong");
        left.textContent = "feedback";
        const right = document.createElement("span");
        right.className = "next";
        right.textContent = data.feedback.nextAction;
        li.append(left, right);
        timeline.append(li);
      }}
      refreshStepperButtons();
    }}

    renderFlow([]);
    renderBranches([]);
    renderNarrative();
    renderSkillIo();
    refreshStepperButtons();

    exampleSelect.addEventListener("change", () => {{
      const selected = exampleTickets.find((example) => example.id === exampleSelect.value);
      if (!selected) {{
        exampleDescription.textContent =
          "Pick an example to load a known scenario, then step through it or run all skills.";
        resetWorkflowView();
        return;
      }}
      applyExampleTicket(selected);
    }});

    async function postJson(url, payload) {{
      const res = await fetch(url, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(payload)
      }});
      const data = await res.json();
      if (!res.ok) {{
        throw new Error(data.error || "Request failed");
      }}
      return data;
    }}

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      showError("");
      activeRunId = "";
      activeNextSkill = "";
      resetSnapshots();
      setBusy(true, "Starting step mode");
      try {{
        const data = await postJson("/api/tickets/start", formPayload());
        renderResult(data);
      }} catch (error) {{
        showError(error.message);
      }} finally {{
        setBusy(false);
      }}
    }});

    runAllButton.addEventListener("click", async () => {{
      showError("");
      activeRunId = "";
      activeNextSkill = "";
      resetSnapshots();
      setBusy(true, "Running workflow");
      try {{
        const data = await postJson("/api/tickets", formPayload());
        renderResult(data);
      }} catch (error) {{
        showError(error.message);
      }} finally {{
        setBusy(false);
      }}
    }});

    stepButton.addEventListener("click", async () => {{
      if (snapshotIndex >= 0 && snapshotIndex < snapshots.length - 1) {{
        showSnapshot(snapshotIndex + 1);
        return;
      }}
      if (!activeRunId || !activeNextSkill) return;
      showError("");
      setBusy(true, "Running next skill");
      try {{
        const payload = {{ workflow_run_id: activeRunId }};
        if (activeNextSkill === "verify-feedback-close-or-reopen") {{
          payload.feedback_text = feedbackText.value;
        }}
        const data = await postJson("/api/step", payload);
        renderResult(data);
      }} catch (error) {{
        showError(error.message);
      }} finally {{
        setBusy(false);
      }}
    }});

    previousButton.addEventListener("click", () => {{
      showSnapshot(snapshotIndex - 1);
    }});

    fixedButton.addEventListener("click", async () => {{
      feedbackText.value = "Thanks, that fixed it!";
      await sendFeedback();
    }});

    brokenButton.addEventListener("click", async () => {{
      feedbackText.value = "Tried it, still not working.";
      await sendFeedback();
    }});

    async function sendFeedback() {{
      if (!activeRunId) return;
      showError("");
      setBusy(true, "Processing feedback");
      try {{
        const url = activeNextSkill === "verify-feedback-close-or-reopen" ? "/api/step" : "/api/feedback";
        const data = await postJson(url, {{
          workflow_run_id: activeRunId,
          feedback_text: feedbackText.value
        }});
        renderResult(data);
      }} catch (error) {{
        showError(error.message);
      }} finally {{
        setBusy(false);
      }}
    }}
  </script>
</body>
</html>"""


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
