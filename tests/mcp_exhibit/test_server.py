"""Tests for the optional MCP exhibit server.

The MCP server is a thin wrapper: tools shell out to the existing skill
scripts (which have their own tests), and resources read CSVs from
data/. These tests confirm the wiring is correct without spinning up an
MCP transport, by exercising FastMCP's introspection API directly.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_exhibit import server as srv

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


def test_server_registers_expected_tools():
    server = srv.build_server()
    tools = _run(server.list_tools())
    assert sorted(t.name for t in tools) == [
        "check_faq_resolution",
        "investigate_specialist_solution",
    ]
    # Every tool must have a non-empty description so an LLM client can
    # decide when to call it.
    for t in tools:
        assert t.description, f"tool {t.name} has no description"


def test_server_registers_expected_resources_and_templates():
    server = srv.build_server()
    resources = _run(server.list_resources())
    templates = _run(server.list_resource_templates())

    assert sorted(str(r.uri) for r in resources) == ["faq://kb", "tickets://raw"]
    assert sorted(t.uriTemplate for t in templates) == [
        "dictionaries://{name}",
        "tickets://{ticket_id}",
        "working://{filename}",
    ]


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
    # Use whatever ticket id is in the first data row, so the test
    # stays valid when the synthetic dataset is regenerated.
    tickets_path = srv.DATA_DIR / "raw" / "submitted_tickets.csv"
    header, first_row = tickets_path.read_text().splitlines()[:2]
    first_ticket_id = first_row.split(",", 1)[0]

    server = srv.build_server()
    body = _read_resource(server, f"tickets://{first_ticket_id}")
    payload = json.loads(body)
    assert payload.get("ticket_id") == first_ticket_id


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
    # ../etc/passwd would escape data/working/ if filename validation were
    # missing. Confirm we get a clean error rather than a file read.
    body = _read_resource(server, "working://..%2Fetc%2Fpasswd")
    payload = json.loads(body)
    assert "filename must match" in payload["error"]


def test_working_returns_error_for_missing_csv(tmp_path: Path):
    server = srv.build_server()
    body = _read_resource(server, "working://definitely_not_here.csv")
    payload = json.loads(body)
    assert payload == {"error": "working file definitely_not_here.csv not found"}


# ---------------------------------------------------------------------------
# Tool wrapper: argument plumbing into the skill subprocess
# ---------------------------------------------------------------------------


def _fake_completed(stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_skill_rejects_empty_ticket_id():
    result = srv._run_skill(srv.SKILL_SCRIPTS["check_faq_resolution"], "")
    assert result["status"] == "error"
    assert result["error"]["code"] == "missing_ticket_id"


def test_run_skill_invokes_subprocess_with_expected_argv():
    envelope = {"status": "ok", "ticket_id": "TKT-00042", "outputs": {"faq_match_found": True}}
    fake = _fake_completed(stdout=json.dumps(envelope) + "\n")

    with patch.object(srv.subprocess, "run", return_value=fake) as run_mock:
        result = srv._run_skill(srv.SKILL_SCRIPTS["check_faq_resolution"], "TKT-00042")

    assert result == envelope
    argv = run_mock.call_args.args[0]
    # The contract documented in lib/ticketing_common.make_skill_parser:
    assert "--json" in argv
    assert ["--ticket-id", "TKT-00042"] == argv[argv.index("--ticket-id") : argv.index("--ticket-id") + 2]
    assert ["--mode", "demo"] == argv[argv.index("--mode") : argv.index("--mode") + 2]
    assert ["--idempotency-mode", "replace"] == argv[
        argv.index("--idempotency-mode") : argv.index("--idempotency-mode") + 2
    ]
    # data-dir points at the repo's data/, out-dir is the isolated temp.
    assert argv[argv.index("--data-dir") + 1] == str(srv.DATA_DIR)
    assert argv[argv.index("--out-dir") + 1] == str(srv.OUT_DIR)


def test_run_skill_returns_synthetic_error_on_empty_stdout():
    fake = _fake_completed(stdout="", returncode=2, stderr="boom")
    with patch.object(srv.subprocess, "run", return_value=fake):
        result = srv._run_skill(srv.SKILL_SCRIPTS["check_faq_resolution"], "TKT-00042")
    assert result["status"] == "error"
    assert result["error"]["code"] == "no_output"
    assert result["error"]["message"] == "boom"


def test_run_skill_returns_synthetic_error_on_invalid_json():
    fake = _fake_completed(stdout="not json at all\n")
    with patch.object(srv.subprocess, "run", return_value=fake):
        result = srv._run_skill(srv.SKILL_SCRIPTS["check_faq_resolution"], "TKT-00042")
    assert result["status"] == "error"
    assert result["error"]["code"] == "invalid_envelope"


@pytest.mark.parametrize(
    "tool_name,script_key",
    [
        ("check_faq_resolution", "check_faq_resolution"),
        ("investigate_specialist_solution", "investigate_specialist_solution"),
    ],
)
def test_tool_call_round_trips_envelope_through_mcp(tool_name: str, script_key: str):
    envelope = {"status": "ok", "ticket_id": "TKT-00001", "outputs": {"demo": True}}

    def fake_run_skill(script_path, ticket_id):
        assert script_path == srv.SKILL_SCRIPTS[script_key]
        assert ticket_id == "TKT-00001"
        return envelope

    with patch.object(srv, "_run_skill", side_effect=fake_run_skill):
        server = srv.build_server()
        content = _run(server.call_tool(tool_name, {"ticket_id": "TKT-00001"}))

    # FastMCP serializes a dict-returning tool's result as one TextContent
    # whose text is the JSON-encoded envelope.
    assert len(content) == 1
    assert json.loads(content[0].text) == envelope
