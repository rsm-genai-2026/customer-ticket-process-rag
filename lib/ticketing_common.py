"""Shared helpers for the IT ticketing skills.

These utilities are used by every skill under ``skills/`` that runs the
AI-assisted IT ticketing workflow. They handle the boring-but-must-be-correct
pieces required for orchestrated execution:

* Loading CSVs and requiring a ticket exists.
* Threading a stable ``workflow_run_id`` and ``step_id`` through every
  decision so the orchestrator can correlate, retry, and replay.
* Idempotency: a re-run with the same ``(workflow_run_id, step_id)``
  is a no-op rather than appending a duplicate row.
* File locking around every CSV write so concurrent skill runs do not
  interleave their output (POSIX flock; falls back to a no-op on
  systems without ``fcntl``).
* A stable JSON envelope for orchestrator output, with text-mode for
  interactive demos.

All functions are deterministic and only depend on the standard library
plus ``polars``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

ACTION_LOG_FILENAME = "ticket_action_log.csv"
LOCK_FILENAME = ".ticketing_common.lock"

ACTION_LOG_COLUMNS = [
    "ticket_id",
    "created_at",
    "skill_name",
    "workflow_run_id",
    "step_id",
    "action",
    "inputs_used",
    "decision_summary",
    "confidence_score",
    "needs_human_review",
    "notes",
]

# Common envelope statuses returned by skill scripts.
STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"

# Modes:
#   "live" — only read live skill output from data/working/. Refuses if
#            an expected upstream working row is missing. Default.
#   "demo" — also accept the synthetic processed/ tables as historical
#            fallback. Useful for tutorials against the seeded dataset.
MODE_LIVE = "live"
MODE_DEMO = "demo"
DEFAULT_MODE = MODE_LIVE

# Confidence threshold below which a skill's output is flagged for human
# review. Skills can override per-decision but this is the project default.
HUMAN_REVIEW_CONFIDENCE_THRESHOLD = 0.60


# ---------------------------------------------------------------------------
# Workflow / step identifiers
# ---------------------------------------------------------------------------


def default_workflow_run_id() -> str:
    """Generate a fresh ``wf-<uuid>`` identifier.

    Used when an orchestrator does not pass ``--workflow-run-id``. Two
    consecutive calls always return distinct values.
    """

    return f"wf-{uuid.uuid4().hex[:12]}"


def default_step_id(skill_name: str) -> str:
    """Generate a fresh ``<skill>-<uuid>`` step id."""

    return f"{skill_name}-{uuid.uuid4().hex[:8]}"


def make_skill_parser(description: str = "") -> argparse.ArgumentParser:
    """Return an argparse parser with the standard skill CLI surface.

    Every skill takes the same orchestrator-facing arguments. Building
    them in one place keeps the contract identical across skills and
    means students adding a new skill don't need to re-derive the flag
    names from a sibling script.

    The returned parser exposes:

    * ``--ticket-id`` (required)
    * ``--data-dir`` / ``--out-dir``
    * ``--workflow-run-id`` / ``--step-id``
    * ``--mode {live,demo}`` — see :data:`MODE_LIVE` / :data:`MODE_DEMO`
    * ``--json`` — emit the orchestration envelope instead of plain text
    * ``--idempotency-mode {skip,replace}`` — short-circuit a duplicate
      ``(workflow_run_id, step_id)`` or rewrite the existing row

    Skill-specific flags (e.g. ``--feedback-text``) can be added to the
    returned parser before ``parse_args``.
    """

    parser = argparse.ArgumentParser(description=description or None)
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="data/working")
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument("--step-id", default="")
    parser.add_argument("--mode", choices=[MODE_LIVE, MODE_DEMO], default=DEFAULT_MODE)
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--idempotency-mode", choices=["skip", "replace"], default="skip")
    return parser


# ---------------------------------------------------------------------------
# Filesystem & lock helpers
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Return the repository root.

    Resolved from this file's location so callers don't depend on the
    current working directory. ``lib/ticketing_common.py`` is one parent
    below the repo root.
    """

    return Path(__file__).resolve().parents[1]


def now_iso() -> str:
    """UTC ISO-8601 timestamp with second precision.

    Example: ``2026-04-30T12:34:56+00:00``. We use timezone-aware UTC so
    rows from different machines compare cleanly.
    """

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def working_lock(out_dir: Path):
    """POSIX advisory lock around any write into ``out_dir``.

    Skills append to shared CSVs in ``data/working/``. Concurrent skill
    runs (orchestrator parallelism, two analysts on the same ticket)
    would otherwise interleave rows or rewrite headers. We hold an
    exclusive ``fcntl`` lock on a sentinel file in the working directory
    for the duration of every write.

    On platforms without ``fcntl`` (e.g. Windows) this degrades to a
    no-op so tests remain runnable; on POSIX the lock is enforced.
    """

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lock_path = out_dir / LOCK_FILENAME
    try:
        import fcntl  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - non-POSIX
        # No-op fallback: still call mkdir but skip locking.
        yield
        return
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


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


def latest_working_row(
    out_dir: Path,
    table: str,
    ticket_id: str,
    *,
    workflow_run_id: str | None = None,
) -> dict | None:
    """Return the most recent row for ``ticket_id`` in ``out_dir/<table>.csv``.

    Returns ``None`` if the file or matching row does not exist. When
    ``workflow_run_id`` is supplied, only rows from that run are eligible.
    ``most recent`` is determined by ``created_at`` if present, falling
    back to file order when the column is absent.
    """

    path = Path(out_dir) / f"{table}.csv"
    if not path.exists():
        return None
    df = pl.read_csv(path)
    if "ticket_id" not in df.columns:
        return None
    matching = df.filter(pl.col("ticket_id") == ticket_id)
    if workflow_run_id:
        if "workflow_run_id" not in matching.columns:
            return None
        matching = matching.filter(pl.col("workflow_run_id") == workflow_run_id)
    if matching.is_empty():
        return None
    if "created_at" in matching.columns:
        matching = matching.sort("created_at")
    return matching.tail(1).to_dicts()[0]


def find_step_row(out_dir: Path, table: str, workflow_run_id: str, step_id: str) -> dict | None:
    """Return any existing row that matches ``(workflow_run_id, step_id)``.

    Used by skill scripts before writing to detect a re-run of the same
    step and short-circuit with ``status=skipped`` instead of appending
    a duplicate row.
    """

    path = Path(out_dir) / f"{table}.csv"
    if not path.exists():
        return None
    df = pl.read_csv(path)
    if "workflow_run_id" not in df.columns or "step_id" not in df.columns:
        return None
    matching = df.filter((pl.col("workflow_run_id") == workflow_run_id) & (pl.col("step_id") == step_id))
    if matching.is_empty():
        return None
    return matching.head(1).to_dicts()[0]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def append_csv_row(path: Path, row: dict) -> None:
    """Append one row to a CSV, creating the file with a header if missing.

    Schema-stable: when the file already exists its header order is
    preserved. New keys that aren't in the existing header are dropped
    with a stderr warning rather than silently introducing column drift.

    All writes are guarded by a POSIX advisory lock on the parent
    directory's lock file, so concurrent skill runs cannot interleave
    rows.
    """

    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    with working_lock(parent):
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


def replace_step_row(path: Path, row: dict, *, workflow_run_id: str, step_id: str) -> None:
    """Replace one ``(workflow_run_id, step_id)`` row, appending if absent.

    This is the implementation behind ``--idempotency-mode replace``. It
    preserves the existing CSV header, drops unknown columns the same way
    :func:`append_csv_row` does, and rewrites the file under the working
    directory lock so readers never see interleaved writes.
    """

    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    with working_lock(parent):
        if not path.exists():
            header = list(row.keys())
            with path.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerow([_to_cell(row.get(c, "")) for c in header])
            return

        with path.open("r", newline="") as f:
            reader = csv.DictReader(f)
            header = list(reader.fieldnames or [])
            rows = list(reader)

        if not header:
            header = list(row.keys())
            rows = []

        extra = [k for k in row.keys() if k not in header]
        if extra:
            print(
                f"warning: dropping unknown columns {extra} when replacing row in {path}",
                file=sys.stderr,
            )

        normalized = {col: _to_cell(row.get(col, "")) for col in header}
        replaced = False
        for idx, existing in enumerate(rows):
            if existing.get("workflow_run_id") == workflow_run_id and existing.get("step_id") == step_id:
                rows[idx] = normalized
                replaced = True
                break
        if not replaced:
            rows.append(normalized)

        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)


def append_action_log(out_dir: Path, record: dict) -> None:
    """Append one row to ``out_dir/ticket_action_log.csv``.

    Always uses :data:`ACTION_LOG_COLUMNS` as the schema so different
    skills produce a uniform timeline. Missing fields are written as
    empty strings. The action log is append-only — re-running a step
    intentionally appends another row so the audit trail shows the
    retry; idempotency lives on the per-skill working tables.
    """

    path = Path(out_dir) / ACTION_LOG_FILENAME
    row = {col: record.get(col, "") for col in ACTION_LOG_COLUMNS}
    append_csv_row(path, row)


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def needs_human_review(
    confidence: float | str | None,
    *,
    threshold: float = HUMAN_REVIEW_CONFIDENCE_THRESHOLD,
    extra: bool = False,
) -> bool:
    """Return True if a decision should be queued for a human reviewer.

    Triggered when confidence is below ``threshold`` or when ``extra``
    is True (used by skills that detect a structural reason for review,
    e.g. an ambiguous customer reply).
    """

    if extra:
        return True
    if confidence in (None, ""):
        return False
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return False
    return c < threshold


def make_envelope(
    *,
    status: str,
    skill_name: str,
    workflow_run_id: str,
    step_id: str,
    ticket_id: str,
    next_action: str = "",
    confidence: float | str | None = None,
    review_required: bool = False,
    artifact_refs: Iterable[str] | None = None,
    outputs: dict | None = None,
    error: dict | None = None,
) -> dict:
    """Build the standard JSON envelope returned by every skill.

    Stable shape so an orchestrator can dispatch on ``next_action``,
    short-circuit on ``status == "skipped"``, surface ``error`` on
    failure, and route to a human reviewer when ``review_required``.
    """

    return {
        "status": status,
        "skill_name": skill_name,
        "workflow_run_id": workflow_run_id,
        "step_id": step_id,
        "ticket_id": ticket_id,
        "next_action": next_action,
        "confidence": (None if confidence in (None, "") else _coerce_float(confidence)),
        "review_required": bool(review_required),
        "artifact_refs": list(artifact_refs or []),
        "outputs": outputs or {},
        "error": error,
    }


def emit_envelope(envelope: dict, *, as_json: bool, text_summary: str = "") -> None:
    """Print the envelope as JSON or as a human-readable summary.

    ``as_json=True`` prints the canonical JSON to stdout (one line). The
    ``text_summary`` is the multi-line operator-friendly display that
    interactive demos show; it is ignored in JSON mode.
    """

    if as_json:
        print(json.dumps(envelope, sort_keys=False, default=str))
    else:
        print(text_summary or json.dumps(envelope, sort_keys=False, indent=2, default=str))


def emit_error(
    *,
    skill_name: str,
    workflow_run_id: str,
    step_id: str,
    ticket_id: str,
    error_code: str,
    message: str,
    as_json: bool,
    next_action: str = "",
    exit_code: int = 2,
) -> int:
    """Emit an error envelope and return the exit code.

    Every skill catches the same handful of exception classes (missing
    ticket, missing upstream row, missing data file, invalid input) and
    needs to produce the same shape of envelope + stderr message. This
    helper centralises that pattern so skill scripts read as:

        except KeyError as exc:
            return emit_error(..., error_code="ticket_not_found",
                              message=str(exc), exit_code=2)

    ``next_action`` should be set when the caller can recommend a skill
    to run instead (e.g. ``classify-prioritize-ticket`` after a missing
    triage row). Returning the exit code lets the caller ``return`` it
    directly from ``main()``.
    """

    env = make_envelope(
        status=STATUS_ERROR,
        skill_name=skill_name,
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        next_action=next_action,
        error={"code": error_code, "message": message},
    )
    emit_envelope(env, as_json=as_json, text_summary=f"error: {message}")
    if not as_json:
        print(f"error: {message}", file=sys.stderr)
    return exit_code


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


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


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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
