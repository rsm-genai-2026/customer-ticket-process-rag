"""Tests for the draft-specialist-response skill."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "draft-specialist-response" / "scripts" / "draft_specialist_response.py"
_spec = importlib.util.spec_from_file_location("draft_specialist_response", _MODULE_PATH)
assert _spec and _spec.loader
dsr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dsr)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"


def _seed_specialist_solution(out_dir: Path) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": SAMPLE_TICKET_ID,
                "created_at": "2026-04-30T12:00:00+00:00",
                "skill_name": "investigate-specialist-solution",
                "specialist_id": "SP-001",
                "specialist_name": "Test Specialist",
                "specialist_group": "identity_security",
                "root_cause": "Permission cache out of sync.",
                "diagnostic_steps": "step 1|step 2",
                "evidence_reviewed": "logs|cache",
                "solution_summary": "Refresh SSO group membership and clear server-side session cache.",
                "customer_action_required": "Sign out completely and back in. Reply to confirm.",
                "specialist_notes": "internal note",
                "requires_follow_up_flag": False,
                "confidence_score": 0.85,
                "inputs_used": "x",
                "decision_summary": "test",
            }
        ]
    ).write_csv(out_dir / "specialist_solutions.csv")


def _seed_escalation(out_dir: Path, reason: str = "no FAQ match found") -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": SAMPLE_TICKET_ID,
                "created_at": "2026-04-30T11:00:00+00:00",
                "skill_name": "escalate-to-specialist",
                "specialist_id": "SP-001",
                "specialist_name": "Test Specialist",
                "specialist_group": "identity_security",
                "specialist_seniority": "senior",
                "specialist_supports_affected_system": True,
                "requested_specialist_group": "identity_security",
                "escalation_reason": reason,
                "handoff_summary": "x",
                "specific_question_for_specialist": "x",
                "customer_evidence_included": "x",
                "missing_information_flag": False,
                "inputs_used": "x",
                "decision_summary": "test",
            }
        ]
    ).write_csv(out_dir / "escalation_decisions.csv")


def test_load_specialist_response_context_happy(tmp_path: Path) -> None:
    _seed_specialist_solution(tmp_path)
    _seed_escalation(tmp_path)
    ctx = dsr.load_specialist_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert ctx["ticket"]["ticket_id"] == SAMPLE_TICKET_ID
    assert ctx["specialist_solution"]["specialist_id"] == "SP-001"


def test_load_specialist_response_context_missing_solution_raises(tmp_path: Path) -> None:
    with pytest.raises(LookupError) as exc:
        dsr.load_specialist_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "investigate-specialist-solution" in str(exc.value)


def test_draft_specialist_response_includes_summary_and_action(tmp_path: Path) -> None:
    _seed_specialist_solution(tmp_path)
    _seed_escalation(tmp_path)
    ctx = dsr.load_specialist_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    draft = dsr.draft_specialist_response(ctx)
    assert "Refresh SSO group membership" in draft["sent_text"]
    assert "Sign out" in draft["customer_action_required"]
    # No internal language leaks.
    assert "internal note" not in draft["sent_text"].lower()
    assert "specialist_notes" not in draft["sent_text"].lower()


def test_draft_specialist_response_acknowledges_post_rejection(tmp_path: Path) -> None:
    _seed_specialist_solution(tmp_path)
    _seed_escalation(tmp_path, reason="re-escalation after customer rejection")
    ctx = dsr.load_specialist_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    draft = dsr.draft_specialist_response(ctx)
    assert draft["is_post_rejection"] is True
    assert "first attempt" in draft["sent_text"].lower()


def test_quality_check_post_rejection_without_acknowledgement_fails() -> None:
    qc = dsr.quality_check_response(
        {
            "draft_text": "x",
            "sent_text": "Hi! Try this. Reply to confirm.",
            "customer_action_required": "Try this and reply",
            "follow_up_request": "Reply",
            "is_post_rejection": True,
        }
    )
    assert qc["ok"] is False
    assert "earlier attempt" in qc["notes"]


def test_quality_check_missing_followup_fails() -> None:
    qc = dsr.quality_check_response(
        {
            "draft_text": "x",
            "sent_text": "Try x and confirm",
            "customer_action_required": "Try x and confirm",
            "follow_up_request": "",
            "is_post_rejection": False,
        }
    )
    assert qc["ok"] is False
    assert "follow_up_request" in qc["notes"]


def test_quality_check_action_without_confirm_or_reply_fails() -> None:
    qc = dsr.quality_check_response(
        {
            "draft_text": "x",
            "sent_text": "Try this. Best regards.",
            "customer_action_required": "Try this.",
            "follow_up_request": "Thanks",
            "is_post_rejection": False,
        }
    )
    assert qc["ok"] is False
    assert "confirm" in qc["notes"]


def test_main_happy_path_writes_drafts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_specialist_solution(tmp_path)
    _seed_escalation(tmp_path)
    rc = dsr.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    drafts = tmp_path / "customer_response_drafts.csv"
    assert drafts.exists()
    with drafts.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    assert rows[1][header.index("message_source")] == "specialist_solution"
    out = capsys.readouterr().out
    assert "Specialist response drafted" in out


def test_main_missing_solution_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = dsr.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 3
    assert "investigate-specialist-solution" in capsys.readouterr().err
