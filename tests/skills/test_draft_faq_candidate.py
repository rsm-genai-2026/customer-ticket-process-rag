"""Tests for the draft-faq-candidate skill."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "draft-faq-candidate" / "scripts" / "draft_faq_candidate.py"
_spec = importlib.util.spec_from_file_location("draft_faq_candidate", _MODULE_PATH)
assert _spec and _spec.loader
dfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dfc)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"
RUN_ID = "wf-test"

MOCK_CANDIDATE = {
    "category": "login_access",
    "system_name": "Customer Portal",
    "issue_pattern": "sso_session_drift_after_password_change",
    "symptoms": ["User signs in but is logged out within a minute", "Token refresh fails silently"],
    "solution_steps": ["Sign out completely", "Wait two minutes", "Sign back in"],
    "required_customer_info": ["Browser", "OS", "Sign-in timestamp"],
    "confidence": 0.82,
    "reasoning": "Solution is broadly applicable to SSO drift symptoms.",
}


def _seed_specialist_solution(out_dir: Path) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": SAMPLE_TICKET_ID,
                "created_at": "2026-04-30T12:00:00+00:00",
                "skill_name": "investigate-specialist-solution",
                "specialist_id": "SP-001",
                "root_cause": "SSO session drift.",
                "diagnostic_steps": "checked logs",
                "evidence_reviewed": "audit log",
                "solution_summary": "Sign out and back in.",
                "customer_action_required": "Sign back in.",
                "confidence_score": 0.85,
                "workflow_run_id": RUN_ID,
                "step_id": "step-investigate",
                "inputs_used": "x",
                "decision_summary": "x",
            }
        ]
    ).write_csv(out_dir / "specialist_solutions.csv")


def _seed_close_feedback(out_dir: Path, *, next_action: str = "close_ticket") -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": SAMPLE_TICKET_ID,
                "created_at": "2026-04-30T14:00:00+00:00",
                "skill_name": "verify-feedback-close-or-reopen",
                "resolution_accepted": next_action == "close_ticket",
                "customer_feedback_text": "Thanks, that fixed it!",
                "rejection_reason": "",
                "verified_rejection": False,
                "reopened_flag": False,
                "verified_by_it_member_id": "IT-001",
                "verification_notes": "x",
                "next_action": next_action,
                "closure_reason": "x",
                "workflow_run_id": RUN_ID,
                "step_id": "step-feedback",
                "inputs_used": "x",
                "decision_summary": "x",
            }
        ]
    ).write_csv(out_dir / "feedback_decisions.csv")


def _run_main(out_dir: Path, *extra: str) -> tuple[int, dict]:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = dfc.main(
            [
                "--ticket-id",
                SAMPLE_TICKET_ID,
                "--data-dir",
                str(DATA_DIR),
                "--out-dir",
                str(out_dir),
                "--workflow-run-id",
                RUN_ID,
                "--json",
                *extra,
            ]
        )
    last_line = buf.getvalue().strip().splitlines()[-1]
    return rc, json.loads(last_line)


def test_normalize_candidate_unknown_category_falls_back_to_other() -> None:
    raw = {"category": "made_up_category", "issue_pattern": "x", "symptoms": ["a"]}
    out = dfc.normalize_candidate(raw, valid_categories=["login_access", "other"], valid_systems=[])
    assert out["category"] == "other"


def test_normalize_candidate_accepts_pipe_string_for_lists() -> None:
    raw = {"category": "other", "symptoms": "first|second", "solution_steps": "a|b|c"}
    out = dfc.normalize_candidate(raw, valid_categories=[], valid_systems=[])
    assert out["symptoms"] == "first|second"
    assert out["solution_steps"] == "a|b|c"


def test_normalize_candidate_clamps_confidence() -> None:
    assert dfc.normalize_candidate({"confidence": 2.5}, valid_categories=[], valid_systems=[])["confidence"] == 1.0
    assert dfc.normalize_candidate({"confidence": -0.5}, valid_categories=[], valid_systems=[])["confidence"] == 0.0
    assert dfc.normalize_candidate({"confidence": "bad"}, valid_categories=[], valid_systems=[])["confidence"] == 0.0


def test_missing_specialist_solution_raises_via_envelope(tmp_path: Path) -> None:
    _seed_close_feedback(tmp_path)
    rc, env = _run_main(tmp_path)
    assert rc == 3
    assert env["status"] == "error"
    assert env["error"]["code"] == "missing_upstream"


def test_missing_close_feedback_raises_via_envelope(tmp_path: Path) -> None:
    _seed_specialist_solution(tmp_path)
    _seed_close_feedback(tmp_path, next_action="reopen_and_escalate")
    rc, env = _run_main(tmp_path)
    assert rc == 3
    assert env["status"] == "error"
    assert env["error"]["code"] == "missing_upstream"


def test_main_happy_path_writes_candidate(tmp_path: Path) -> None:
    """Hit the real LLM; assert envelope routing and CSV shape, not wording."""
    _seed_specialist_solution(tmp_path)
    _seed_close_feedback(tmp_path)
    rc, env = _run_main(tmp_path)
    assert rc == 0
    assert env["status"] == "ok"
    assert env["next_action"] == "approve-faq-promotion"

    rows = list(csv.DictReader((tmp_path / "faq_candidates.csv").open(newline="")))
    assert len(rows) == 1
    row = rows[0]
    # category must be valid; the normalize layer enforces this regardless of LLM output.
    assert row["category"] in {
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
    assert row["issue_pattern"].strip()
    assert row["solution_steps"].strip()
    confidence = float(row["confidence"])
    assert 0.0 <= confidence <= 1.0
