"""Tests for the approve-faq-promotion automation (the FAQ HITL gate)."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "automations" / "approve-faq-promotion" / "scripts" / "approve_faq_promotion.py"
_spec = importlib.util.spec_from_file_location("approve_faq_promotion", _MODULE_PATH)
assert _spec and _spec.loader
afp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(afp)

SAMPLE_TICKET_ID = "TKT-PROMO-01"
RUN_ID = "wf-test"

FAQ_KB_HEADER = (
    "faq_id,category,system_name,issue_pattern,symptoms,solution_steps,"
    "required_customer_info,last_updated,owner,active_flag\n"
)


def _make_per_run_data_dir(tmp_path: Path, *, seed_existing_faq_id: str | None = None) -> Path:
    """Build a minimal per-run data directory (matches what the orchestrator copies)."""

    data_dir = tmp_path / "data"
    (data_dir / "raw").mkdir(parents=True)
    rows = FAQ_KB_HEADER
    if seed_existing_faq_id:
        rows += f"{seed_existing_faq_id},other,System X,issue_x,sym,sol,info,2024-01-01,someone,true\n"
    (data_dir / "raw" / "faq_knowledge_base.csv").write_text(rows, encoding="utf-8")
    return data_dir


def _seed_candidate(out_dir: Path, *, ticket_id: str = SAMPLE_TICKET_ID) -> None:
    pl.DataFrame(
        [
            {
                "ticket_id": ticket_id,
                "created_at": "2026-04-30T14:30:00+00:00",
                "skill_name": "draft-faq-candidate",
                "source_solution_skill_name": "investigate-specialist-solution",
                "category": "login_access",
                "system_name": "Customer Portal",
                "issue_pattern": "candidate_issue",
                "symptoms": "sym 1|sym 2",
                "solution_steps": "step 1|step 2",
                "required_customer_info": "browser|os",
                "confidence": 0.78,
                "reasoning": "LLM rationale.",
                "workflow_run_id": RUN_ID,
                "step_id": "step-candidate",
                "inputs_used": "x",
                "decision_summary": "x",
            }
        ]
    ).write_csv(out_dir / "faq_candidates.csv")


def _run_main(data_dir: Path, out_dir: Path, *extra: str, ticket_id: str = SAMPLE_TICKET_ID) -> tuple[int, dict]:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = afp.main(
            [
                "--ticket-id",
                ticket_id,
                "--data-dir",
                str(data_dir),
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
    data_dir = _make_per_run_data_dir(tmp_path)
    out_dir = tmp_path / "working"
    out_dir.mkdir()
    _seed_candidate(out_dir)
    rc, env = _run_main(data_dir, out_dir)
    assert rc == 0
    assert env["status"] == "awaiting_input"
    assert env["next_action"] == "approve-faq-promotion"
    cand = env["outputs"]["candidate"]
    assert cand["category"] == "login_access"
    # No decision row written yet.
    assert not (out_dir / "faq_promotion_decisions.csv").exists()
    # FAQ KB not mutated.
    body = (data_dir / "raw" / "faq_knowledge_base.csv").read_text()
    assert body == FAQ_KB_HEADER


def test_missing_candidate_errors(tmp_path: Path) -> None:
    data_dir = _make_per_run_data_dir(tmp_path)
    out_dir = tmp_path / "working"
    out_dir.mkdir()
    rc, env = _run_main(data_dir, out_dir)
    assert rc == 3
    assert env["status"] == "error"
    assert env["error"]["code"] == "missing_upstream"


def test_approve_appends_to_faq_kb(tmp_path: Path) -> None:
    data_dir = _make_per_run_data_dir(tmp_path)
    out_dir = tmp_path / "working"
    out_dir.mkdir()
    _seed_candidate(out_dir)
    rc, env = _run_main(data_dir, out_dir, "--decision", "approve")
    assert rc == 0
    assert env["status"] == "ok"
    assert env["next_action"] == "audit-ticket-process"
    assert env["outputs"]["decision"] == "approve"
    new_faq_id = env["outputs"]["new_faq_id"]
    assert new_faq_id.startswith("FAQ-")

    faq_rows = list(csv.DictReader((data_dir / "raw" / "faq_knowledge_base.csv").open(newline="")))
    assert len(faq_rows) == 1
    appended = faq_rows[0]
    assert appended["faq_id"] == new_faq_id
    assert appended["category"] == "login_access"
    assert appended["solution_steps"] == "step 1|step 2"
    assert appended["owner"] == "workflow_promotion"
    assert appended["active_flag"] == "true"

    decisions = list(csv.DictReader((out_dir / "faq_promotion_decisions.csv").open(newline="")))
    assert decisions[0]["decision"] == "approve"
    assert decisions[0]["new_faq_id"] == new_faq_id
    assert decisions[0]["edited"] == "false"


def test_approve_with_overrides_writes_edited_fields(tmp_path: Path) -> None:
    data_dir = _make_per_run_data_dir(tmp_path)
    out_dir = tmp_path / "working"
    out_dir.mkdir()
    _seed_candidate(out_dir)
    overrides = {
        "issue_pattern": "human_edited_pattern",
        "symptoms": ["edited sym a", "edited sym b"],
    }
    rc, env = _run_main(
        data_dir,
        out_dir,
        "--decision",
        "approve",
        "--candidate-json",
        json.dumps(overrides),
    )
    assert rc == 0
    assert env["outputs"]["edited"] is True

    faq_rows = list(csv.DictReader((data_dir / "raw" / "faq_knowledge_base.csv").open(newline="")))
    appended = faq_rows[0]
    assert appended["issue_pattern"] == "human_edited_pattern"
    assert appended["symptoms"] == "edited sym a|edited sym b"
    # Non-overridden fields keep the candidate's values.
    assert appended["category"] == "login_access"


def test_skip_writes_decision_but_no_faq_row(tmp_path: Path) -> None:
    data_dir = _make_per_run_data_dir(tmp_path)
    out_dir = tmp_path / "working"
    out_dir.mkdir()
    _seed_candidate(out_dir)
    rc, env = _run_main(data_dir, out_dir, "--decision", "skip", "--reviewer-notes", "Too customer-specific")
    assert rc == 0
    assert env["next_action"] == "audit-ticket-process"
    assert env["outputs"]["decision"] == "skip"
    assert env["outputs"]["new_faq_id"] == ""

    body = (data_dir / "raw" / "faq_knowledge_base.csv").read_text()
    assert body == FAQ_KB_HEADER

    decisions = list(csv.DictReader((out_dir / "faq_promotion_decisions.csv").open(newline="")))
    assert decisions[0]["decision"] == "skip"
    assert decisions[0]["new_faq_id"] == ""
    assert decisions[0]["reviewer_notes"] == "Too customer-specific"


def test_invalid_candidate_json_errors(tmp_path: Path) -> None:
    data_dir = _make_per_run_data_dir(tmp_path)
    out_dir = tmp_path / "working"
    out_dir.mkdir()
    _seed_candidate(out_dir)
    rc, env = _run_main(data_dir, out_dir, "--decision", "approve", "--candidate-json", "not json")
    assert rc == 2
    assert env["status"] == "error"
    assert env["error"]["code"] == "invalid_input"


def test_next_faq_id_avoids_collisions(tmp_path: Path) -> None:
    data_dir = _make_per_run_data_dir(tmp_path, seed_existing_faq_id="FAQ-TKTPROMO01")
    out_dir = tmp_path / "working"
    out_dir.mkdir()
    _seed_candidate(out_dir)
    rc, env = _run_main(data_dir, out_dir, "--decision", "approve")
    assert rc == 0
    # Existing id was FAQ-TKTPROMO01 so the new one must be different.
    assert env["outputs"]["new_faq_id"] == "FAQ-TKTPROMO01-2"
