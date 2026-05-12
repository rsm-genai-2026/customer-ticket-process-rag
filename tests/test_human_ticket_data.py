"""End-to-end test: run the human-ticketing generator into a temp directory,
then run the validator against that output and check key invariants."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "data" / "generate_human_ticket_data.py"
VALIDATOR = REPO_ROOT / "data" / "validate_human_ticket_data.py"


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_dir = tmp_path_factory.mktemp("human_tickets")
    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--n-tickets",
            "120",
            "--seed",
            "777",
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"generator failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    return out_dir


def test_generator_creates_expected_files(generated: Path) -> None:
    expected = [
        "raw/customers.csv",
        "raw/it_team_members.csv",
        "raw/it_specialists.csv",
        "raw/faq_knowledge_base.csv",
        "raw/submitted_tickets.csv",
        "processed/ticket_triage.csv",
        "processed/faq_checks.csv",
        "processed/specialist_escalations.csv",
        "processed/specialist_investigations.csv",
        "processed/customer_messages.csv",
        "processed/resolution_feedback.csv",
        "processed/ticket_lifecycle_events.csv",
        "processed/ticket_summary.csv",
        "dictionaries/categories.csv",
        "dictionaries/priority_rules.csv",
        "dictionaries/systems.csv",
        "dictionaries/status_codes.csv",
    ]
    for rel in expected:
        assert (generated / rel).exists(), f"missing: {rel}"


def test_validator_passes_on_generated_output(generated: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--data-dir", str(generated)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"validator reported failures:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_row_counts_consistent(generated: Path) -> None:
    submitted = pl.read_csv(generated / "raw" / "submitted_tickets.csv")
    triage = pl.read_csv(generated / "processed" / "ticket_triage.csv")
    faq_checks = pl.read_csv(generated / "processed" / "faq_checks.csv")
    summary = pl.read_csv(generated / "processed" / "ticket_summary.csv")
    n = submitted.height
    assert n == 120
    assert triage.height == n
    assert faq_checks.height == n
    assert summary.height == n


def test_workflow_detail_fields_are_populated(generated: Path) -> None:
    submitted = pl.read_csv(generated / "raw" / "submitted_tickets.csv")
    triage = pl.read_csv(generated / "processed" / "ticket_triage.csv")
    faq_checks = pl.read_csv(generated / "processed" / "faq_checks.csv")
    escalations = pl.read_csv(generated / "processed" / "specialist_escalations.csv")
    investigations = pl.read_csv(generated / "processed" / "specialist_investigations.csv")
    messages = pl.read_csv(generated / "processed" / "customer_messages.csv")
    feedback = pl.read_csv(generated / "processed" / "resolution_feedback.csv")
    summary = pl.read_csv(generated / "processed" / "ticket_summary.csv")

    required = {
        "submitted": (
            submitted,
            ["error_or_symptom_detail", "steps_already_tried", "expected_outcome", "availability_window"],
        ),
        "triage": (
            triage,
            [
                "intake_summary",
                "classification_evidence",
                "recommended_specialist_group",
                "target_first_response_at",
                "target_resolution_at",
            ],
        ),
        "faq_checks": (
            faq_checks,
            ["search_terms", "candidate_faq_ids", "faq_application_reason"],
        ),
        "escalations": (
            escalations,
            ["requested_specialist_group", "handoff_summary", "specific_question_for_specialist"],
        ),
        "investigations": (
            investigations,
            ["diagnostic_steps", "evidence_reviewed", "customer_action_required", "confidence_score"],
        ),
        "messages": (
            messages,
            ["customer_action_required", "included_context", "follow_up_request", "quality_check_notes"],
        ),
        "feedback": (
            feedback,
            ["verification_notes", "next_action", "closure_reason"],
        ),
        "summary": (summary, ["true_category", "first_resolution_source"]),
    }

    for table_name, (df, columns) in required.items():
        for column in columns:
            assert column in df.columns, f"{table_name} missing {column}"
            if df.schema[column] == pl.String:
                assert df.filter(pl.col(column).is_null() | (pl.col(column).str.len_chars() == 0)).height == 0, (
                    table_name,
                    column,
                )


def test_rates_in_plausible_range(generated: Path) -> None:
    summary = pl.read_csv(generated / "processed" / "ticket_summary.csv")
    n = summary.height
    faq_rate = summary["faq_match_found"].sum() / n
    esc_rate = summary["escalated_flag"].sum() / n
    acc_rate = summary["resolution_accepted"].sum() / n
    reopen_rate = summary["reopened_flag"].sum() / n
    assert 0.35 <= faq_rate <= 0.70, faq_rate
    assert 0.25 <= esc_rate <= 0.60, esc_rate
    assert 0.70 <= acc_rate <= 0.95, acc_rate
    assert 0.05 <= reopen_rate <= 0.25, reopen_rate


def test_timestamps_ordered_for_all_tickets(generated: Path) -> None:
    submitted = pl.read_csv(generated / "raw" / "submitted_tickets.csv")
    triage = pl.read_csv(generated / "processed" / "ticket_triage.csv")
    faq_checks = pl.read_csv(generated / "processed" / "faq_checks.csv")
    feedback = pl.read_csv(generated / "processed" / "resolution_feedback.csv")
    messages = pl.read_csv(generated / "processed" / "customer_messages.csv")

    sub_map = {r["ticket_id"]: datetime.fromisoformat(r["submitted_at"]) for r in submitted.iter_rows(named=True)}
    tri_map = {r["ticket_id"]: datetime.fromisoformat(r["triaged_at"]) for r in triage.iter_rows(named=True)}
    faq_map = {r["ticket_id"]: datetime.fromisoformat(r["faq_checked_at"]) for r in faq_checks.iter_rows(named=True)}

    for tid, sub in sub_map.items():
        assert sub < tri_map[tid] < faq_map[tid]

    # First message after FAQ check; first customer reply after first message
    msg_first: dict[str, datetime] = {}
    for r in messages.iter_rows(named=True):
        ts = datetime.fromisoformat(r["sent_at"])
        if r["ticket_id"] not in msg_first or ts < msg_first[r["ticket_id"]]:
            msg_first[r["ticket_id"]] = ts
    fb_first: dict[str, datetime] = {}
    for r in feedback.iter_rows(named=True):
        ts = datetime.fromisoformat(r["customer_reply_at"])
        if r["ticket_id"] not in fb_first or ts < fb_first[r["ticket_id"]]:
            fb_first[r["ticket_id"]] = ts

    for tid, faq_ts in faq_map.items():
        assert msg_first[tid] > faq_ts, tid
        assert fb_first[tid] > msg_first[tid], tid


def test_event_log_has_minimum_events_per_ticket(generated: Path) -> None:
    events = pl.read_csv(generated / "processed" / "ticket_lifecycle_events.csv")
    counts = events.group_by("ticket_id").agg(pl.len().alias("n"))
    assert counts["n"].min() >= 4


def test_faq_matches_reference_valid_faq_ids(generated: Path) -> None:
    faq_kb = pl.read_csv(generated / "raw" / "faq_knowledge_base.csv")
    faq_checks = pl.read_csv(generated / "processed" / "faq_checks.csv")
    valid = set(faq_kb["faq_id"].to_list())
    matched = faq_checks.filter(pl.col("faq_match_found"))
    for faq_id in matched["faq_id"].to_list():
        assert faq_id in valid, faq_id


def test_escalated_tickets_have_investigations(generated: Path) -> None:
    escalations = pl.read_csv(generated / "processed" / "specialist_escalations.csv")
    investigations = pl.read_csv(generated / "processed" / "specialist_investigations.csv")
    esc_tickets = set(escalations["ticket_id"].to_list())
    inv_tickets = set(investigations["ticket_id"].to_list())
    assert esc_tickets <= inv_tickets


def test_deterministic_with_same_seed(tmp_path: Path) -> None:
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    for out in (out1, out2):
        result = subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--n-tickets",
                "60",
                "--seed",
                "1234",
                "--out-dir",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
    a = (out1 / "processed" / "ticket_summary.csv").read_bytes()
    b = (out2 / "processed" / "ticket_summary.csv").read_bytes()
    assert a == b, "generator output is not deterministic for fixed seed"
