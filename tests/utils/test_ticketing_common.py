"""Tests for utils/ticketing_common.py — the shared infrastructure module.

Imported by every skill *and* every automation in this repo, so it is not
specific to either. Each public function gets a happy path plus at least
one error/edge case. File IO uses ``tmp_path`` so the real
``data/working/`` is never touched.
"""

from __future__ import annotations

import csv
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from utils import ticketing_common


def test_now_iso_is_parseable_and_utc() -> None:
    ts = ticketing_common.now_iso()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_repo_root_points_to_repo() -> None:
    root = ticketing_common.repo_root()
    assert (root / "pyproject.toml").exists()
    assert (root / "utils" / "ticketing_common.py").exists()


def test_read_csv_happy(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    target = tmp_path / "raw" / "demo.csv"
    pl.DataFrame({"a": [1, 2], "b": ["x", "y"]}).write_csv(target)
    df = ticketing_common.read_csv(tmp_path, "raw/demo.csv")
    assert df.height == 2
    assert df.columns == ["a", "b"]


def test_read_csv_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as exc:
        ticketing_common.read_csv(tmp_path, "raw/nope.csv")
    assert "regenerate" in str(exc.value)


def test_require_ticket_returns_row(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    pl.DataFrame({"ticket_id": ["TKT-00001", "TKT-00002"], "subject": ["a", "b"]}).write_csv(
        tmp_path / "raw" / "submitted_tickets.csv"
    )
    row = ticketing_common.require_ticket(tmp_path, "TKT-00002")
    assert row == {"ticket_id": "TKT-00002", "subject": "b"}


def test_require_ticket_missing_raises_keyerror(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    pl.DataFrame({"ticket_id": ["TKT-00001"], "subject": ["a"]}).write_csv(tmp_path / "raw" / "submitted_tickets.csv")
    with pytest.raises(KeyError) as exc:
        ticketing_common.require_ticket(tmp_path, "TKT-99999")
    assert "TKT-99999" in str(exc.value)


def test_require_ticket_empty_id_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ticketing_common.require_ticket(tmp_path, "")


def test_latest_working_row_returns_most_recent(tmp_path: Path) -> None:
    path = tmp_path / "triage_decisions.csv"
    pl.DataFrame(
        {
            "ticket_id": ["TKT-00001", "TKT-00001", "TKT-00002"],
            "created_at": [
                "2026-04-01T10:00:00+00:00",
                "2026-04-02T10:00:00+00:00",
                "2026-04-01T10:00:00+00:00",
            ],
            "value": ["old", "new", "other"],
        }
    ).write_csv(path)
    row = ticketing_common.latest_working_row(tmp_path, "triage_decisions", "TKT-00001")
    assert row is not None
    assert row["value"] == "new"


def test_latest_working_row_scopes_to_workflow_run(tmp_path: Path) -> None:
    path = tmp_path / "triage_decisions.csv"
    pl.DataFrame(
        {
            "ticket_id": ["TKT-00001", "TKT-00001"],
            "workflow_run_id": ["wf-a", "wf-b"],
            "created_at": [
                "2026-04-01T10:00:00+00:00",
                "2026-04-02T10:00:00+00:00",
            ],
            "value": ["from-a", "from-b"],
        }
    ).write_csv(path)
    row = ticketing_common.latest_working_row(
        tmp_path,
        "triage_decisions",
        "TKT-00001",
        workflow_run_id="wf-a",
    )
    assert row is not None
    assert row["value"] == "from-a"


def test_latest_working_row_missing_file_returns_none(tmp_path: Path) -> None:
    assert ticketing_common.latest_working_row(tmp_path, "triage_decisions", "TKT-1") is None


def test_latest_working_row_no_match_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "triage_decisions.csv"
    pl.DataFrame(
        {
            "ticket_id": ["TKT-00001"],
            "created_at": ["2026-04-01T10:00:00+00:00"],
            "value": ["x"],
        }
    ).write_csv(path)
    assert ticketing_common.latest_working_row(tmp_path, "triage_decisions", "TKT-99999") is None


def test_append_csv_row_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    ticketing_common.append_csv_row(path, {"a": 1, "b": "x"})
    with path.open() as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["a", "b"]
    assert rows[1] == ["1", "x"]


def test_append_csv_row_preserves_existing_header(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    ticketing_common.append_csv_row(path, {"a": 1, "b": "x"})
    ticketing_common.append_csv_row(path, {"b": "y", "a": 2})
    with path.open() as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["a", "b"]
    assert rows[1] == ["1", "x"]
    assert rows[2] == ["2", "y"]


def test_append_csv_row_drops_unknown_columns_with_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "out.csv"
    ticketing_common.append_csv_row(path, {"a": 1, "b": "x"})
    ticketing_common.append_csv_row(path, {"a": 2, "b": "y", "c": "ignored"})
    captured = capsys.readouterr()
    assert "dropping unknown columns" in captured.err
    with path.open() as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["a", "b"]
    assert rows[2] == ["2", "y"]


def test_append_csv_row_writes_booleans_lowercase(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    ticketing_common.append_csv_row(path, {"flag": True, "other": False})
    with path.open() as f:
        rows = list(csv.reader(f))
    assert rows[1] == ["true", "false"]


def test_replace_step_row_replaces_matching_step_without_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    ticketing_common.append_csv_row(
        path,
        {
            "ticket_id": "TKT-1",
            "workflow_run_id": "wf-1",
            "step_id": "step-1",
            "value": "old",
        },
    )
    ticketing_common.replace_step_row(
        path,
        {
            "ticket_id": "TKT-1",
            "workflow_run_id": "wf-1",
            "step_id": "step-1",
            "value": "new",
        },
        workflow_run_id="wf-1",
        step_id="step-1",
    )
    df = pl.read_csv(path)
    assert df.height == 1
    assert df.row(0, named=True)["value"] == "new"


def test_append_action_log_normalises_columns(tmp_path: Path) -> None:
    ticketing_common.append_action_log(
        tmp_path,
        {
            "ticket_id": "TKT-00001",
            "created_at": "2026-04-30T00:00:00+00:00",
            "skill_name": "receive-ticket",
            "action": "intake_summary",
            "decision_summary": "ok",
        },
    )
    path = tmp_path / "ticket_action_log.csv"
    with path.open() as f:
        rows = list(csv.reader(f))
    assert rows[0] == ticketing_common.ACTION_LOG_COLUMNS
    assert rows[1][0] == "TKT-00001"
    # missing fields written as empty strings, not "None"
    assert rows[1][rows[0].index("inputs_used")] == ""


def test_pipe_join_drops_empty_and_strips() -> None:
    assert ticketing_common.pipe_join(["a", " b ", "", None, "c"]) == "a|b|c"


def test_pipe_join_handles_all_empty() -> None:
    assert ticketing_common.pipe_join(["", None, "  "]) == ""


# ---------------------------------------------------------------------------
# New helpers added for orchestrated execution
# ---------------------------------------------------------------------------


def test_default_workflow_run_id_is_unique_per_call() -> None:
    a = ticketing_common.default_workflow_run_id()
    b = ticketing_common.default_workflow_run_id()
    assert a.startswith("wf-")
    assert b.startswith("wf-")
    assert a != b


def test_default_step_id_includes_skill_name() -> None:
    out = ticketing_common.default_step_id("classify-prioritize-ticket")
    assert out.startswith("classify-prioritize-ticket-")


def test_find_step_row_returns_existing(tmp_path: Path) -> None:
    path = tmp_path / "triage_decisions.csv"
    pl.DataFrame(
        [
            {
                "ticket_id": "TKT-1",
                "workflow_run_id": "wf-1",
                "step_id": "step-a",
                "value": "first",
            },
            {
                "ticket_id": "TKT-1",
                "workflow_run_id": "wf-1",
                "step_id": "step-b",
                "value": "second",
            },
        ]
    ).write_csv(path)
    row = ticketing_common.find_step_row(tmp_path, "triage_decisions", "wf-1", "step-b")
    assert row is not None
    assert row["value"] == "second"


def test_find_step_row_no_match_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "triage_decisions.csv"
    pl.DataFrame(
        [
            {
                "ticket_id": "TKT-1",
                "workflow_run_id": "wf-1",
                "step_id": "step-a",
                "value": "x",
            }
        ]
    ).write_csv(path)
    assert ticketing_common.find_step_row(tmp_path, "triage_decisions", "wf-2", "step-a") is None


def test_find_step_row_missing_file_returns_none(tmp_path: Path) -> None:
    assert ticketing_common.find_step_row(tmp_path, "triage_decisions", "wf-1", "s-1") is None


def test_find_step_row_no_id_columns_returns_none(tmp_path: Path) -> None:
    """Old CSV files without workflow_run_id/step_id should not match."""
    path = tmp_path / "triage_decisions.csv"
    pl.DataFrame([{"ticket_id": "TKT-1", "value": "x"}]).write_csv(path)
    assert ticketing_common.find_step_row(tmp_path, "triage_decisions", "wf-1", "s-1") is None


def test_needs_human_review_below_threshold() -> None:
    assert ticketing_common.needs_human_review(0.4) is True
    assert ticketing_common.needs_human_review(0.7) is False
    assert ticketing_common.needs_human_review("0.5") is True


def test_needs_human_review_extra_overrides() -> None:
    assert ticketing_common.needs_human_review(0.95, extra=True) is True


def test_needs_human_review_blank_or_invalid_returns_false() -> None:
    assert ticketing_common.needs_human_review(None) is False
    assert ticketing_common.needs_human_review("") is False
    assert ticketing_common.needs_human_review("not a number") is False


def test_make_envelope_has_stable_shape() -> None:
    env = ticketing_common.make_envelope(
        status=ticketing_common.STATUS_OK,
        skill_name="x",
        workflow_run_id="wf-1",
        step_id="s-1",
        ticket_id="TKT-1",
        next_action="next-skill",
        confidence=0.9,
        review_required=False,
        artifact_refs=["working/triage_decisions.csv"],
        outputs={"a": 1},
    )
    expected_keys = {
        "status",
        "skill_name",
        "workflow_run_id",
        "step_id",
        "ticket_id",
        "next_action",
        "confidence",
        "review_required",
        "artifact_refs",
        "outputs",
        "error",
    }
    assert set(env.keys()) == expected_keys
    assert env["confidence"] == 0.9
    assert env["error"] is None


def test_emit_envelope_json_is_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    env = ticketing_common.make_envelope(
        status="ok",
        skill_name="x",
        workflow_run_id="wf",
        step_id="s",
        ticket_id="TKT-1",
    )
    ticketing_common.emit_envelope(env, as_json=True)
    out = capsys.readouterr().out
    assert "\n" not in out.strip()
    parsed = json.loads(out)
    assert parsed["status"] == "ok"
    assert parsed["ticket_id"] == "TKT-1"


def test_emit_envelope_text_uses_summary(capsys: pytest.CaptureFixture[str]) -> None:
    env = ticketing_common.make_envelope(status="ok", skill_name="x", workflow_run_id="wf", step_id="s", ticket_id="t")
    ticketing_common.emit_envelope(env, as_json=False, text_summary="HUMAN-FRIENDLY")
    assert "HUMAN-FRIENDLY" in capsys.readouterr().out


def test_working_lock_serialises_concurrent_writes(tmp_path: Path) -> None:
    """Two threads appending in parallel must not interleave rows or
    duplicate the header. With locking we expect exactly N+1 lines."""
    path = tmp_path / "out.csv"
    barrier = threading.Barrier(2)

    def writer(value: int) -> None:
        barrier.wait()
        for i in range(20):
            ticketing_common.append_csv_row(path, {"value": value, "i": i})
            time.sleep(0)  # yield to encourage interleaving without locking

    t1 = threading.Thread(target=writer, args=(1,))
    t2 = threading.Thread(target=writer, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    with path.open() as f:
        lines = f.readlines()
    assert len(lines) == 41  # 1 header + 40 rows
    # Every data row must have exactly two fields — proves no interleaving.
    for line in lines[1:]:
        assert len(line.strip().split(",")) == 2


def test_action_log_columns_include_workflow_metadata() -> None:
    cols = ticketing_common.ACTION_LOG_COLUMNS
    assert "workflow_run_id" in cols
    assert "step_id" in cols
    assert "needs_human_review" in cols


# ---------------------------------------------------------------------------
# Standard skill CLI parser
# ---------------------------------------------------------------------------


def test_make_skill_parser_defaults() -> None:
    parser = ticketing_common.make_skill_parser("desc")
    args = parser.parse_args(["--ticket-id", "TKT-1"])
    assert args.ticket_id == "TKT-1"
    assert args.data_dir == "data"
    assert args.out_dir == "data/working"
    assert args.workflow_run_id == ""
    assert args.step_id == ""
    assert args.mode == ticketing_common.DEFAULT_MODE
    assert args.as_json is False
    assert args.idempotency_mode == "skip"


def test_make_skill_parser_accepts_overrides() -> None:
    parser = ticketing_common.make_skill_parser()
    args = parser.parse_args(
        [
            "--ticket-id",
            "TKT-1",
            "--data-dir",
            "/tmp/d",
            "--out-dir",
            "/tmp/w",
            "--workflow-run-id",
            "wf-x",
            "--step-id",
            "step-x",
            "--mode",
            "demo",
            "--json",
            "--idempotency-mode",
            "replace",
        ]
    )
    assert args.data_dir == "/tmp/d"
    assert args.out_dir == "/tmp/w"
    assert args.workflow_run_id == "wf-x"
    assert args.step_id == "step-x"
    assert args.mode == "demo"
    assert args.as_json is True
    assert args.idempotency_mode == "replace"


def test_make_skill_parser_rejects_unknown_mode() -> None:
    parser = ticketing_common.make_skill_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--ticket-id", "TKT-1", "--mode", "rogue"])


def test_make_skill_parser_rejects_unknown_idempotency_mode() -> None:
    parser = ticketing_common.make_skill_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--ticket-id", "TKT-1", "--idempotency-mode", "merge"])


def test_make_skill_parser_requires_ticket_id() -> None:
    parser = ticketing_common.make_skill_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_make_skill_parser_extension_with_skill_specific_args() -> None:
    """Skills that need extra flags add them on top of the standard parser."""

    parser = ticketing_common.make_skill_parser()
    parser.add_argument("--feedback-text", required=True)
    args = parser.parse_args(["--ticket-id", "TKT-1", "--feedback-text", "great"])
    assert args.feedback_text == "great"


# ---------------------------------------------------------------------------
# Uniform error envelope
# ---------------------------------------------------------------------------


def test_emit_error_returns_exit_code_and_writes_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = ticketing_common.emit_error(
        skill_name="x",
        workflow_run_id="wf-1",
        step_id="step-1",
        ticket_id="TKT-1",
        error_code="ticket_not_found",
        message="missing TKT-1",
        as_json=True,
    )
    assert rc == 2
    captured = capsys.readouterr()
    # JSON mode: one-line stdout, nothing on stderr
    assert captured.err == ""
    envelope = json.loads(captured.out)
    assert envelope["status"] == "error"
    assert envelope["error"] == {"code": "ticket_not_found", "message": "missing TKT-1"}
    assert envelope["ticket_id"] == "TKT-1"
    assert envelope["next_action"] == ""


def test_emit_error_text_mode_writes_to_stdout_and_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    rc = ticketing_common.emit_error(
        skill_name="x",
        workflow_run_id="wf",
        step_id="s",
        ticket_id="t",
        error_code="missing_data",
        message="no such file",
        as_json=False,
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "error: no such file" in captured.out
    assert "error: no such file" in captured.err


def test_emit_error_custom_exit_code_and_next_action(capsys: pytest.CaptureFixture[str]) -> None:
    rc = ticketing_common.emit_error(
        skill_name="x",
        workflow_run_id="wf",
        step_id="s",
        ticket_id="TKT-1",
        error_code="missing_upstream",
        message="run triage first",
        as_json=True,
        exit_code=3,
        next_action="classify-prioritize-ticket",
    )
    assert rc == 3
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["next_action"] == "classify-prioritize-ticket"
    assert envelope["error"]["code"] == "missing_upstream"
