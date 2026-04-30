"""Tests for the verify-feedback-close-or-reopen skill."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "verify-feedback-close-or-reopen" / "scripts" / "verify_feedback.py"
_spec = importlib.util.spec_from_file_location("verify_feedback", _MODULE_PATH)
assert _spec and _spec.loader
vf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vf)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"


def _seed_draft(out_dir: Path, ticket_id: str = SAMPLE_TICKET_ID) -> None:
    pl.DataFrame(
        [
            {
                "message_id": f"MSG-{ticket_id}-test",
                "ticket_id": ticket_id,
                "created_at": "2026-04-30T13:00:00+00:00",
                "skill_name": "draft-faq-response",
                "message_source": "faq",
                "draft_text": "x",
                "sent_text": "x",
                "customer_action_required": "Try and confirm",
                "included_context": "x",
                "follow_up_request": "Reply please",
                "quality_check_notes": "",
                "inputs_used": "x",
                "decision_summary": "x",
                "confidence_score": 0.85,
            }
        ]
    ).write_csv(out_dir / "customer_response_drafts.csv")


def _seed_prior_feedback(out_dir: Path, ticket_id: str = SAMPLE_TICKET_ID, reopened: bool = True) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": ticket_id,
                "created_at": "2026-04-30T14:00:00+00:00",
                "skill_name": "verify-feedback-close-or-reopen",
                "resolution_accepted": False,
                "customer_feedback_text": "still broken",
                "rejection_reason": "x",
                "verified_rejection": True,
                "reopened_flag": reopened,
                "verified_by_it_member_id": "IT-001",
                "verification_notes": "x",
                "next_action": "reopen_and_escalate" if reopened else "close_unresolved_vendor_followup",
                "closure_reason": "" if reopened else "x",
                "inputs_used": "x",
                "decision_summary": "x",
            }
        ]
    ).write_csv(out_dir / "feedback_decisions.csv")


def test_classify_feedback_positive() -> None:
    out = vf.classify_feedback("Thanks, that fixed it!")
    assert out["sentiment"] == "positive"
    assert "fixed" in out["evidence"]


def test_classify_feedback_negative() -> None:
    out = vf.classify_feedback("Tried it but still not working.")
    assert out["sentiment"] == "negative"
    assert "still not" in out["evidence"] or "not working" in out["evidence"]


def test_classify_feedback_polite_rejection_treated_negative() -> None:
    # "Thanks for trying" but problem persists
    out = vf.classify_feedback("Thanks for trying but it's still broken.")
    assert out["sentiment"] == "negative"


def test_classify_feedback_ambiguous_returns_ambiguous() -> None:
    out = vf.classify_feedback("Got it, will let you know soon.")
    assert out["sentiment"] == "ambiguous"


def test_classify_feedback_empty_raises() -> None:
    with pytest.raises(ValueError):
        vf.classify_feedback("")
    with pytest.raises(ValueError):
        vf.classify_feedback("   ")


def test_load_feedback_context_no_draft_raises(tmp_path: Path) -> None:
    with pytest.raises(LookupError):
        vf.load_feedback_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID, "thanks")


def test_decide_next_action_positive_closes(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    ctx = vf.load_feedback_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID, "thanks, fixed!")
    decision = vf.decide_next_action(ctx, {"sentiment": "positive", "evidence": "fixed"})
    assert decision["resolution_accepted"] is True
    assert decision["next_action"] == "close_ticket"
    assert decision["reopened_flag"] is False


def test_decide_next_action_first_negative_reopens(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    ctx = vf.load_feedback_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID, "tried it, still broken")
    decision = vf.decide_next_action(ctx, {"sentiment": "negative", "evidence": "still broken"})
    assert decision["next_action"] == "reopen_and_escalate"
    assert decision["reopened_flag"] is True


def test_decide_next_action_second_negative_closes_unresolved(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    _seed_prior_feedback(tmp_path, reopened=True)
    ctx = vf.load_feedback_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID, "still not working")
    decision = vf.decide_next_action(ctx, {"sentiment": "negative", "evidence": "still not"})
    assert decision["next_action"] == "close_unresolved_vendor_followup"
    assert decision["reopened_flag"] is False


def test_decide_next_action_ambiguous_requests_clarification(tmp_path: Path) -> None:
    _seed_draft(tmp_path)
    ctx = vf.load_feedback_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID, "ok, will get back to you")
    decision = vf.decide_next_action(ctx, {"sentiment": "ambiguous", "evidence": ""})
    assert decision["next_action"] == "request_clarification"


def test_main_happy_path_positive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_draft(tmp_path)
    rc = vf.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--feedback-text",
            "Thanks, that fixed it!",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    fb = tmp_path / "feedback_decisions.csv"
    assert fb.exists()
    with fb.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    assert rows[1][header.index("next_action")] == "close_ticket"
    assert "audit-ticket-process" in capsys.readouterr().out


def test_main_empty_feedback_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_draft(tmp_path)
    rc = vf.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--feedback-text",
            "   ",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 2
    assert "must not be empty" in capsys.readouterr().err


def test_main_no_draft_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = vf.main(
        [
            "--ticket-id",
            SAMPLE_TICKET_ID,
            "--feedback-text",
            "thanks",
            "--data-dir",
            str(DATA_DIR),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 3
    assert "draft-faq-response" in capsys.readouterr().err
