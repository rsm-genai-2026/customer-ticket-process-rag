"""Tests for the send-customer-response skill."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "send-customer-response" / "scripts" / "send_customer_response.py"
_spec = importlib.util.spec_from_file_location("send_customer_response", _MODULE_PATH)
assert _spec and _spec.loader
scr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scr)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"


def _seed_draft(
    out_dir: Path,
    *,
    ticket_id: str = SAMPLE_TICKET_ID,
    workflow_run_id: str = "wf-test",
) -> None:
    pl.DataFrame(
        [
            {
                "message_id": f"MSG-{ticket_id}-test",
                "ticket_id": ticket_id,
                "created_at": "2026-04-30T10:00:00+00:00",
                "skill_name": "draft-faq-response",
                "workflow_run_id": workflow_run_id,
                "step_id": "step-draft",
                "message_source": "faq",
                "draft_text": "Try X and confirm.",
                "sent_text": "Try X and confirm.",
                "customer_action_required": "Try X and reply.",
                "included_context": "x",
                "follow_up_request": "Reply please",
                "quality_check_notes": "",
                "inputs_used": "x",
                "decision_summary": "x",
                "confidence_score": 0.9,
            }
        ]
    ).write_csv(out_dir / "customer_response_drafts.csv")


def test_load_send_context_happy(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    ctx = scr.load_send_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert ctx["ticket"]["ticket_id"] == SAMPLE_TICKET_ID
    assert ctx["draft"]["message_source"] == "faq"


def test_load_send_context_no_draft_raises(tmp_path: Path) -> None:
    with pytest.raises(LookupError) as exc:
        scr.load_send_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "draft-faq-response" in str(exc.value) or "draft-specialist-response" in str(exc.value)


def test_build_send_record_uses_ticket_email(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    ctx = scr.load_send_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    record = scr.build_send_record(ctx, channel="email", workflow_run_id="wf-1", step_id="step-1")
    assert record["delivery_id"].startswith(f"DEL-{SAMPLE_TICKET_ID}-")
    assert record["channel"] == "email"
    assert record["recipient_email"] == ctx["ticket"]["submitted_by_email"]
    assert record["delivery_status"] == "delivered"
    assert record["message_id"] == ctx["draft"]["message_id"]


def test_main_happy_path_writes_sent_messages(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_draft(tmp_path)
    rc = scr.main(
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
    sent = tmp_path / "sent_messages.csv"
    assert sent.exists()
    with sent.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    for col in (
        "delivery_id",
        "ticket_id",
        "message_id",
        "channel",
        "recipient_email",
        "sent_at",
        "delivery_status",
        "workflow_run_id",
        "step_id",
    ):
        assert col in header, col
    out = capsys.readouterr().out
    assert "Sent response for" in out
    assert "verify-feedback-close-or-reopen" in out


def test_main_missing_draft_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = scr.main(
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
    assert "draft-faq-response" in err or "draft-specialist-response" in err


def test_main_missing_ticket_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = scr.main(
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


def test_main_idempotent_does_not_double_send(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_draft(tmp_path, workflow_run_id="wf-once")
    args = [
        "--ticket-id",
        SAMPLE_TICKET_ID,
        "--data-dir",
        str(DATA_DIR),
        "--out-dir",
        str(tmp_path),
        "--workflow-run-id",
        "wf-once",
        "--step-id",
        "step-once",
        "--json",
    ]
    assert scr.main(args) == 0
    capsys.readouterr()
    assert scr.main(args) == 0
    env = json.loads(capsys.readouterr().out.strip())
    assert env["status"] == "skipped"
    df = pl.read_csv(tmp_path / "sent_messages.csv")
    assert df.height == 1


def test_main_replace_rewrites_existing_send_row(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_draft(tmp_path, workflow_run_id="wf-replace")
    args = [
        "--ticket-id",
        SAMPLE_TICKET_ID,
        "--data-dir",
        str(DATA_DIR),
        "--out-dir",
        str(tmp_path),
        "--workflow-run-id",
        "wf-replace",
        "--step-id",
        "step-replace",
        "--json",
    ]
    assert scr.main(args) == 0
    capsys.readouterr()
    assert scr.main(args + ["--idempotency-mode", "replace"]) == 0
    df = pl.read_csv(tmp_path / "sent_messages.csv")
    assert df.filter(pl.col("workflow_run_id") == "wf-replace").height == 1


def test_main_json_envelope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_draft(tmp_path, workflow_run_id="wf-send")
    rc = scr.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
            "--workflow-run-id",
            "wf-send",
            "--step-id",
            "step-send",
            "--json",
            "--channel",
            "email",
        ]
    )
    assert rc == 0
    env = json.loads(capsys.readouterr().out.strip())
    assert env["status"] == "ok"
    assert env["skill_name"] == "send-customer-response"
    assert env["next_action"] == "verify-feedback-close-or-reopen"
    assert env["outputs"]["delivery_id"].startswith("DEL-")
    assert env["outputs"]["channel"] == "email"
    assert env["outputs"]["delivery_status"] == "delivered"


def test_main_invalid_channel_rejected(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    with pytest.raises(SystemExit):
        scr.main(
            [
                "--ticket-id",
                SAMPLE_TICKET_ID,
                "--data-dir",
                str(DATA_DIR),
                "--out-dir",
                str(tmp_path),
                "--channel",
                "carrier-pigeon",
            ]
        )
