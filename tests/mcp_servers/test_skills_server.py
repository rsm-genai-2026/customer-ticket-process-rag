"""Tests for the MCP skills server.

The skills server is a thin wrapper: each tool shells out to the
existing skill scripts (which have their own tests under
``tests/skills/``). These tests confirm the wiring by exercising
FastMCP's introspection API directly, without spinning up an MCP
transport or making real LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import patch

import pytest

from mcp_servers import skills_server as srv


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_skills_server_registers_expected_tools():
    server = srv.build_server()
    tools = _run(server.list_tools())
    assert sorted(t.name for t in tools) == [
        "check_faq_resolution",
        "investigate_specialist_solution",
    ]
    for t in tools:
        assert t.description, f"tool {t.name} has no description"


def test_skills_server_has_no_resources():
    """The skills server serves work only — no data resources."""
    server = srv.build_server()
    resources = _run(server.list_resources())
    templates = _run(server.list_resource_templates())
    assert resources == []
    assert templates == []


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

    assert len(content) == 1
    assert json.loads(content[0].text) == envelope
