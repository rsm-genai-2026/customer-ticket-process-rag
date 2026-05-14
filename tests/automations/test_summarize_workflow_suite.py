"""End-to-end tests for the summarize-workflow-suite automation.

LLM calls hit the real TritonAI gateway. Assertions are structural
(scenario completes, expected branch reached, report renders) rather
than content-specific. Requires ``TRITONAI_API_KEY`` in ``.env``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "automations" / "summarize-workflow-suite" / "scripts" / "summarize_workflow_suite.py"
_spec = importlib.util.spec_from_file_location("summarize_workflow_suite", _MODULE_PATH)
assert _spec and _spec.loader
suite = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = suite
_spec.loader.exec_module(suite)


def test_suite_report_skill_runs_human_expert_scenario(tmp_path: Path) -> None:
    """The human-expert billing scenario should land on the specialist branch."""
    summary = suite.run_suite(
        work_root=tmp_path / "runs",
        scenario_id="human_expert_billing_api_502",
    )

    assert summary["scenario_count"] == 1
    assert summary["failed_count"] == 0
    row = summary["rows"][0]
    # This scenario is designed to escalate; the LLM should agree.
    assert row["actual_branch"] == "specialist"
    assert row["actual_terminal"] == "close_ticket"
    assert "escalate-to-specialist" in row["skills"]
    assert isinstance(row["faq_candidate_note"], str) and row["faq_candidate_note"].strip()


def test_suite_report_writes_html(tmp_path: Path) -> None:
    summary = suite.run_suite(work_root=tmp_path / "runs", limit=2)
    report = suite.write_report(summary, tmp_path / "report.html")

    html = report.read_text(encoding="utf-8")
    assert report.exists()
    assert "Ticket Workflow Suite Report" in html
    assert "FAQ: customer portal SSO loop" in html
