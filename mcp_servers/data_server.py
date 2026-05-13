"""MCP data server — exposes the workflow's data files as URI resources.

This is the **data** half of the MCP exhibit. It does **not** run any
skill or call any LLM. It only serves the repo's CSV data to an MCP
client (Claude Desktop, Claude Code) under five URI templates:

* ``tickets://raw`` — full submitted-tickets CSV
* ``tickets://{ticket_id}`` — one ticket row as JSON
* ``faq://kb`` — the FAQ knowledge base CSV
* ``dictionaries://{name}`` — one of the four reference dictionaries
* ``working://{filename}`` — any CSV under ``data/working/``

Local-vs-remote switch for the FAQ
----------------------------------

When the environment variable ``FAQ_KB_URI`` is set (e.g. to a Postgres
connection URI), ``faq://kb`` returns rows fetched from that database
instead of from the local CSV. Everything else stays on local files.
This is the small, teachable demonstration of the local→remote pattern
the rest of the repo is built around.

Run from the repo root::

    uv run python -m mcp_servers.data_server

The companion ``mcp_servers.skills_server`` exposes the LLM-skill tools.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import polars as pl
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
DICTIONARY_NAMES = ("categories", "priority_rules", "status_codes", "systems")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_\-]+\.csv$")


def _load_faq_kb() -> pl.DataFrame:
    """Return the FAQ knowledge base, local CSV by default.

    When ``FAQ_KB_URI`` is set in the environment (or in a ``.env`` file
    next to this repo or in ``~/.env``), the function reads from the URI
    instead — e.g. a Postgres database. The URI is anything
    ``polars.read_database_uri`` accepts (``postgresql://user:pass@host/db``,
    ``mysql://...``, ``sqlite://...``).
    """

    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(Path.home() / ".env")
    uri = os.environ.get("FAQ_KB_URI", "").strip()
    if uri:
        return pl.read_database_uri("SELECT * FROM faq_knowledge_base", uri)
    return pl.read_csv(DATA_DIR / "raw" / "faq_knowledge_base.csv")


def build_server() -> FastMCP:
    """Construct and return the configured FastMCP data server.

    Kept as a function (rather than a module-level singleton) so tests
    can instantiate fresh servers without side effects.
    """

    server = FastMCP("customer-ticket-data")

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
        description=(
            "FAQ knowledge base as CSV. Reads from data/raw/faq_knowledge_base.csv "
            "by default; reads from the URI in FAQ_KB_URI when that env var is set."
        ),
        mime_type="text/csv",
    )
    def faq_kb() -> str:
        return _load_faq_kb().write_csv()

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
