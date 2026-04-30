"""Tests for the receive-ticket skill."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "receive-ticket" / "scripts" / "receive_ticket.py"
_spec = importlib.util.spec_from_file_location("receive_ticket", _MODULE_PATH)
assert _spec and _spec.loader
receive_ticket = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(receive_ticket)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"


def test_load_ticket_context_returns_ticket_and_customer() -> None:
    ctx = receive_ticket.load_ticket_context(DATA_DIR, SAMPLE_TICKET_ID)
    assert ctx["ticket"]["ticket_id"] == SAMPLE_TICKET_ID
    assert ctx["customer"]["customer_id"] == ctx["ticket"]["customer_id"]
    assert ctx["customer"]["customer_name"]


def test_load_ticket_context_missing_ticket_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        receive_ticket.load_ticket_context(DATA_DIR, "TKT-99999")


def test_build_intake_summary_includes_required_fields() -> None:
    ctx = receive_ticket.load_ticket_context(DATA_DIR, SAMPLE_TICKET_ID)
    summary = receive_ticket.build_intake_summary(ctx)
    for key in [
        "ticket_id",
        "submitted_at",
        "customer_name",
        "account_tier",
        "subject",
        "affected_system",
        "customer_reported_urgency",
        "business_impact_text",
        "symptom_detail",
        "steps_already_tried",
        "expected_outcome",
    ]:
        assert key in summary, key
    assert summary["ticket_id"] == SAMPLE_TICKET_ID


def test_build_intake_summary_handles_missing_steps_already_tried() -> None:
    ctx = {
        "ticket": {
            "ticket_id": "TKT-00001",
            "submitted_at": "2026-01-15T10:00:00",
            "customer_id": "CUST-001",
            "channel": "portal",
            "subject": "x",
            "affected_system": "y",
            "customer_reported_urgency": "low",
            "business_impact_text": "z",
            "error_or_symptom_detail": "",
            "steps_already_tried": "",
            "expected_outcome": "",
            "availability_window": "",
            "attachment_flag": "false",
            "attachment_description": "",
        },
        "customer": {
            "customer_id": "CUST-001",
            "customer_name": "Test Co",
            "account_tier": "standard",
            "sla_plan": "basic",
            "region": "NA-West",
            "industry": "logistics",
        },
    }
    summary = receive_ticket.build_intake_summary(ctx)
    assert summary["steps_already_tried"] == "(none reported)"
    assert summary["attachment"] == "(none)"


def test_render_summary_shows_blank_for_empty_fields() -> None:
    ctx = receive_ticket.load_ticket_context(DATA_DIR, SAMPLE_TICKET_ID)
    summary = receive_ticket.build_intake_summary(ctx)
    text = receive_ticket.render_summary(summary)
    assert SAMPLE_TICKET_ID in text
    assert "Next valid action" in text
    assert summary["customer_name"] in text


def test_main_happy_path_writes_action_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = receive_ticket.main(
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
    out = capsys.readouterr().out
    assert SAMPLE_TICKET_ID in out
    log = tmp_path / "ticket_action_log.csv"
    assert log.exists()
    with log.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    assert header[:3] == ["ticket_id", "created_at", "skill_name"]
    for col in ("workflow_run_id", "step_id", "action", "needs_human_review"):
        assert col in header, col
    data = dict(zip(header, rows[1]))
    assert data["ticket_id"] == SAMPLE_TICKET_ID
    assert data["skill_name"] == "receive-ticket"
    assert data["action"] == "intake_summary"
    assert data["workflow_run_id"]
    assert data["step_id"]


def test_main_missing_ticket_returns_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = receive_ticket.main(
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
    err = capsys.readouterr().err
    assert "TKT-99999" in err
    assert not (tmp_path / "ticket_action_log.csv").exists()


def test_main_missing_data_dir_returns_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = receive_ticket.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--data-dir",
            str(tmp_path / "no_such_dir"),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 2
    assert "missing" in capsys.readouterr().err


def test_main_json_envelope_has_stable_shape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = receive_ticket.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
            "--workflow-run-id",
            "wf-test-1",
            "--step-id",
            "step-receive-1",
            "--json",
        ]
    )
    assert rc == 0
    line = capsys.readouterr().out.strip()
    env = json.loads(line)
    assert env["status"] == "ok"
    assert env["skill_name"] == "receive-ticket"
    assert env["workflow_run_id"] == "wf-test-1"
    assert env["step_id"] == "step-receive-1"
    assert env["ticket_id"] == SAMPLE_TICKET_ID
    assert env["next_action"] == "classify-prioritize-ticket"
    assert env["error"] is None
    assert env["outputs"]["intake_summary"]["ticket_id"] == SAMPLE_TICKET_ID


def test_main_idempotent_skip_on_repeat(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = [
        "--ticket-id",
        SAMPLE_TICKET_ID,
        "--data-dir",
        str(DATA_DIR),
        "--out-dir",
        str(tmp_path),
        "--workflow-run-id",
        "wf-idem",
        "--step-id",
        "step-idem",
        "--json",
    ]
    assert receive_ticket.main(args) == 0
    capsys.readouterr()
    # Second call with same workflow_run_id+step_id must not append a row
    assert receive_ticket.main(args) == 0
    line = capsys.readouterr().out.strip()
    env = json.loads(line)
    assert env["status"] == "skipped"
    log = tmp_path / "ticket_action_log.csv"
    with log.open() as f:
        rows = list(csv.reader(f))
    # Header + exactly one data row (idempotency held)
    assert len(rows) == 2


def test_main_json_envelope_on_missing_ticket(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = receive_ticket.main(
        [
            "--ticket-id",
            "TKT-99999",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
            "--json",
        ]
    )
    assert rc == 2
    line = capsys.readouterr().out.strip()
    env = json.loads(line)
    assert env["status"] == "error"
    assert env["error"]["code"] == "ticket_not_found"
    assert "TKT-99999" in env["error"]["message"]
