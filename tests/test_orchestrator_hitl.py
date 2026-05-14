"""Integration-style tests for the two new human-in-the-loop gates.

These exercise the orchestrator's full step routing on the specialist
branch: ``draft-specialist-response`` → pause at ``review-specialist-draft``,
approve/reject behavior, and the post-feedback FAQ-promotion gate. All LLM
calls are real (TritonAI); assertions are structural and routing-focused,
not contents-of-LLM-output focused. Requires ``TRITONAI_API_KEY`` in ``.env``.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "scripts" / "orchestrator.py"
_spec = importlib.util.spec_from_file_location("orchestrator", _MODULE_PATH)
assert _spec and _spec.loader
orchestrator = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = orchestrator
_spec.loader.exec_module(orchestrator)


# A ticket that the FAQ skill should escalate (no matching FAQ exists for the
# specific "nightly 502 during migration" pattern in our synthetic KB).
SPECIALIST_PAYLOAD = {
    "requester_name": "Iris Woods",
    "requester_email": "iris.woods@example.com",
    "company": "Atlas Manufacturing",
    "account_tier": "enterprise",
    "subject": "Nightly subscription sync bug returns 502",
    "description": (
        "Our nightly subscription sync against your billing API has been returning "
        "intermittent 502 errors for the past three nights during the production "
        "migration window. This is not a credentials, MFA, login, or password issue; "
        "we have ruled those out. The failure is a backend integration bug."
    ),
    "affected_system": "Billing System",
    "customer_reported_urgency": "critical",
    "business_impact_text": "Migration job blocked.",
    "steps_already_tried": "Retried from two regions.",
}


def _require_specialist_branch(result: dict) -> None:
    """Skip the test if the real LLM unexpectedly took the FAQ branch."""
    next_step = result["orchestrator"]["nextStep"]
    if next_step != "review-specialist-draft":
        pytest.skip(
            f"LLM routed ticket to FAQ branch instead of specialist (nextStep={next_step!r}); "
            "this test only exercises the specialist HITL gate"
        )


def test_run_until_response_pauses_at_specialist_draft_review(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    result = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(result)
    # The draft text from the LLM-then-template should be available for the UI.
    assert result["specialistDraftReview"]["draftText"].strip()
    assert result["specialistDraftReview"]["retryCount"] == 0


def test_review_approve_drives_to_feedback_pause(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(first)
    workflow_id = first["workflowRunId"]

    result = o.review_specialist_draft(
        workflow_id,
        decision="approve",
        reviewer_notes="Looks fine.",
    )
    # After approve the orchestrator runs send and stops at verify-feedback.
    assert result["orchestrator"]["nextStep"] == "verify-feedback-close-or-reopen"
    paths = orchestrator.read_metadata(tmp_path, workflow_id)
    rows = list(csv.DictReader((paths.out_dir / "specialist_draft_reviews.csv").open(newline="")))
    assert rows[0]["decision"] == "approve"


def test_review_approve_with_edit_changes_sent_text(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(first)
    workflow_id = first["workflowRunId"]

    edited = "Edited body: please sign back in and confirm."
    result = o.review_specialist_draft(workflow_id, decision="approve", edited_text=edited)
    assert result["response"]["text"] == edited


def test_review_reject_first_time_loops_back_and_stops_at_review_again(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(first)
    workflow_id = first["workflowRunId"]

    after_reject = o.review_specialist_draft(workflow_id, decision="reject", reviewer_notes="Try again.")
    # The orchestrator should re-run investigate + redraft and stop at review again.
    assert after_reject["orchestrator"]["nextStep"] == "review-specialist-draft"
    assert after_reject["specialistDraftReview"]["retryCount"] == 1


def test_review_reject_second_time_forces_approve_through(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(first)
    workflow_id = first["workflowRunId"]

    o.review_specialist_draft(workflow_id, decision="reject")
    forced = o.review_specialist_draft(workflow_id, decision="reject")
    # Forced approve sends, ends at verify-feedback pause.
    assert forced["orchestrator"]["nextStep"] == "verify-feedback-close-or-reopen"
    paths = orchestrator.read_metadata(tmp_path, workflow_id)
    rows = list(csv.DictReader((paths.out_dir / "specialist_draft_reviews.csv").open(newline="")))
    assert rows[-1]["forced_approve"] == "true"


_VALID_FAQ_CATEGORIES = {
    "login_access",
    "password_reset",
    "billing_account",
    "software_bug",
    "hardware_issue",
    "network_connectivity",
    "email_calendar",
    "data_reporting",
    "security_request",
    "other",
}


def test_process_feedback_specialist_close_pauses_at_faq_promotion(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(first)
    workflow_id = first["workflowRunId"]
    o.review_specialist_draft(workflow_id, decision="approve")

    after_feedback = o.process_feedback(workflow_id, "Thanks, the workaround fixed it!")
    assert after_feedback["orchestrator"]["nextStep"] == "approve-faq-promotion"
    # The LLM-pre-filled candidate should be exposed for the UI panel.
    cand = after_feedback["faqCandidate"]
    assert cand["category"] in _VALID_FAQ_CATEGORIES
    assert isinstance(cand["issuePattern"], str) and cand["issuePattern"].strip()


def test_process_feedback_faq_branch_does_not_trigger_faq_promotion(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(
        {
            "requester_name": "Avery Chen",
            "requester_email": "avery@example.com",
            "company": "Northstar Retail",
            "subject": "Customer Portal sign-in redirects in a loop",
            "description": (
                "The sign-in page loops between SSO and the portal without completing. "
                "Using Chrome on Windows; happy to share a screenshot."
            ),
            "affected_system": "Customer Portal",
        }
    )
    if first["orchestrator"]["nextStep"] != "verify-feedback-close-or-reopen":
        pytest.skip(
            f"LLM did not take FAQ branch for the SSO-loop ticket (nextStep={first['orchestrator']['nextStep']!r})"
        )
    workflow_id = first["workflowRunId"]
    after = o.process_feedback(workflow_id, "Thanks, that fixed it!")
    # FAQ-branch close goes straight to audit-ticket-process, which is terminal.
    assert after["orchestrator"]["nextStep"] == ""
    assert after["orchestrator"]["terminal"] is True


def test_approve_faq_promotion_drives_to_terminal_and_appends_kb(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(first)
    workflow_id = first["workflowRunId"]
    o.review_specialist_draft(workflow_id, decision="approve")
    o.process_feedback(workflow_id, "Thanks, the workaround fixed it!")

    after = o.approve_faq_promotion(workflow_id, decision="approve", reviewer_notes="Keep this one.")
    assert after["orchestrator"]["nextStep"] == ""
    assert after["orchestrator"]["terminal"] is True

    paths = orchestrator.read_metadata(tmp_path, workflow_id)
    kb_rows = list(csv.DictReader((paths.data_dir / "raw" / "faq_knowledge_base.csv").open(newline="")))
    promoted = [row for row in kb_rows if row.get("owner") == "workflow_promotion"]
    assert len(promoted) == 1
    assert promoted[0]["issue_pattern"].strip()


def test_skip_faq_promotion_does_not_modify_kb(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(first)
    workflow_id = first["workflowRunId"]
    o.review_specialist_draft(workflow_id, decision="approve")
    o.process_feedback(workflow_id, "Thanks, the workaround fixed it!")
    paths = orchestrator.read_metadata(tmp_path, workflow_id)
    kb_before = (paths.data_dir / "raw" / "faq_knowledge_base.csv").read_text()

    after = o.approve_faq_promotion(workflow_id, decision="skip")
    assert after["orchestrator"]["terminal"] is True
    kb_after = (paths.data_dir / "raw" / "faq_knowledge_base.csv").read_text()
    assert kb_before == kb_after


def test_step_refuses_to_advance_at_hitl_gate(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(first)
    workflow_id = first["workflowRunId"]
    # Clicking the generic Next button while paused on review-specialist-draft
    # must raise — the UI's Next is blocked too, but the server enforces it.
    with pytest.raises(orchestrator.WorkflowError) as exc:
        o.step(workflow_id)
    assert "supervisor decision" in str(exc.value)


def test_review_methods_validate_decision(tmp_path: Path) -> None:
    o = orchestrator.TicketWorkflowOrchestrator(work_root=tmp_path)
    first = o.run_until_response(SPECIALIST_PAYLOAD)
    _require_specialist_branch(first)
    workflow_id = first["workflowRunId"]
    with pytest.raises(orchestrator.WorkflowError):
        o.review_specialist_draft(workflow_id, decision="maybe")
    with pytest.raises(orchestrator.WorkflowError):
        o.approve_faq_promotion(workflow_id, decision="approve")  # not waiting on this gate yet
