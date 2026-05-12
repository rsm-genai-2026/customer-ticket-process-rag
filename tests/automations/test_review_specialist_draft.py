"""Tests for the review-specialist-draft automation (the HITL gate)."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "automations" / "review-specialist-draft" / "scripts" / "review_specialist_draft.py"
_spec = importlib.util.spec_from_file_location("review_specialist_draft", _MODULE_PATH)
assert _spec and _spec.loader
rsd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rsd)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"
RUN_ID = "wf-test"


def _seed_draft(out_dir: Path, *, sent_text: str = "Original draft body.") -> None:
    pl.DataFrame(
        [
            {
                "message_id": f"MSG-{SAMPLE_TICKET_ID}-spec",
                "ticket_id": SAMPLE_TICKET_ID,
                "created_at": "2026-04-30T13:00:00+00:00",
                "skill_name": "draft-specialist-response",
                "workflow_run_id": RUN_ID,
                "step_id": "step-draft",
                "message_source": "specialist_solution",
                "draft_text": sent_text,
                "sent_text": sent_text,
                "customer_action_required": "Try and confirm.",
                "included_context": "x",
                "follow_up_request": "Reply please",
                "quality_check_notes": "",
                "inputs_used": "x",
                "decision_summary": "x",
                "confidence_score": 0.85,
            }
        ]
    ).write_csv(out_dir / "customer_response_drafts.csv")


def _run_main(out_dir: Path, *extra: str) -> tuple[int, dict]:
    """Run the script's main() and return (return_code, parsed envelope)."""

    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = rsd.main(
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


def test_awaiting_mode_emits_envelope_without_writing(tmp_path: Path) -> None:
    _seed_draft(tmp_path, sent_text="Hi customer, here's the fix...")
    rc, env = _run_main(tmp_path)
    assert rc == 0
    assert env["status"] == "awaiting_input"
    assert env["next_action"] == "review-specialist-draft"
    assert env["outputs"]["draft_text"] == "Hi customer, here's the fix..."
    # No decision rows written yet.
    assert not (tmp_path / "specialist_draft_reviews.csv").exists()


def test_missing_upstream_draft_errors(tmp_path: Path) -> None:
    rc, env = _run_main(tmp_path)
    assert rc == 3
    assert env["status"] == "error"
    assert env["error"]["code"] == "missing_upstream"


def test_approve_writes_decision_and_routes_to_send(tmp_path: Path) -> None:
    _seed_draft(tmp_path, sent_text="Hello, here is the fix.")
    rc, env = _run_main(tmp_path, "--decision", "approve")
    assert rc == 0
    assert env["status"] == "ok"
    assert env["next_action"] == "send-customer-response"
    assert env["outputs"]["decision"] == "approve"
    assert env["outputs"]["forced_approve"] is False

    reviews = (tmp_path / "specialist_draft_reviews.csv").read_text().splitlines()
    rows = list(csv.DictReader([reviews[0], reviews[1]]))
    assert rows[0]["decision"] == "approve"
    assert rows[0]["original_text"] == "Hello, here is the fix."


def test_approve_with_edit_patches_sent_text(tmp_path: Path) -> None:
    _seed_draft(tmp_path, sent_text="Original text.")
    rc, env = _run_main(tmp_path, "--decision", "approve", "--edited-text", "Edited text.")
    assert rc == 0
    assert env["outputs"]["edited"] is True

    drafts = list(csv.DictReader((tmp_path / "customer_response_drafts.csv").open(newline="")))
    assert drafts[0]["sent_text"] == "Edited text."
    # draft_text should be untouched (audit trail of the LLM's original output).
    assert drafts[0]["draft_text"] == "Original text."


def test_reject_first_time_routes_to_investigate(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    rc, env = _run_main(tmp_path, "--decision", "reject", "--reviewer-notes", "The wording is too internal.")
    assert rc == 0
    assert env["next_action"] == "investigate-specialist-solution"
    assert env["outputs"]["decision"] == "reject"
    assert env["outputs"]["forced_approve"] is False
    rows = list(csv.DictReader((tmp_path / "specialist_draft_reviews.csv").open(newline="")))
    assert rows[0]["decision"] == "reject"


def test_reject_second_time_force_approves(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    # First reject
    _run_main(tmp_path, "--decision", "reject")
    # Second reject — should be forced through as approve.
    rc, env = _run_main(tmp_path, "--decision", "reject")
    assert rc == 0
    assert env["next_action"] == "send-customer-response"
    assert env["outputs"]["forced_approve"] is True
    assert env["outputs"]["decision"] == "approve"
    rows = list(csv.DictReader((tmp_path / "specialist_draft_reviews.csv").open(newline="")))
    # Second row should be the forced approve.
    assert rows[1]["decision"] == "approve"
    assert rows[1]["forced_approve"] == "true"


def test_prior_reject_count_helper(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    assert rsd._prior_reject_count(tmp_path, SAMPLE_TICKET_ID, RUN_ID) == 0
    _run_main(tmp_path, "--decision", "reject")
    assert rsd._prior_reject_count(tmp_path, SAMPLE_TICKET_ID, RUN_ID) == 1
