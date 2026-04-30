"""Tests for the draft-faq-response skill."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import polars as pl
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "skills" / "draft-faq-response" / "scripts" / "draft_faq_response.py"
_spec = importlib.util.spec_from_file_location("draft_faq_response", _MODULE_PATH)
assert _spec and _spec.loader
dfr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dfr)

DATA_DIR = _REPO_ROOT / "data"
SAMPLE_TICKET_ID = "TKT-00042"
SAMPLE_FAQ_ID = "FAQ-001"


def _seed_faq_decision(
    out_dir: Path,
    ticket_id: str = SAMPLE_TICKET_ID,
    faq_id: str = SAMPLE_FAQ_ID,
    faq_match_found: bool = True,
    recommended_next_step: str = "draft-faq-response",
    match_confidence: float = 0.85,
) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": ticket_id,
                "created_at": "2026-04-30T10:00:00+00:00",
                "skill_name": "check-faq-resolution",
                "faq_match_found": faq_match_found,
                "faq_id": faq_id if faq_match_found else "",
                "match_confidence": match_confidence,
                "search_terms": "x",
                "candidate_faq_ids": faq_id,
                "required_customer_info_available": True,
                "faq_application_reason": "test",
                "recommended_next_step": recommended_next_step,
                "inputs_used": "x",
                "decision_summary": "test",
            }
        ]
    ).write_csv(out_dir / "faq_decisions.csv")


def test_load_response_context_happy(tmp_path: Path) -> None:
    _seed_faq_decision(tmp_path)
    ctx = dfr.load_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert ctx["ticket"]["ticket_id"] == SAMPLE_TICKET_ID
    assert ctx["faq"]["faq_id"] == SAMPLE_FAQ_ID
    assert ctx["faq_decision"]["faq_match_found"] in {"true", True}


def test_load_response_context_no_faq_decision_raises(tmp_path: Path) -> None:
    with pytest.raises(LookupError) as exc:
        dfr.load_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "check-faq-resolution" in str(exc.value)


def test_load_response_context_no_match_raises(tmp_path: Path) -> None:
    _seed_faq_decision(tmp_path, faq_match_found=False)
    with pytest.raises(LookupError) as exc:
        dfr.load_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "escalate-to-specialist" in str(exc.value)


def test_load_response_context_wrong_recommended_step_raises(tmp_path: Path) -> None:
    _seed_faq_decision(tmp_path, recommended_next_step="escalate-to-specialist")
    with pytest.raises(LookupError) as exc:
        dfr.load_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "Refusing" in str(exc.value)


def test_load_response_context_unknown_faq_id_raises(tmp_path: Path) -> None:
    _seed_faq_decision(tmp_path, faq_id="FAQ-DOES-NOT-EXIST")
    with pytest.raises(LookupError) as exc:
        dfr.load_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    assert "FAQ-DOES-NOT-EXIST" in str(exc.value)


def test_draft_faq_response_includes_steps_and_followup(tmp_path: Path) -> None:
    _seed_faq_decision(tmp_path)
    ctx = dfr.load_response_context(DATA_DIR, tmp_path, SAMPLE_TICKET_ID)
    draft = dfr.draft_faq_response(ctx)
    assert ctx["faq"]["solution_steps"].split(".")[0].strip() in draft["sent_text"]
    assert draft["follow_up_request"]
    # No internal language leaked into customer-facing text
    for forbidden in ("specialist_notes", "credentials", "internal note"):
        assert forbidden not in draft["sent_text"].lower()


def test_quality_check_flags_missing_fields() -> None:
    bad = {
        "draft_text": "hi",
        "sent_text": "hi",
        "customer_action_required": "",
        "follow_up_request": "",
        "included_context": "",
    }
    qc = dfr.quality_check_response(bad)
    assert qc["ok"] is False
    assert "customer_action_required" in qc["notes"]
    assert "follow_up_request" in qc["notes"]
    assert "actionable steps" in qc["notes"]


def test_quality_check_flags_internal_language() -> None:
    bad = {
        "draft_text": "x",
        "sent_text": "Try these specialist log lines from the credential dump.",
        "customer_action_required": "Try x",
        "follow_up_request": "Reply",
        "included_context": "x",
    }
    qc = dfr.quality_check_response(bad)
    assert qc["ok"] is False
    assert "internal language" in qc["notes"]


def test_main_happy_path_writes_drafts_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_faq_decision(tmp_path)
    rc = dfr.main(
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
    drafts = tmp_path / "customer_response_drafts.csv"
    assert drafts.exists()
    with drafts.open() as f:
        rows = list(csv.reader(f))
    header = rows[0]
    assert "message_source" in header
    assert rows[1][header.index("message_source")] == "faq"
    assert rows[1][header.index("ticket_id")] == SAMPLE_TICKET_ID
    out = capsys.readouterr().out
    assert "FAQ response drafted" in out


def test_main_no_faq_decision_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = dfr.main(
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
    assert "check-faq-resolution" in err


def test_main_no_match_returns_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_faq_decision(tmp_path, faq_match_found=False)
    rc = dfr.main(
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
