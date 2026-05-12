"""Optional MCP exhibit for the customer-ticket-process repo.

This server is **not** part of the orchestrator workflow. It is a teaching
exhibit that gives an LLM client (Claude Desktop, Claude Code) a second
front door to the same skills and data the orchestrator uses, so students
can compare "agent-driven via MCP" with "subprocess-driven via the
orchestrator." See ``mcp_exhibit/README.md`` for context and wiring
instructions.

What this server exposes:

* **Tools** — thin wrappers around the two LLM-judgment skills. Each tool
  invokes the existing skill script as a subprocess and returns its JSON
  envelope, so we do not duplicate prompt-construction or LLM-call logic
  here.
* **Resources** — read-only views of the repo's data files (raw tickets,
  FAQ KB, reference dictionaries, working CSVs).

Design choices worth noting:

* The data directory is resolved from this file's location, so the server
  needs no configuration.
* Tool invocations write to a separate temp ``out-dir`` so the exhibit
  cannot pollute the orchestrator's ``data/working/`` state.
* Tools run in ``--mode demo`` and ``--idempotency-mode replace`` so they
  work standalone (no upstream orchestrator state required) and re-running
  the same call does not endlessly append rows.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import polars as pl
from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

SKILL_SCRIPTS = {
    "check_faq_resolution": REPO_ROOT / "skills" / "check-faq-resolution" / "scripts" / "check_faq_resolution.py",
    "investigate_specialist_solution": REPO_ROOT
    / "skills"
    / "investigate-specialist-solution"
    / "scripts"
    / "investigate_specialist_solution.py",
}

DICTIONARY_NAMES = ("categories", "priority_rules", "status_codes", "systems")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_\-]+\.csv$")

OUT_DIR = Path(tempfile.gettempdir()) / "mcp_exhibit_working"


def _run_skill(script_path: Path, ticket_id: str) -> dict:
    """Run a skill script as a subprocess and parse its JSON envelope.

    Returns the parsed envelope on success, or a synthetic error envelope
    if the subprocess produced no output or unparseable output.
    """

    if not ticket_id or not isinstance(ticket_id, str):
        return {
            "status": "error",
            "error": {"code": "missing_ticket_id", "message": "ticket_id is required"},
        }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script_path),
        "--json",
        "--mode",
        "demo",
        "--idempotency-mode",
        "replace",
        "--ticket-id",
        ticket_id,
        "--data-dir",
        str(DATA_DIR),
        "--out-dir",
        str(OUT_DIR),
    ]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {
            "status": "error",
            "error": {
                "code": "no_output",
                "message": (completed.stderr or "").strip() or "skill produced no output",
            },
            "exit_code": completed.returncode,
        }
    try:
        return json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "error": {
                "code": "invalid_envelope",
                "message": str(exc),
                "stdout": stdout[:1000],
            },
            "exit_code": completed.returncode,
        }


def build_server() -> FastMCP:
    """Construct and return a configured FastMCP server.

    Kept as a function (rather than a module-level singleton) so tests
    can instantiate fresh servers without side effects.
    """

    server = FastMCP("customer-ticket-process-exhibit")

    @server.tool()
    def check_faq_resolution(ticket_id: str) -> dict:
        """Ask the LLM whether the FAQ KB resolves the given ticket.

        Wraps skills/check-faq-resolution. Returns the skill's JSON
        envelope including ``outputs.faq_match_found``, ``outputs.faq_id``,
        ``outputs.match_confidence``, and a recommended next step.
        """

        return _run_skill(SKILL_SCRIPTS["check_faq_resolution"], ticket_id)

    @server.tool()
    def investigate_specialist_solution(ticket_id: str) -> dict:
        """Ask the LLM to act as an IT specialist for an escalated ticket.

        Wraps skills/investigate-specialist-solution. Returns the skill's
        JSON envelope including the specialist's root cause, diagnostic
        steps, and customer-safe solution summary.
        """

        return _run_skill(SKILL_SCRIPTS["investigate_specialist_solution"], ticket_id)

    @server.resource(
        "tickets://raw",
        description="Full submitted tickets CSV (data/raw/submitted_tickets.csv).",
        mime_type="text/csv",
    )
    def tickets_raw() -> str:
        return (DATA_DIR / "raw" / "submitted_tickets.csv").read_text()

    @server.resource(
        "tickets://{ticket_id}",
        description="One submitted ticket row as JSON, or a not-found error.",
        mime_type="application/json",
    )
    def ticket(ticket_id: str) -> str:
        tickets = pl.read_csv(DATA_DIR / "raw" / "submitted_tickets.csv")
        rows = tickets.filter(pl.col("ticket_id") == ticket_id).to_dicts()
        if not rows:
            return json.dumps({"error": f"ticket {ticket_id} not found"})
        return json.dumps(rows[0], indent=2, default=str)

    @server.resource(
        "faq://kb",
        description="FAQ knowledge base CSV (data/raw/faq_knowledge_base.csv).",
        mime_type="text/csv",
    )
    def faq_kb() -> str:
        return (DATA_DIR / "raw" / "faq_knowledge_base.csv").read_text()

    @server.resource(
        "dictionaries://{name}",
        description=("Reference dictionary CSV. Allowed names: " + ", ".join(DICTIONARY_NAMES) + "."),
        mime_type="text/csv",
    )
    def dictionary(name: str) -> str:
        if name not in DICTIONARY_NAMES:
            return json.dumps(
                {
                    "error": f"unknown dictionary {name!r}",
                    "available": list(DICTIONARY_NAMES),
                }
            )
        return (DATA_DIR / "dictionaries" / f"{name}.csv").read_text()

    @server.resource(
        "working://{filename}",
        description=(
            "A CSV from data/working/ (e.g. ticket_action_log.csv, "
            "triage_decisions.csv). Filename is restricted to ^[A-Za-z0-9_-]+\\.csv$."
        ),
        mime_type="text/csv",
    )
    def working(filename: str) -> str:
        if not SAFE_FILENAME.match(filename):
            return json.dumps({"error": "filename must match ^[A-Za-z0-9_-]+\\.csv$"})
        path = DATA_DIR / "working" / filename
        if not path.exists():
            return json.dumps({"error": f"working file {filename} not found"})
        return path.read_text()

    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":  # pragma: no cover - CLI guard
    main()
