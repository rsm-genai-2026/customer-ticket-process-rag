"""Tests for skills/ticketing_common/ticketing_common.py.

Each public function gets a happy path plus at least one error/edge case.
File IO uses ``tmp_path`` so the real ``data/working/`` is never touched.
"""

from __future__ import annotations

import csv
import importlib.util
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "skills" / "ticketing_common" / "ticketing_common.py"
_spec = importlib.util.spec_from_file_location("ticketing_common", _MODULE_PATH)
assert _spec and _spec.loader
ticketing_common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ticketing_common)


def test_now_iso_is_parseable_and_utc() -> None:
    ts = ticketing_common.now_iso()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_repo_root_points_to_repo() -> None:
    root = ticketing_common.repo_root()
    assert (root / "pyproject.toml").exists()
    assert (root / "skills" / "ticketing_common" / "ticketing_common.py").exists()


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
