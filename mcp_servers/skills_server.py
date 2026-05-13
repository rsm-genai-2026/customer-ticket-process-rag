"""MCP skills server — exposes the two LLM-judgement skills as MCP tools.

This is the **skills** half of the MCP exhibit. Each tool here is a thin
wrapper around the corresponding skill script under ``skills/`` — the
tool runs the script as a subprocess in ``--mode demo`` and returns its
JSON envelope verbatim. No prompt-construction or LLM-call logic is
duplicated.

Two tools:

* ``check_faq_resolution(ticket_id)`` — wraps ``skills/check-faq-resolution``
* ``investigate_specialist_solution(ticket_id)`` — wraps ``skills/investigate-specialist-solution``

The companion ``mcp_servers.data_server`` exposes the workflow's data
files as URI resources. The two servers are deliberately split so it is
obvious which one serves work and which one serves data.

Run from the repo root::

    uv run python -m mcp_servers.skills_server
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

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

OUT_DIR = Path(tempfile.gettempdir()) / "mcp_servers_working"


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
    """Construct and return the configured FastMCP skills server."""

    server = FastMCP("customer-ticket-skills")

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

    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":  # pragma: no cover - CLI guard
    main()
