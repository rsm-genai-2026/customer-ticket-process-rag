"""Shared helpers for the IT ticketing skills.

These utilities are used by every skill under ``skills/`` that runs the
human IT ticketing workflow. They handle the boring-but-must-be-correct
pieces: loading CSVs, requiring a ticket exists, finding the latest
working-row a downstream step needs, and appending rows to CSVs without
reordering existing columns.

All functions are deterministic and only depend on the standard library
plus ``polars``.
"""

from __future__ import annotations

import csv
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

ACTION_LOG_FILENAME = "ticket_action_log.csv"

ACTION_LOG_COLUMNS = [
    "ticket_id",
    "created_at",
    "skill_name",
    "action",
    "inputs_used",
    "decision_summary",
    "confidence_score",
    "notes",
]


def repo_root() -> Path:
    """Return the repository root.

    Resolved from this file's location so callers don't depend on the
    current working directory. ``skills/ticketing_common/ticketing_common.py``
    is two parents below the repo root.
    """

    return Path(__file__).resolve().parents[2]


def now_iso() -> str:
    """UTC ISO-8601 timestamp with second precision.

    Example: ``2026-04-30T12:34:56+00:00``. We use timezone-aware UTC so
    rows from different machines compare cleanly.
    """

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_csv(data_dir: Path, rel: str) -> pl.DataFrame:
    """Read a CSV under ``data_dir`` with a clear error if missing.

    ``rel`` is relative to ``data_dir`` (e.g. ``raw/submitted_tickets.csv``).
    Raises ``FileNotFoundError`` with a hint if the file does not exist.
    """

    path = Path(data_dir) / rel
    if not path.exists():
        raise FileNotFoundError(
            f"required data file is missing: {path}. "
            f"Run `uv run python scripts/generate_human_ticket_data.py` to regenerate."
        )
    return pl.read_csv(path)


def require_ticket(data_dir: Path, ticket_id: str) -> dict:
    """Return the ticket row as a dict, or raise ``KeyError`` if missing.

    Reads ``raw/submitted_tickets.csv``. Skill scripts should catch this
    and exit non-zero with a clear stderr message.
    """

    if not ticket_id:
        raise ValueError("ticket_id is required and cannot be empty")
    tickets = read_csv(Path(data_dir), "raw/submitted_tickets.csv")
    rows = tickets.filter(pl.col("ticket_id") == ticket_id).to_dicts()
    if not rows:
        raise KeyError(f"ticket_id {ticket_id!r} not found in raw/submitted_tickets.csv")
    return rows[0]


def latest_working_row(out_dir: Path, table: str, ticket_id: str) -> dict | None:
    """Return the most recent row for ``ticket_id`` in ``out_dir/<table>.csv``.

    Returns ``None`` if the file or matching row does not exist. ``most
    recent`` is determined by ``created_at`` if present, falling back to
    file order when the column is absent (useful early in development).
    """

    path = Path(out_dir) / f"{table}.csv"
    if not path.exists():
        return None
    df = pl.read_csv(path)
    if "ticket_id" not in df.columns:
        return None
    matching = df.filter(pl.col("ticket_id") == ticket_id)
    if matching.is_empty():
        return None
    if "created_at" in matching.columns:
        matching = matching.sort("created_at")
    return matching.tail(1).to_dicts()[0]


def append_csv_row(path: Path, row: dict) -> None:
    """Append one row to a CSV, creating the file with a header if missing.

    Schema-stable: when the file already exists its header order is
    preserved. New keys that aren't in the existing header are dropped
    with a stderr warning rather than silently introducing column drift.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_header: list[str] | None = None
    if path.exists():
        with path.open("r", newline="") as f:
            reader = csv.reader(f)
            try:
                existing_header = next(reader)
            except StopIteration:
                existing_header = None

    if existing_header is None:
        header = list(row.keys())
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerow([_to_cell(row.get(c, "")) for c in header])
        return

    extra = [k for k in row.keys() if k not in existing_header]
    if extra:
        print(
            f"warning: dropping unknown columns {extra} when appending to {path}",
            file=sys.stderr,
        )
    with path.open("a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([_to_cell(row.get(c, "")) for c in existing_header])


def append_action_log(out_dir: Path, record: dict) -> None:
    """Append one row to ``out_dir/ticket_action_log.csv``.

    Always uses :data:`ACTION_LOG_COLUMNS` as the schema so different
    skills produce a uniform timeline. Missing fields are written as
    empty strings.
    """

    path = Path(out_dir) / ACTION_LOG_FILENAME
    row = {col: record.get(col, "") for col in ACTION_LOG_COLUMNS}
    append_csv_row(path, row)


def pipe_join(values: Iterable[object]) -> str:
    """Join values with ``|`` for multi-value cells.

    Empty / ``None`` values are skipped. Strings are stripped to avoid
    accidental whitespace in CSV cells.
    """

    parts: list[str] = []
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            parts.append(s)
    return "|".join(parts)


def _to_cell(value: object) -> str:
    """Normalize a value for CSV output.

    Polars and our generators sometimes hand booleans through unchanged;
    we want lowercase ``true``/``false`` to match what ``polars.write_csv``
    produces in the rest of the project.
    """

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
