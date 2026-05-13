"""Tests for the MCP data server.

The data server is a thin wrapper: each resource handler reads a CSV
from ``data/`` and returns it (or returns one ticket row as JSON). These
tests confirm the wiring by exercising FastMCP's introspection API
directly, without spinning up an MCP transport.

The one piece of logic worth its own test is the FAQ-from-Postgres
switch — when ``FAQ_KB_URI`` is set, ``faq://kb`` and ``_load_faq_kb()``
read from the URI via ``polars.read_database_uri`` instead of the local
CSV.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from mcp_servers import data_server as srv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _read_resource(server, uri: str) -> str:
    contents = _run(server.read_resource(uri))
    assert contents, f"no contents returned for {uri}"
    return contents[0].content


# ---------------------------------------------------------------------------
# Registration: the server exposes the contract the README documents
# ---------------------------------------------------------------------------


def test_data_server_registers_expected_resources_and_templates():
    server = srv.build_server()
    resources = _run(server.list_resources())
    templates = _run(server.list_resource_templates())

    assert sorted(str(r.uri) for r in resources) == ["faq://kb", "tickets://raw"]
    assert sorted(t.uriTemplate for t in templates) == [
        "dictionaries://{name}",
        "tickets://{ticket_id}",
        "working://{filename}",
    ]


def test_data_server_has_no_tools():
    """The data server serves data only — no tools."""
    server = srv.build_server()
    tools = _run(server.list_tools())
    assert tools == []


# ---------------------------------------------------------------------------
# Resources: happy path + edge cases
# ---------------------------------------------------------------------------


def test_tickets_raw_returns_csv_with_header():
    server = srv.build_server()
    body = _read_resource(server, "tickets://raw")
    assert body.startswith("ticket_id,"), "tickets://raw should be the raw CSV"


def test_ticket_template_returns_clean_error_for_unknown_id():
    server = srv.build_server()
    body = _read_resource(server, "tickets://NOPE-9999")
    payload = json.loads(body)
    assert payload == {"error": "ticket NOPE-9999 not found"}


def test_ticket_template_returns_a_row_for_a_known_id():
    # Use whatever ticket id is in the first data row, so the test stays
    # valid when the synthetic dataset is regenerated.
    tickets_path = srv.DATA_DIR / "raw" / "submitted_tickets.csv"
    header, first_row = tickets_path.read_text().splitlines()[:2]
    first_ticket_id = first_row.split(",", 1)[0]

    server = srv.build_server()
    body = _read_resource(server, f"tickets://{first_ticket_id}")
    payload = json.loads(body)
    assert payload.get("ticket_id") == first_ticket_id


def test_faq_kb_returns_csv_from_local_file_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FAQ_KB_URI", raising=False)
    server = srv.build_server()
    body = _read_resource(server, "faq://kb")
    assert body.startswith("faq_id,"), "faq://kb should be the FAQ KB CSV"


def test_dictionary_rejects_unknown_name():
    server = srv.build_server()
    body = _read_resource(server, "dictionaries://NOT_A_DICT")
    payload = json.loads(body)
    assert payload["error"].startswith("unknown dictionary")
    assert set(payload["available"]) == set(srv.DICTIONARY_NAMES)


def test_dictionary_returns_csv_for_known_name():
    server = srv.build_server()
    body = _read_resource(server, "dictionaries://categories")
    assert body.startswith("category_id,")


def test_working_rejects_path_traversal():
    server = srv.build_server()
    body = _read_resource(server, "working://..%2Fetc%2Fpasswd")
    payload = json.loads(body)
    assert "filename must match" in payload["error"]


def test_working_returns_error_for_missing_csv(tmp_path: Path):
    server = srv.build_server()
    body = _read_resource(server, "working://definitely_not_here.csv")
    payload = json.loads(body)
    assert payload == {"error": "working file definitely_not_here.csv not found"}


# ---------------------------------------------------------------------------
# FAQ-from-Postgres switch
# ---------------------------------------------------------------------------


_FAKE_PG_FAQ_DF = pl.DataFrame(
    {
        "faq_id": ["FAQ-PG-001"],
        "active_flag": [True],
        "category": ["login_access"],
        "system_name": ["Customer Portal"],
        "issue_pattern": ["from-postgres"],
        "symptoms": ["pg-symptom"],
        "solution_steps": ["pg-step"],
        "required_customer_info": [""],
    }
)


def test_load_faq_kb_reads_local_csv_when_uri_unset(monkeypatch: pytest.MonkeyPatch):
    """Default: no env var set, read local CSV."""
    monkeypatch.delenv("FAQ_KB_URI", raising=False)
    df = srv._load_faq_kb()
    # The local CSV always has these columns; FAQ-PG-001 only appears in
    # the fake Postgres dataset, so its absence here confirms the local
    # branch ran.
    assert "faq_id" in df.columns
    assert "FAQ-PG-001" not in df["faq_id"].to_list()


def test_load_faq_kb_reads_postgres_when_uri_set(monkeypatch: pytest.MonkeyPatch):
    """When FAQ_KB_URI is set, dispatch to polars.read_database_uri."""

    monkeypatch.setenv("FAQ_KB_URI", "postgresql://demo:demo@127.0.0.1:5432/demo")
    with patch.object(srv.pl, "read_database_uri", return_value=_FAKE_PG_FAQ_DF) as read_db:
        df = srv._load_faq_kb()
    read_db.assert_called_once()
    # First positional arg is the SQL query.
    sql_arg = read_db.call_args.args[0]
    assert "faq_knowledge_base" in sql_arg.lower()
    assert df["faq_id"].to_list() == ["FAQ-PG-001"]


def test_faq_kb_resource_uses_postgres_when_uri_set(monkeypatch: pytest.MonkeyPatch):
    """End-to-end through the MCP resource: faq://kb returns Postgres rows."""

    monkeypatch.setenv("FAQ_KB_URI", "postgresql://demo:demo@127.0.0.1:5432/demo")
    with patch.object(srv.pl, "read_database_uri", return_value=_FAKE_PG_FAQ_DF):
        server = srv.build_server()
        body = _read_resource(server, "faq://kb")
    assert "FAQ-PG-001" in body
    assert body.startswith("faq_id,")
