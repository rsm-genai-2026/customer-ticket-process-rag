"""Tests for the audit-ticket-process skill."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "audit-ticket-process" / "scripts" / "audit_ticket_process.py"
_spec = importlib.util.spec_from_file_location("audit_ticket_process", _MODULE_PATH)
assert _spec and _spec.loader
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

DATA_DIR = _REPO_ROOT / "data"


def _isolated_data(tmp_path: Path) -> Path:
    """Build a tiny but fully-shaped data dir with one ticket and empty processed tables.

    Lets us test "fresh ticket" / "triaged but no FAQ check yet" / etc.
    states without depending on whichever historical rows the real
    ``data/processed/`` tables have.
    """

    raw = tmp_path / "data" / "raw"
    proc = tmp_path / "data" / "processed"
    dicts = tmp_path / "data" / "dictionaries"
    for d in (raw, proc, dicts):
        d.mkdir(parents=True)

    pl.DataFrame(
        [
            {
                "ticket_id": "TKT-X",
                "submitted_at": "2026-04-30T08:00:00",
                "customer_id": "CUST-001",
                "submitted_by_name": "Alex",
                "submitted_by_email": "a@x.example",
                "channel": "portal",
                "subject": "Cannot log in",
                "description": "issue",
                "affected_system": "Customer Portal",
                "customer_reported_urgency": "medium",
                "business_impact_text": "blocked",
                "attachment_flag": "false",
                "error_or_symptom_detail": "x",
                "steps_already_tried": "y",
                "expected_outcome": "z",
                "availability_window": "any",
                "attachment_description": "",
            }
        ]
    ).write_csv(raw / "submitted_tickets.csv")

    pl.DataFrame(
        [
            {
                "customer_id": "CUST-001",
                "customer_name": "Test Co",
                "account_tier": "premium",
                "industry": "logistics",
                "region": "NA-West",
                "sla_plan": "business",
                "active_users": 100,
                "relationship_start_date": "2025-01-01",
            }
        ]
    ).write_csv(raw / "customers.csv")
    # Empty-but-typed processed tables
    for name, schema in [
        (
            "ticket_triage",
            {"ticket_id": pl.Utf8, "triaged_at": pl.Utf8, "assigned_category": pl.Utf8, "faq_match_found": pl.Boolean},
        ),
        (
            "faq_checks",
            {"ticket_id": pl.Utf8, "faq_checked_at": pl.Utf8, "faq_match_found": pl.Boolean, "faq_id": pl.Utf8},
        ),
        ("specialist_escalations", {"ticket_id": pl.Utf8, "escalated_at": pl.Utf8}),
        ("specialist_investigations", {"ticket_id": pl.Utf8, "solution_created_at": pl.Utf8}),
        ("customer_messages", {"ticket_id": pl.Utf8, "sent_at": pl.Utf8}),
        ("resolution_feedback", {"ticket_id": pl.Utf8, "customer_reply_at": pl.Utf8, "closed_at": pl.Utf8}),
    ]:
        pl.DataFrame(schema=schema).write_csv(proc / f"{name}.csv")

    return tmp_path / "data"


def _seed_triage(out_dir: Path) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": "TKT-X",
                "created_at": "2026-04-30T09:00:00+00:00",
                "skill_name": "classify-prioritize-ticket",
                "assigned_category": "login_access",
                "assigned_priority": "medium",
                "recommended_specialist_group": "identity_security",
                "target_first_response_at": "2026-04-30T18:00:00+00:00",
                "target_resolution_at": "2026-05-01T08:00:00+00:00",
                "classification_evidence": "test",
                "priority_reason": "test",
                "confidence_score": 0.9,
                "inputs_used": "x",
                "decision_summary": "x",
            }
        ]
    ).write_csv(out_dir / "triage_decisions.csv")


def _seed_faq(out_dir: Path, match: bool = True) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": "TKT-X",
                "created_at": "2026-04-30T09:30:00+00:00",
                "skill_name": "check-faq-resolution",
                "faq_match_found": match,
                "faq_id": "FAQ-001" if match else "",
                "match_confidence": 0.9 if match else 0.2,
                "search_terms": "x",
                "candidate_faq_ids": "FAQ-001",
                "required_customer_info_available": True,
                "faq_application_reason": "x",
                "recommended_next_step": "draft-faq-response" if match else "escalate-to-specialist",
                "inputs_used": "x",
                "decision_summary": "x",
            }
        ]
    ).write_csv(out_dir / "faq_decisions.csv")


def _seed_response(out_dir: Path) -> None:
    pl.DataFrame(
        [
            {
                "message_id": "MSG-X",
                "ticket_id": "TKT-X",
                "created_at": "2026-04-30T09:45:00+00:00",
                "skill_name": "draft-faq-response",
                "message_source": "faq",
                "draft_text": "x",
                "sent_text": "x",
                "customer_action_required": "x",
                "included_context": "x",
                "follow_up_request": "x",
                "quality_check_notes": "",
                "inputs_used": "x",
                "decision_summary": "x",
                "confidence_score": 0.9,
            }
        ]
    ).write_csv(out_dir / "customer_response_drafts.csv")


def _seed_feedback_close(out_dir: Path) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": "TKT-X",
                "created_at": "2026-04-30T10:00:00+00:00",
                "skill_name": "verify-feedback-close-or-reopen",
                "resolution_accepted": True,
                "customer_feedback_text": "thanks",
                "rejection_reason": "",
                "verified_rejection": False,
                "reopened_flag": False,
                "verified_by_it_member_id": "",
                "verification_notes": "x",
                "next_action": "close_ticket",
                "closure_reason": "customer confirmed",
                "inputs_used": "x",
                "decision_summary": "x",
            }
        ]
    ).write_csv(out_dir / "feedback_decisions.csv")


def test_load_ticket_history_returns_ticket_and_customer(tmp_path: Path) -> None:
    data = _isolated_data(tmp_path)
    history = audit.load_ticket_history(data, tmp_path / "out", "TKT-X")
    assert history["ticket"]["ticket_id"] == "TKT-X"
    assert history["customer"]["customer_name"] == "Test Co"


def test_state_fresh_ticket_recommends_classify(tmp_path: Path) -> None:
    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    history = audit.load_ticket_history(data, out, "TKT-X")
    state = audit.infer_current_state(history)
    assert state["state"] == "submitted_awaiting_triage"
    assert audit.list_valid_next_actions(state) == ["classify-prioritize-ticket"]


def test_state_after_triage_recommends_faq_check(tmp_path: Path) -> None:
    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _seed_triage(out)
    history = audit.load_ticket_history(data, out, "TKT-X")
    state = audit.infer_current_state(history)
    assert state["state"] == "triaged_awaiting_faq_check"
    assert audit.list_valid_next_actions(state) == ["check-faq-resolution"]


def test_state_after_faq_match_recommends_draft_faq(tmp_path: Path) -> None:
    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _seed_triage(out)
    _seed_faq(out, match=True)
    history = audit.load_ticket_history(data, out, "TKT-X")
    state = audit.infer_current_state(history)
    assert state["state"] == "faq_checked_awaiting_decision"
    assert audit.list_valid_next_actions(state) == ["draft-faq-response"]


def test_state_after_faq_no_match_recommends_escalate(tmp_path: Path) -> None:
    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _seed_triage(out)
    _seed_faq(out, match=False)
    history = audit.load_ticket_history(data, out, "TKT-X")
    state = audit.infer_current_state(history)
    assert audit.list_valid_next_actions(state) == ["escalate-to-specialist"]


def test_state_response_drafted_but_not_sent_recommends_send(tmp_path: Path) -> None:
    """In live mode, a draft alone is not yet 'sent' — the next valid step is send-customer-response."""
    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _seed_triage(out)
    _seed_faq(out, match=True)
    _seed_response(out)
    history = audit.load_ticket_history(data, out, "TKT-X", mode="live")
    state = audit.infer_current_state(history)
    assert state["state"] == "response_drafted_awaiting_send"
    assert audit.list_valid_next_actions(state) == ["send-customer-response"]


def test_state_response_sent_recommends_verify_feedback(tmp_path: Path) -> None:
    """Once a sent_messages row exists, audit recommends verify-feedback."""
    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _seed_triage(out)
    _seed_faq(out, match=True)
    _seed_response(out)
    pl.DataFrame(
        [
            {
                "delivery_id": "DEL-X",
                "ticket_id": "TKT-X",
                "message_id": "MSG-X",
                "sent_at": "2026-04-30T09:50:00+00:00",
                "channel": "email",
                "recipient_email": "u@x",
                "delivery_status": "delivered",
                "skill_name": "send-customer-response",
                "workflow_run_id": "wf-x",
                "step_id": "step-x",
                "inputs_used": "x",
                "decision_summary": "x",
            }
        ]
    ).write_csv(out / "sent_messages.csv")
    history = audit.load_ticket_history(data, out, "TKT-X", mode="live")
    state = audit.infer_current_state(history)
    assert state["state"] == "response_sent_awaiting_customer"
    actions = audit.list_valid_next_actions(state)
    assert any("verify-feedback" in a for a in actions)


def test_state_closed_returns_no_next_action(tmp_path: Path) -> None:
    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _seed_triage(out)
    _seed_faq(out, match=True)
    _seed_response(out)
    _seed_feedback_close(out)
    history = audit.load_ticket_history(data, out, "TKT-X")
    state = audit.infer_current_state(history)
    assert state["state"] == "closed"
    assert audit.list_valid_next_actions(state) == []


def test_build_audit_report_includes_timeline_and_next(tmp_path: Path) -> None:
    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _seed_triage(out)
    history = audit.load_ticket_history(data, out, "TKT-X")
    state = audit.infer_current_state(history)
    report = audit.build_audit_report(history, state)
    assert "TKT-X" in report
    assert "Timeline:" in report
    assert "classify-prioritize-ticket" in report
    assert "check-faq-resolution" in report  # the recommended next action


def test_main_happy_path_writes_action_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    rc = audit.main(["--ticket-id", "TKT-X", "--data-dir", str(data), "--out-dir", str(out)])
    assert rc == 0
    log = out / "ticket_action_log.csv"
    assert log.exists()
    with log.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    data_row = dict(zip(header, rows[1]))
    assert data_row["ticket_id"] == "TKT-X"
    assert data_row["skill_name"] == "audit-ticket-process"
    assert data_row["action"].startswith("audit:")
    assert data_row["workflow_run_id"]
    assert data_row["step_id"]


def test_main_emits_json_envelope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    data = _isolated_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _seed_triage(out)
    rc = audit.main(
        [
            "--ticket-id",
            "TKT-X",
            "--data-dir",
            str(data),
            "--out-dir",
            str(out),
            "--json",
        ]
    )
    assert rc == 0
    env = json.loads(capsys.readouterr().out.strip())
    assert env["status"] == "ok"
    assert env["skill_name"] == "audit-ticket-process"
    assert env["next_action"] == "check-faq-resolution"
    assert env["outputs"]["state"] == "triaged_awaiting_faq_check"
    assert env["outputs"]["valid_next_actions"]


def test_main_live_mode_does_not_use_processed_history(tmp_path: Path) -> None:
    """In live mode the audit must not include rows from data/processed/."""
    out = tmp_path / "out"
    out.mkdir()
    history_live = audit.load_ticket_history(DATA_DIR, out, "TKT-00042", mode="live")
    history_demo = audit.load_ticket_history(DATA_DIR, out, "TKT-00042", mode="demo")
    # In live mode every historical bucket is empty.
    for bucket in history_live["historical"].values():
        assert bucket == []
    # In demo mode, the dataset's TKT-00042 rows surface.
    assert any(history_demo["historical"][k] for k in history_demo["historical"])


def test_main_against_real_data_for_existing_ticket(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The synthetic dataset has TKT-00042 fully closed in processed/. Audit should agree."""
    rc = audit.main(["--ticket-id", "TKT-00042", "--data-dir", str(DATA_DIR), "--out-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TKT-00042" in out
    assert "Timeline" in out


def test_main_missing_ticket_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = audit.main(["--ticket-id", "TKT-99999", "--data-dir", str(DATA_DIR), "--out-dir", str(tmp_path)])
    assert rc == 2
    assert "TKT-99999" in capsys.readouterr().err
