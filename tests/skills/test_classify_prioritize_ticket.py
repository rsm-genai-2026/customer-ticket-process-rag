"""Tests for the classify-prioritize-ticket skill."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "classify-prioritize-ticket" / "scripts" / "classify_prioritize_ticket.py"
_spec = importlib.util.spec_from_file_location("classify_prioritize_ticket", _MODULE_PATH)
assert _spec and _spec.loader
cpt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpt)

DATA_DIR = _REPO_ROOT / "data"


def _categories() -> pl.DataFrame:
    return pl.read_csv(DATA_DIR / "dictionaries" / "categories.csv")


def _priority_rules() -> pl.DataFrame:
    return pl.read_csv(DATA_DIR / "dictionaries" / "priority_rules.csv")


def _ctx(
    *,
    description: str = "Cannot log into customer portal",
    subject: str = "login issue",
    affected_system: str = "Customer Portal",
    tier: str = "premium",
    urgency: str = "high",
) -> dict:
    return {
        "ticket": {
            "ticket_id": "TKT-TEST",
            "subject": subject,
            "description": description,
            "error_or_symptom_detail": "",
            "steps_already_tried": "",
            "expected_outcome": "",
            "business_impact_text": "",
            "affected_system": affected_system,
            "customer_reported_urgency": urgency,
        },
        "customer": {"customer_id": "CUST-TEST", "account_tier": tier},
        "categories": _categories(),
        "priority_rules": _priority_rules(),
    }


def test_score_categories_login_text_picks_login_access() -> None:
    scored = cpt.score_categories(_ctx(), _categories())
    assert scored.row(0, named=True)["category"] == "login_access"
    assert scored.row(0, named=True)["score"] > 0


def test_score_categories_no_keyword_no_system_returns_all_zero() -> None:
    ctx = _ctx(subject="general", description="hello", affected_system="UnknownSystem")
    scored = cpt.score_categories(ctx, _categories())
    assert scored["score"].max() == 0


def test_score_categories_system_match_bonus_is_applied() -> None:
    # Subject/description with no keyword, but system matches login_access typical systems
    ctx = _ctx(subject="something", description="anything", affected_system="Identity Provider")
    scored = cpt.score_categories(ctx, _categories())
    top = scored.row(0, named=True)
    assert top["system_match"] is True


def test_assign_priority_is_monotonic_in_tier() -> None:
    rules = _priority_rules()
    for urgency in ["low", "medium", "high", "critical"]:
        for category in ["login_access", "software_bug", "password_reset"]:
            std = cpt.assign_priority(_ctx(tier="standard", urgency=urgency), rules, category)["score"]
            ent = cpt.assign_priority(_ctx(tier="enterprise", urgency=urgency), rules, category)["score"]
            assert ent >= std, (urgency, category, std, ent)


def test_assign_priority_is_monotonic_in_urgency() -> None:
    rules = _priority_rules()
    for tier in ["standard", "premium", "enterprise"]:
        scores = [
            cpt.assign_priority(_ctx(tier=tier, urgency=u), rules, "login_access")["score"]
            for u in ["low", "medium", "high", "critical"]
        ]
        assert scores == sorted(scores)


def test_assign_priority_returns_valid_priority() -> None:
    rules = _priority_rules()
    valid = set(rules["priority"].to_list())
    out = cpt.assign_priority(_ctx(), rules, "login_access")
    assert out["priority"] in valid


def test_build_triage_decision_for_login_ticket_picks_identity_security() -> None:
    decision = cpt.build_triage_decision(_ctx())
    assert decision["assigned_category"] == "login_access"
    assert decision["recommended_specialist_group"] == "identity_security"
    assert 0.0 < decision["confidence_score"] <= 0.95
    assert decision["target_first_response_at"] > decision["created_at"]
    assert decision["target_resolution_at"] >= decision["target_first_response_at"]


def test_build_triage_decision_falls_back_to_other_when_no_signal() -> None:
    ctx = _ctx(subject="x", description="y", affected_system="UnknownSystem")
    decision = cpt.build_triage_decision(ctx)
    assert decision["assigned_category"] == "other"
    assert "fell back" in decision["classification_evidence"]


def test_main_happy_path_writes_triage_and_action_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cpt.main(
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
    triage_path = tmp_path / "triage_decisions.csv"
    log_path = tmp_path / "ticket_action_log.csv"
    assert triage_path.exists() and log_path.exists()

    with triage_path.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    assert "assigned_category" in header
    assert "target_first_response_at" in header
    assert rows[1][header.index("ticket_id")] == "TKT-00042"
    assert rows[1][header.index("assigned_category")]
    assert rows[1][header.index("assigned_priority")] in {"low", "medium", "high", "urgent"}

    out = capsys.readouterr().out
    assert "Triage for TKT-00042" in out
    assert "Next valid action" in out


def test_main_missing_ticket_returns_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cpt.main(
        [
            "--ticket-id",
            "TKT-99999",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 2
    assert "TKT-99999" in capsys.readouterr().err
    assert not (tmp_path / "triage_decisions.csv").exists()


def test_main_json_envelope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    rc = cpt.main(
        [
            "--ticket-id",
            "TKT-00042",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
            "--workflow-run-id",
            "wf-cpt-1",
            "--step-id",
            "step-cpt-1",
            "--json",
        ]
    )
    assert rc == 0
    env = json.loads(capsys.readouterr().out.strip())
    assert env["status"] == "ok"
    assert env["skill_name"] == "classify-prioritize-ticket"
    assert env["workflow_run_id"] == "wf-cpt-1"
    assert env["step_id"] == "step-cpt-1"
    assert env["next_action"] == "check-faq-resolution"
    assert env["outputs"]["assigned_priority"] in {"low", "medium", "high", "urgent"}
    assert env["confidence"] is not None
    assert isinstance(env["review_required"], bool)


def test_main_idempotent_skip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    args = [
        "--ticket-id",
        "TKT-00042",
        "--data-dir",
        str(DATA_DIR),
        "--out-dir",
        str(tmp_path),
        "--workflow-run-id",
        "wf-1",
        "--step-id",
        "step-1",
        "--json",
    ]
    assert cpt.main(args) == 0
    capsys.readouterr()
    assert cpt.main(args) == 0
    env = json.loads(capsys.readouterr().out.strip())
    assert env["status"] == "skipped"
    # File still has only one data row.
    df = pl.read_csv(tmp_path / "triage_decisions.csv")
    assert df.height == 1


def test_main_writes_workflow_metadata_columns(tmp_path: Path) -> None:
    rc = cpt.main(
        [
            "--ticket-id",
            "TKT-00042",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
            "--workflow-run-id",
            "wf-meta",
            "--step-id",
            "step-meta",
        ]
    )
    assert rc == 0
    df = pl.read_csv(tmp_path / "triage_decisions.csv")
    assert "workflow_run_id" in df.columns
    assert "step_id" in df.columns
    row = df.to_dicts()[0]
    assert row["workflow_run_id"] == "wf-meta"
    assert row["step_id"] == "step-meta"
