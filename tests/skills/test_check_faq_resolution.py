"""Tests for the check-faq-resolution skill."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
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

LLM_MATCH = {
    "faq_match_found": True,
    "faq_id": "FAQ-001",
    "confidence": 0.91,
    "required_customer_info_available": True,
    "reason": "The ticket describes the same SSO redirect loop covered by the FAQ.",
    "ticket_evidence": "portal loops between SSO and portal",
    "faq_evidence": "Sign-in page loops between SSO and portal",
}

LLM_NO_MATCH = {
    "faq_match_found": False,
    "faq_id": "",
    "confidence": 0.24,
    "required_customer_info_available": False,
    "reason": "No FAQ directly covers the reported issue.",
    "ticket_evidence": "unlisted issue",
    "faq_evidence": "",
}


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
    # Default function-level mode is "demo" to keep tutorial-style tests working.
    ctx = cfr.load_faq_context(DATA_DIR, tmp_path, "TKT-00042")
    assert "processed/ticket_triage.csv" in ctx["triage_source"]
    assert ctx["triage"]["ticket_id"] == "TKT-00042"


def test_load_faq_context_live_mode_refuses_processed_fallback(tmp_path: Path) -> None:
    with pytest.raises(LookupError) as exc:
        cfr.load_faq_context(DATA_DIR, tmp_path, "TKT-00042", mode="live")
    assert "working/" in str(exc.value)
    assert "--mode demo" in str(exc.value)


def test_load_faq_context_no_triage_anywhere_raises(tmp_path: Path) -> None:
    fake = tmp_path / "fake_data"
    (fake / "raw").mkdir(parents=True)
    (fake / "processed").mkdir()
    pl.DataFrame([{"ticket_id": "TKT-X", "subject": "x", "description": "y", "customer_id": "c"}]).write_csv(
        fake / "raw" / "submitted_tickets.csv"
    )
    pl.DataFrame([{"faq_id": "FAQ-X", "active_flag": False}]).write_csv(fake / "raw" / "faq_knowledge_base.csv")
    pl.DataFrame([{"ticket_id": "TKT-OTHER", "assigned_category": "other"}]).write_csv(
        fake / "processed" / "ticket_triage.csv"
    )
    with pytest.raises(LookupError):
        cfr.load_faq_context(fake, tmp_path / "out", "TKT-X")


def test_build_llm_prompt_passes_ticket_triage_and_all_active_faqs(tmp_path: Path) -> None:
    _seed_triage(tmp_path, "TKT-00042", "login_access")
    ctx = cfr.load_faq_context(DATA_DIR, tmp_path, "TKT-00042")
    prompt = cfr.build_llm_prompt(ctx)

    assert "Choose the single FAQ" in prompt
    assert "TKT-00042" in prompt
    assert "assigned_category" in prompt
    assert "FAQ-001" in prompt
    assert "FAQ-033" in prompt
    assert "required_json" in prompt


def test_normalize_llm_decision_recommends_draft_for_confident_match() -> None:
    decision = cfr.normalize_llm_decision(LLM_MATCH, {"FAQ-001"})

    assert decision["faq_match_found"] is True
    assert decision["faq_id"] == "FAQ-001"
    assert decision["match_confidence"] == 0.91
    assert decision["recommended_next_step"] == "draft-faq-response"
    assert "Evidence:" in decision["faq_application_reason"]


def test_normalize_llm_decision_routes_no_match_to_specialist() -> None:
    decision = cfr.normalize_llm_decision(LLM_NO_MATCH, {"FAQ-001"})

    assert decision["faq_match_found"] is False
    assert decision["faq_id"] == ""
    assert decision["recommended_next_step"] == "escalate-to-specialist"


def test_normalize_llm_decision_rejects_unknown_faq_id() -> None:
    raw = dict(LLM_MATCH, faq_id="FAQ-999", confidence=0.95)
    decision = cfr.normalize_llm_decision(raw, {"FAQ-001"})

    assert decision["faq_match_found"] is False
    assert decision["faq_id"] == ""
    assert decision["match_confidence"] == 0.40
    assert "unknown FAQ id" in decision["faq_application_reason"]


def test_build_faq_decision_row_calls_llm(tmp_path: Path) -> None:
    """Hit the real LLM and assert the row's structural invariants.

    Wording of the LLM's reason/evidence is not asserted (non-deterministic);
    keys, types, and the recommended_next_step routing are.
    """
    _seed_triage(tmp_path, "TKT-00042", "login_access")
    ctx = cfr.load_faq_context(DATA_DIR, tmp_path, "TKT-00042")

    row = cfr.build_faq_decision_row(ctx, model=cfr.DEFAULT_MODEL)

    assert isinstance(row["faq_match_found"], bool)
    assert row["search_terms"] == "llm_full_faq_review"
    assert "FAQ-001" in row["candidate_faq_ids"]
    assert f"llm_model={cfr.DEFAULT_MODEL}" in row["decision_summary"]
    assert row["recommended_next_step"] in {"draft-faq-response", "escalate-to-specialist"}


def test_main_happy_path_writes_faq_decision(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_triage(tmp_path, "TKT-00042", "login_access")
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
    assert "workflow_run_id" in header
    assert rows[1][header.index("ticket_id")] == "TKT-00042"
    assert "LLM model" in capsys.readouterr().out


def test_main_demo_mode_falls_back_to_processed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cfr.main(
        [
            "--ticket-id",
            "TKT-00042",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
            "--mode",
            "demo",
        ]
    )
    assert rc == 0
    assert (tmp_path / "faq_decisions.csv").exists()


def test_main_live_mode_refuses_processed_fallback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
    assert rc == 3
    assert "--mode demo" in capsys.readouterr().err


def test_main_missing_triage_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
    """Smoke test the CLI end-to-end against the real LLM."""

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
            "--mode",
            "demo",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "faq_decisions.csv").exists()


def test_cli_emits_json_envelope(tmp_path: Path) -> None:
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
            "--mode",
            "demo",
            "--json",
            "--workflow-run-id",
            "wf-cli",
            "--step-id",
            "step-cli",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    env_out = json.loads(result.stdout.strip())
    assert env_out["status"] == "ok"
    assert env_out["skill_name"] == "check-faq-resolution"
    assert env_out["workflow_run_id"] == "wf-cli"
    assert env_out["next_action"] in {"draft-faq-response", "escalate-to-specialist"}
