"""Tests for the escalate-to-specialist skill."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "escalate-to-specialist" / "scripts" / "escalate_to_specialist.py"
_spec = importlib.util.spec_from_file_location("escalate_to_specialist", _MODULE_PATH)
assert _spec and _spec.loader
ets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ets)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"


def _seed_triage(
    out_dir: Path, ticket_id: str = SAMPLE_TICKET_ID, group: str = "identity_security", category: str = "login_access"
) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": ticket_id,
                "created_at": "2026-04-30T10:00:00+00:00",
                "skill_name": "classify-prioritize-ticket",
                "assigned_category": category,
                "assigned_priority": "medium",
                "recommended_specialist_group": group,
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


def _seed_faq_decision(
    out_dir: Path, ticket_id: str = SAMPLE_TICKET_ID, match: bool = False, recommend: str = "escalate-to-specialist"
) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": ticket_id,
                "created_at": "2026-04-30T11:00:00+00:00",
                "skill_name": "check-faq-resolution",
                "faq_match_found": match,
                "faq_id": "FAQ-001" if match else "",
                "match_confidence": 0.4,
                "search_terms": "x",
                "candidate_faq_ids": "FAQ-001",
                "required_customer_info_available": True,
                "faq_application_reason": "test",
                "recommended_next_step": recommend,
                "inputs_used": "x",
                "decision_summary": "test",
            }
        ]
    ).write_csv(out_dir / "faq_decisions.csv")


def _seed_feedback_reopen(out_dir: Path, ticket_id: str = SAMPLE_TICKET_ID) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": ticket_id,
                "created_at": "2026-04-30T15:00:00+00:00",
                "skill_name": "verify-feedback-close-or-reopen",
                "resolution_accepted": False,
                "customer_feedback_text": "still broken",
                "rejection_reason": "Steps did not resolve the issue",
                "verified_rejection": True,
                "reopened_flag": True,
                "verified_by_it_member_id": "IT-001",
                "verification_notes": "verified",
                "next_action": "reopen_and_escalate",
                "closure_reason": "",
                "inputs_used": "x",
                "decision_summary": "test",
            }
        ]
    ).write_csv(out_dir / "feedback_decisions.csv")


def test_load_escalation_context_no_match_path(tmp_path: Path) -> None:
    _seed_triage(tmp_path)
    _seed_faq_decision(tmp_path, match=False)
    ctx = ets.load_escalation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert ctx["escalation_reason"] == "no FAQ match found"


def test_load_escalation_context_match_but_missing_info(tmp_path: Path) -> None:
    _seed_triage(tmp_path)
    _seed_faq_decision(tmp_path, match=True, recommend="escalate-to-specialist")
    ctx = ets.load_escalation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "required customer information" in ctx["escalation_reason"]


def test_load_escalation_context_reopen_path(tmp_path: Path) -> None:
    _seed_triage(tmp_path)
    _seed_feedback_reopen(tmp_path)
    ctx = ets.load_escalation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "re-escalation" in ctx["escalation_reason"]


def test_load_escalation_context_no_signal_raises(tmp_path: Path) -> None:
    _seed_triage(tmp_path)
    # No FAQ decision and no reopen: refuse.
    with pytest.raises(LookupError) as exc:
        ets.load_escalation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "check-faq-resolution" in str(exc.value)


def test_select_specialist_prefers_group_plus_system(tmp_path: Path) -> None:
    _seed_triage(tmp_path, group="identity_security")
    _seed_faq_decision(tmp_path, match=False)
    ctx = ets.load_escalation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    chosen = ets.select_specialist(ctx, ctx["specialists"])
    assert chosen["specialist_group"] == "identity_security"


def test_select_specialist_falls_back_when_group_empty() -> None:
    # Build a fake specialists frame: nobody in the requested group, but
    # one supports the system; the script must pick the system supporter.
    specialists = pl.DataFrame(
        [
            {
                "specialist_id": "SP-A",
                "name": "A",
                "specialist_group": "billing_finance",
                "systems_supported": "Billing System",
                "seniority": "junior",
                "max_daily_escalation_capacity": 4,
            },
            {
                "specialist_id": "SP-B",
                "name": "B",
                "specialist_group": "billing_finance",
                "systems_supported": "Customer Portal",
                "seniority": "senior",
                "max_daily_escalation_capacity": 8,
            },
        ]
    )
    ctx = {
        "ticket": {"affected_system": "Customer Portal"},
        "triage": {"recommended_specialist_group": "identity_security"},
        "specialists": specialists,
        "feedback_decision": None,
        "faq_decision": None,
    }
    chosen = ets.select_specialist(ctx, specialists)
    assert chosen["specialist_id"] == "SP-B"  # system supporter wins


def test_select_specialist_seniority_breaks_ties() -> None:
    specialists = pl.DataFrame(
        [
            {
                "specialist_id": "SP-A",
                "name": "A",
                "specialist_group": "identity_security",
                "systems_supported": "Customer Portal",
                "seniority": "junior",
                "max_daily_escalation_capacity": 4,
            },
            {
                "specialist_id": "SP-B",
                "name": "B",
                "specialist_group": "identity_security",
                "systems_supported": "Customer Portal",
                "seniority": "senior",
                "max_daily_escalation_capacity": 8,
            },
        ]
    )
    ctx = {
        "ticket": {"affected_system": "Customer Portal"},
        "triage": {"recommended_specialist_group": "identity_security"},
        "specialists": specialists,
        "feedback_decision": None,
        "faq_decision": None,
    }
    chosen = ets.select_specialist(ctx, specialists)
    assert chosen["specialist_id"] == "SP-B"  # senior wins


def test_build_handoff_includes_steps_and_question(tmp_path: Path) -> None:
    _seed_triage(tmp_path, category="login_access")
    _seed_faq_decision(tmp_path, match=False)
    ctx = ets.load_escalation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    chosen = ets.select_specialist(ctx, ctx["specialists"])
    handoff = ets.build_handoff(ctx, chosen)
    assert SAMPLE_TICKET_ID in handoff["handoff_summary"]
    assert "MFA" in handoff["specific_question_for_specialist"] or "SSO" in handoff["specific_question_for_specialist"]


def test_build_handoff_reopen_includes_rejection_reason(tmp_path: Path) -> None:
    _seed_triage(tmp_path)
    _seed_feedback_reopen(tmp_path)
    ctx = ets.load_escalation_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    chosen = ets.select_specialist(ctx, ctx["specialists"])
    handoff = ets.build_handoff(ctx, chosen)
    assert "REOPEN" in handoff["handoff_summary"]
    assert "Steps did not resolve" in handoff["handoff_summary"]


def test_main_happy_path_writes_escalation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_triage(tmp_path)
    _seed_faq_decision(tmp_path, match=False)
    rc = ets.main(
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
    decisions = tmp_path / "escalation_decisions.csv"
    assert decisions.exists()
    with decisions.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    assert "specialist_id" in header
    assert "handoff_summary" in header
    assert rows[1][header.index("ticket_id")] == SAMPLE_TICKET_ID
    out = capsys.readouterr().out
    assert "Escalation for" in out
    assert "investigate-specialist-solution" in out


def test_main_no_signal_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_triage(tmp_path)
    rc = ets.main(
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
    err = capsys.readouterr().err
    assert "no upstream signal" in err
