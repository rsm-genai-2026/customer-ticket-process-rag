"""End-to-end orchestrator test driven by each skill's JSON envelope.

A small in-process orchestrator runs each skill via subprocess so the
JSON envelope contract is exercised exactly as a real workflow engine
would consume it. The orchestrator:

* Reads the ``next_action`` field from every envelope.
* Routes to the named skill, passing a stable ``workflow_run_id`` and a
  fresh ``step_id`` per step.
* Stops when the audit reports a closed state.

The tests assert: every ticket reaches ``close_ticket`` (or the
``close_unresolved_vendor_followup`` terminal), a ``sent_messages.csv``
row exists, idempotent retries do not duplicate rows, live-only mode
works on a clean ``data/working/`` (no ``processed/`` fallback needed).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _REPO_ROOT / "data"

SCRIPT_BY_SKILL = {
    "receive-ticket": "automations/receive-ticket/scripts/receive_ticket.py",
    "classify-prioritize-ticket": ("automations/classify-prioritize-ticket/scripts/classify_prioritize_ticket.py"),
    "check-faq-resolution": "skills/check-faq-resolution/scripts/check_faq_resolution.py",
    "draft-faq-response": "automations/draft-faq-response/scripts/draft_faq_response.py",
    "escalate-to-specialist": "automations/escalate-to-specialist/scripts/escalate_to_specialist.py",
    "investigate-specialist-solution": (
        "skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py"
    ),
    "draft-specialist-response": ("automations/draft-specialist-response/scripts/draft_specialist_response.py"),
    "send-customer-response": ("automations/send-customer-response/scripts/send_customer_response.py"),
    "verify-feedback-close-or-reopen": ("automations/verify-feedback-close-or-reopen/scripts/verify_feedback.py"),
    "audit-ticket-process": "automations/audit-ticket-process/scripts/audit_ticket_process.py",
}


def _run(
    skill: str,
    *,
    ticket_id: str,
    out_dir: Path,
    workflow_run_id: str,
    step_id: str | None = None,
    extra: list[str] | None = None,
) -> dict:
    """Invoke a skill via subprocess and return its parsed JSON envelope."""

    if step_id is None:
        step_id = f"{skill}-{uuid.uuid4().hex[:8]}"
    cmd = [
        sys.executable,
        str(_REPO_ROOT / SCRIPT_BY_SKILL[skill]),
        "--ticket-id",
        ticket_id,
        "--data-dir",
        str(DATA_DIR),
        "--out-dir",
        str(out_dir),
        "--workflow-run-id",
        workflow_run_id,
        "--step-id",
        step_id,
        "--json",
    ]
    if extra:
        cmd.extend(extra)
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode in (0, 2, 3), f"skill {skill} crashed unexpectedly:\n{result.stderr}"
    if not result.stdout.strip():
        raise AssertionError(f"skill {skill} produced no JSON output:\n{result.stderr}")
    return json.loads(result.stdout.strip())


def _drive_ticket(
    *,
    ticket_id: str,
    out_dir: Path,
    workflow_run_id: str,
    feedback_text: str = "Thanks, that fixed it!",
    max_steps: int = 20,
) -> list[dict]:
    """Run the workflow for ``ticket_id`` until the ticket is closed.

    Returns the list of envelopes seen in order. Aborts if more than
    ``max_steps`` skills fire (catches infinite loops).
    """

    envelopes: list[dict] = []
    env = _run(
        "receive-ticket",
        ticket_id=ticket_id,
        out_dir=out_dir,
        workflow_run_id=workflow_run_id,
    )
    envelopes.append(env)

    for _ in range(max_steps):
        env = _run(
            "audit-ticket-process",
            ticket_id=ticket_id,
            out_dir=out_dir,
            workflow_run_id=workflow_run_id,
        )
        envelopes.append(env)
        state = env["outputs"]["state"]
        if state == "closed":
            return envelopes

        next_skill = env["next_action"]
        if not next_skill or next_skill not in SCRIPT_BY_SKILL:
            raise AssertionError(f"audit recommended {next_skill!r}, which is not a runnable skill")

        extra: list[str] = []
        if next_skill == "verify-feedback-close-or-reopen":
            extra = ["--feedback-text", feedback_text]

        next_env = _run(
            next_skill,
            ticket_id=ticket_id,
            out_dir=out_dir,
            workflow_run_id=workflow_run_id,
            extra=extra,
        )
        envelopes.append(next_env)
        if next_env["status"] == "error":
            raise AssertionError(f"skill {next_skill} returned error envelope: {next_env['error']}")
    raise AssertionError(f"workflow for {ticket_id} did not terminate in {max_steps} steps")


@pytest.fixture(scope="module")
def candidate_tickets() -> list[str]:
    """Return ticket ids spanning both the FAQ and specialist branches.

    Probes real classify + FAQ skills against TritonAI, picking tickets from
    high-coverage systems (likely FAQ) and low-coverage systems (likely
    specialist) so the probe terminates after a small number of real LLM
    calls. Module-scoped: one probe per session.
    """
    submitted = pl.read_csv(DATA_DIR / "raw" / "submitted_tickets.csv")
    faq_candidates = submitted.filter(pl.col("affected_system").is_in(["Identity Provider", "Customer Portal"]))[
        "ticket_id"
    ].to_list()[:15]
    spec_candidates = submitted.filter(
        pl.col("affected_system").is_in(["Inventory App", "Analytics Dashboard", "CRM"])
    )["ticket_id"].to_list()[:15]

    faq_branch: list[str] = []
    specialist_branch: list[str] = []

    def _probe(ticket_id: str) -> str | None:
        probe = Path("/tmp") / f"orch_probe_{ticket_id}"
        probe.mkdir(parents=True, exist_ok=True)
        for f in probe.iterdir():
            f.unlink()
        wf = f"probe-{ticket_id}"
        cls_env = _run("classify-prioritize-ticket", ticket_id=ticket_id, out_dir=probe, workflow_run_id=wf)
        if cls_env["status"] != "ok":
            return None
        faq_env = _run("check-faq-resolution", ticket_id=ticket_id, out_dir=probe, workflow_run_id=wf)
        if faq_env["status"] != "ok":
            return None
        return faq_env["next_action"]

    for ticket_id in faq_candidates:
        if len(faq_branch) >= 3:
            break
        if _probe(ticket_id) == "draft-faq-response":
            faq_branch.append(ticket_id)

    for ticket_id in spec_candidates:
        if len(specialist_branch) >= 2:
            break
        if _probe(ticket_id) == "escalate-to-specialist":
            specialist_branch.append(ticket_id)

    if not faq_branch or not specialist_branch:
        pytest.skip("could not find both FAQ and specialist tickets to drive")
    return faq_branch + specialist_branch


def test_orchestrator_drives_multiple_tickets_to_close(tmp_path: Path, candidate_tickets: list[str]) -> None:
    for ticket_id in candidate_tickets:
        wf = f"wf-{ticket_id}"
        envelopes = _drive_ticket(
            ticket_id=ticket_id,
            out_dir=tmp_path,
            workflow_run_id=wf,
        )
        # Every ticket reaches the closed state and recorded a sent message.
        final_audit = envelopes[-1]
        assert final_audit["skill_name"] == "audit-ticket-process"
        assert final_audit["outputs"]["state"] == "closed"
        # The send happened: a sent_messages row exists for this ticket.
        sent = pl.read_csv(tmp_path / "sent_messages.csv")
        assert sent.filter(pl.col("ticket_id") == ticket_id).height >= 1, f"no sent_messages row for {ticket_id}"
        # Closure is one of the two terminal next_actions.
        feedback = pl.read_csv(tmp_path / "feedback_decisions.csv")
        ticket_feedback = feedback.filter(pl.col("ticket_id") == ticket_id)
        assert ticket_feedback.height >= 1
        terminal = ticket_feedback.tail(1).to_dicts()[0]["next_action"]
        assert terminal in {"close_ticket", "close_unresolved_vendor_followup"}


def test_idempotent_retry_does_not_duplicate_step_rows(tmp_path: Path) -> None:
    """Re-running classify-prioritize-ticket with the same workflow_run_id
    and step_id must not append a second row, and must return status=skipped.
    """
    ticket_id = "TKT-00042"
    wf = "wf-idem-orch"
    step = "step-idem-orch"
    env1 = _run(
        "classify-prioritize-ticket",
        ticket_id=ticket_id,
        out_dir=tmp_path,
        workflow_run_id=wf,
        step_id=step,
    )
    assert env1["status"] == "ok"
    env2 = _run(
        "classify-prioritize-ticket",
        ticket_id=ticket_id,
        out_dir=tmp_path,
        workflow_run_id=wf,
        step_id=step,
    )
    assert env2["status"] == "skipped"
    df = pl.read_csv(tmp_path / "triage_decisions.csv")
    assert df.filter(pl.col("ticket_id") == ticket_id).height == 1


def test_live_mode_works_without_processed_fallback(tmp_path: Path) -> None:
    """Drive a ticket through the FAQ branch in live mode end-to-end.

    No skills should need ``--mode demo``. Default mode is live, and the
    orchestrator never seeds working/ from processed/ — every working
    row comes from a real skill run.
    """
    submitted = pl.read_csv(DATA_DIR / "raw" / "submitted_tickets.csv")
    ticket_id = submitted["ticket_id"].to_list()[0]
    wf = f"wf-live-{ticket_id}"

    # Receive
    env = _run("receive-ticket", ticket_id=ticket_id, out_dir=tmp_path, workflow_run_id=wf)
    assert env["status"] == "ok"
    # Classify
    env = _run(
        "classify-prioritize-ticket",
        ticket_id=ticket_id,
        out_dir=tmp_path,
        workflow_run_id=wf,
    )
    assert env["status"] == "ok"
    # FAQ check — must succeed in live mode because triage exists in working/.
    env = _run(
        "check-faq-resolution",
        ticket_id=ticket_id,
        out_dir=tmp_path,
        workflow_run_id=wf,
    )
    assert env["status"] == "ok"


def test_audit_does_not_cross_workflow_runs_for_same_ticket(tmp_path: Path) -> None:
    ticket_id = "TKT-00042"
    env = _run(
        "classify-prioritize-ticket",
        ticket_id=ticket_id,
        out_dir=tmp_path,
        workflow_run_id="wf-other",
        step_id="classify-other",
    )
    assert env["status"] == "ok"

    audit_env = _run(
        "audit-ticket-process",
        ticket_id=ticket_id,
        out_dir=tmp_path,
        workflow_run_id="wf-current",
        step_id="audit-current",
    )
    assert audit_env["outputs"]["state"] == "submitted_awaiting_triage"
    assert audit_env["next_action"] == "classify-prioritize-ticket"


def test_reopen_then_close_unresolved_via_orchestrator(tmp_path: Path, candidate_tickets: list[str]) -> None:
    """Negative feedback twice in a row must close the ticket as unresolved
    rather than spinning the workflow into a second reopen."""
    # Pick a specialist-branch ticket for this scenario (FAQ might match strongly).
    specialist_tickets = candidate_tickets[3:]
    assert specialist_tickets, "need at least one specialist-branch ticket"
    ticket_id = specialist_tickets[0]
    wf = f"wf-reopen-{ticket_id}"

    # First negative -> reopen
    envelopes = _drive_ticket(
        ticket_id=ticket_id,
        out_dir=tmp_path,
        workflow_run_id=wf,
        feedback_text="Tried it, still not working.",
        max_steps=30,
    )
    final = envelopes[-1]
    assert final["outputs"]["state"] == "closed"
    feedback = pl.read_csv(tmp_path / "feedback_decisions.csv").filter(pl.col("ticket_id") == ticket_id)
    last = feedback.tail(1).to_dicts()[0]
    assert last["next_action"] == "close_unresolved_vendor_followup"
