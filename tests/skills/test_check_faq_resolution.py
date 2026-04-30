"""Tests for the check-faq-resolution skill."""

from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "check-faq-resolution" / "scripts" / "check_faq_resolution.py"
_spec = importlib.util.spec_from_file_location("check_faq_resolution", _MODULE_PATH)
assert _spec and _spec.loader
cfr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfr)

DATA_DIR = _REPO_ROOT / "data"


def _seed_triage(out_dir: Path, ticket_id: str, category: str) -> None:
    """Seed a working triage decision for the given ticket."""

    pl.DataFrame(
        [
            {
                "ticket_id": ticket_id,
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


def test_load_faq_context_uses_working_triage_first(tmp_path: Path) -> None:
    _seed_triage(tmp_path, "TKT-00042", "login_access")
    ctx = cfr.load_faq_context(DATA_DIR, tmp_path, "TKT-00042")
    assert ctx["triage_source"] == "working/triage_decisions.csv"
    assert ctx["triage"]["assigned_category"] == "login_access"


def test_load_faq_context_falls_back_to_processed(tmp_path: Path) -> None:
    ctx = cfr.load_faq_context(DATA_DIR, tmp_path, "TKT-00042")
    assert ctx["triage_source"] == "processed/ticket_triage.csv"
    assert ctx["triage"]["ticket_id"] == "TKT-00042"


def test_load_faq_context_no_triage_anywhere_raises(tmp_path: Path) -> None:
    # Build a tiny data dir with the raw tables but a triage CSV that
    # contains no row for the test ticket.
    fake = tmp_path / "fake_data"
    (fake / "raw").mkdir(parents=True)
    (fake / "processed").mkdir()
    pl.DataFrame([{"ticket_id": "TKT-X", "subject": "x", "description": "y", "customer_id": "c"}]).write_csv(
        fake / "raw" / "submitted_tickets.csv"
    )
    # one inactive FAQ row so polars infers the boolean schema correctly,
    # and the active-flag filter then produces an empty frame.
    pl.DataFrame([{"faq_id": "FAQ-X", "active_flag": False}]).write_csv(fake / "raw" / "faq_knowledge_base.csv")
    pl.DataFrame([{"ticket_id": "TKT-OTHER", "assigned_category": "other"}]).write_csv(
        fake / "processed" / "ticket_triage.csv"
    )
    with pytest.raises(LookupError):
        cfr.load_faq_context(fake, tmp_path / "out", "TKT-X")


def test_rank_faq_candidates_active_only_and_category_priority() -> None:
    ctx = cfr.load_faq_context(DATA_DIR, Path("/tmp/this_does_not_exist"), "TKT-00042")
    faqs = ctx["faqs"]
    assert faqs["active_flag"].all()  # only active rows kept
    ranked = cfr.rank_faq_candidates(ctx, faqs)
    top = ranked.row(0, named=True)
    assert top["score"] > 0
    # Top match should belong to a category we'd reasonably expect for TKT-00042
    # (we don't pin the exact id, but the category should match assigned_category
    # OR system_name should match the ticket affected_system).
    ticket_cat = ctx["triage"]["assigned_category"]
    ticket_sys = ctx["ticket"]["affected_system"]
    assert top["category"] == ticket_cat or top["system_name"] == ticket_sys


def test_decide_faq_applicability_no_match_recommends_escalation() -> None:
    ctx = {
        "ticket": {
            "error_or_symptom_detail": "x",
            "steps_already_tried": "x",
            "business_impact_text": "x",
        },
        "triage": {"assigned_category": "other"},
    }
    ranked = pl.DataFrame(
        [
            {
                "faq_id": "FAQ-001",
                "category": "other",
                "system_name": "Customer Portal",
                "issue_pattern": "p",
                "score": 1,
                "overlap_terms": "",
            }
        ]
    )
    decision = cfr.decide_faq_applicability(ctx, ranked)
    assert decision["faq_match_found"] is False
    assert decision["recommended_next_step"] == "escalate-to-specialist"


def test_decide_faq_applicability_strong_match_recommends_draft() -> None:
    ctx = {
        "ticket": {
            "error_or_symptom_detail": "x",
            "steps_already_tried": "x",
            "business_impact_text": "x",
        },
        "triage": {"assigned_category": "login_access"},
    }
    ranked = pl.DataFrame(
        [
            {
                "faq_id": "FAQ-001",
                "category": "login_access",
                "system_name": "Customer Portal",
                "issue_pattern": "redirect",
                "score": 8,
                "overlap_terms": "redirect|portal",
            }
        ]
    )
    decision = cfr.decide_faq_applicability(ctx, ranked)
    assert decision["faq_match_found"] is True
    assert decision["faq_id"] == "FAQ-001"
    assert decision["recommended_next_step"] == "draft-faq-response"


def test_decide_faq_applicability_match_but_missing_info_recommends_escalation() -> None:
    ctx = {
        "ticket": {
            "error_or_symptom_detail": "",
            "steps_already_tried": "",
            "business_impact_text": "",
        },
        "triage": {"assigned_category": "login_access"},
    }
    ranked = pl.DataFrame(
        [
            {
                "faq_id": "FAQ-001",
                "category": "login_access",
                "system_name": "Customer Portal",
                "issue_pattern": "redirect",
                "score": 8,
                "overlap_terms": "redirect|portal",
            }
        ]
    )
    decision = cfr.decide_faq_applicability(ctx, ranked)
    assert decision["faq_match_found"] is True
    assert decision["recommended_next_step"] == "escalate-to-specialist"
    assert "required information" in decision["faq_application_reason"]


def test_main_happy_path_writes_faq_decision(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cfr.main(
        [
            "--ticket-id",
            "TKT-00042",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    decisions = tmp_path / "faq_decisions.csv"
    assert decisions.exists()
    with decisions.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    assert "faq_match_found" in header
    assert "candidate_faq_ids" in header
    assert "search_terms" in header
    assert rows[1][header.index("ticket_id")] == "TKT-00042"
    assert "FAQ check for TKT-00042" in capsys.readouterr().out


def test_main_missing_triage_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Fabricate a tiny data dir with no row for TKT-X in processed triage
    fake = tmp_path / "fake_data"
    (fake / "raw").mkdir(parents=True)
    (fake / "processed").mkdir()
    pl.DataFrame(
        [
            {
                "ticket_id": "TKT-X",
                "submitted_at": "2026-04-30T10:00:00",
                "customer_id": "c",
                "submitted_by_name": "n",
                "submitted_by_email": "e",
                "channel": "portal",
                "subject": "s",
                "description": "d",
                "affected_system": "Customer Portal",
                "customer_reported_urgency": "medium",
                "business_impact_text": "i",
                "attachment_flag": "false",
                "error_or_symptom_detail": "x",
                "steps_already_tried": "y",
                "expected_outcome": "z",
                "availability_window": "any",
                "attachment_description": "",
            }
        ]
    ).write_csv(fake / "raw" / "submitted_tickets.csv")
    pl.DataFrame(
        [
            {
                "faq_id": "F1",
                "category": "other",
                "system_name": "Customer Portal",
                "issue_pattern": "p",
                "symptoms": "s",
                "solution_steps": "x",
                "required_customer_info": "y",
                "last_updated": "2026-01-01",
                "owner": "o",
                "active_flag": True,
            }
        ]
    ).write_csv(fake / "raw" / "faq_knowledge_base.csv")
    pl.DataFrame(schema={"ticket_id": pl.Utf8, "assigned_category": pl.Utf8}).write_csv(
        fake / "processed" / "ticket_triage.csv"
    )
    rc = cfr.main(
        [
            "--ticket-id",
            "TKT-X",
            "--data-dir",
            str(fake),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "classify-prioritize" in err


def test_cli_runs_via_subprocess(tmp_path: Path) -> None:
    """Smoke test that the script runs end-to-end via uv run python."""
    result = subprocess.run(
        [
            sys.executable,
            str(_MODULE_PATH),
            "--ticket-id",
            "TKT-00042",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "faq_decisions.csv").exists()
