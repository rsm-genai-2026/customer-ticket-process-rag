"""Tests for the investigate-specialist-solution skill."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = (
    _REPO_ROOT / "skills" / "investigate-specialist-solution" / "scripts" / "investigate_specialist_solution.py"
)
_spec = importlib.util.spec_from_file_location("investigate_specialist_solution", _MODULE_PATH)
assert _spec and _spec.loader
iss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(iss)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"


def _seed_triage(out_dir: Path, category: str = "login_access") -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": SAMPLE_TICKET_ID,
                "created_at": "2026-04-30T10:00:00+00:00",
                "skill_name": "classify-prioritize-ticket",
                "assigned_category": category,
                "assigned_priority": "medium",
                "recommended_specialist_group": "identity_security",
                "target_first_response_at": "2026-04-30T18:00:00+00:00",
                "target_resolution_at": "2026-05-01T10:00:00+00:00",
                "classification_evidence": "test",
                "priority_reason": "test",
                "confidence_score": 0.9,
                "inputs_used": "x",
                "decision_summary": "test",
            }
        ]
    ).write_csv(out_dir / "triage_decisions.csv")


def _seed_escalation(
    out_dir: Path,
    specialist_id: str = "SP-001",
    missing_info: bool = False,
) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": SAMPLE_TICKET_ID,
                "created_at": "2026-04-30T11:00:00+00:00",
                "skill_name": "escalate-to-specialist",
                "specialist_id": specialist_id,
                "specialist_name": "Test Specialist",
                "specialist_group": "identity_security",
                "specialist_seniority": "senior",
                "specialist_supports_affected_system": True,
                "requested_specialist_group": "identity_security",
                "escalation_reason": "no FAQ match found",
                "handoff_summary": "x",
                "specific_question_for_specialist": "x",
                "customer_evidence_included": "x",
                "missing_information_flag": missing_info,
                "inputs_used": "x",
                "decision_summary": "test",
            }
        ]
    ).write_csv(out_dir / "escalation_decisions.csv")


def test_load_investigation_context_happy(tmp_path: Path) -> None:
    _seed_escalation(tmp_path)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert ctx["ticket"]["ticket_id"] == SAMPLE_TICKET_ID
    assert ctx["specialist"]["specialist_id"] == "SP-001"


def test_load_investigation_context_no_escalation_raises(tmp_path: Path) -> None:
    with pytest.raises(LookupError) as exc:
        iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "escalate-to-specialist" in str(exc.value)


def test_load_investigation_context_unknown_specialist_raises(tmp_path: Path) -> None:
    _seed_escalation(tmp_path, specialist_id="SP-DOES-NOT-EXIST")
    with pytest.raises(LookupError) as exc:
        iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "SP-DOES-NOT-EXIST" in str(exc.value)


def test_build_diagnostic_plan_login_access() -> None:
    ctx = {"category": "login_access"}
    steps = iss.build_diagnostic_plan(ctx)
    assert steps
    assert any("SSO" in s for s in steps)


def test_infer_root_cause_unknown_falls_back_to_other() -> None:
    ctx = {"category": "no_such_category"}
    rc = iss.infer_root_cause(ctx)
    assert "case-by-case" in rc["root_cause"]


def test_build_solution_summary_lowers_confidence_on_missing_info(tmp_path: Path) -> None:
    _seed_escalation(tmp_path, missing_info=False)
    ctx_full = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    ctx_full["category"] = "login_access"

    out_dir2 = tmp_path / "with_missing"
    out_dir2.mkdir()
    _seed_escalation(out_dir2, missing_info=True)
    ctx_missing = iss.load_investigation_context(DATA_DIR, out_dir2, SAMPLE_TICKET_ID)
    ctx_missing["category"] = "login_access"

    rc = iss.infer_root_cause(ctx_full)
    full = iss.build_solution_summary(ctx_full, rc)
    missing = iss.build_solution_summary(ctx_missing, rc)
    assert missing["confidence_score"] < full["confidence_score"]
    assert "missing" in missing["specialist_notes"].lower()


def test_build_solution_summary_software_bug_sets_followup(tmp_path: Path) -> None:
    _seed_escalation(tmp_path)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    ctx["category"] = "software_bug"
    rc = iss.infer_root_cause(ctx)
    out = iss.build_solution_summary(ctx, rc)
    assert out["requires_follow_up_flag"] is True


def test_build_solution_row_does_not_leak_internal_keywords(tmp_path: Path) -> None:
    _seed_triage(tmp_path, category="login_access")
    _seed_escalation(tmp_path)
    ctx = iss.load_investigation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    ctx["_out_dir"] = str(tmp_path)
    ctx["category"] = iss._category_for(ctx)
    row = iss.build_solution_row(ctx)
    summary_lower = (row["solution_summary"] + " " + row["customer_action_required"]).lower()
    for forbidden in ("credential", "password hash", "audit log id"):
        assert forbidden not in summary_lower


def test_main_happy_path_writes_solution(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_triage(tmp_path)
    _seed_escalation(tmp_path)
    rc = iss.main(
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
    sols = tmp_path / "specialist_solutions.csv"
    assert sols.exists()
    with sols.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    for required in (
        "root_cause",
        "diagnostic_steps",
        "evidence_reviewed",
        "solution_summary",
        "customer_action_required",
        "confidence_score",
    ):
        assert required in header, required
    out = capsys.readouterr().out
    assert "Specialist solution for" in out
    assert "draft-specialist-response" in out


def test_main_no_escalation_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = iss.main(
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
    assert "escalate-to-specialist" in capsys.readouterr().err
