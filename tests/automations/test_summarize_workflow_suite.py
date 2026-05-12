from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "automations" / "summarize-workflow-suite" / "scripts" / "summarize_workflow_suite.py"
_spec = importlib.util.spec_from_file_location("summarize_workflow_suite", _MODULE_PATH)
assert _spec and _spec.loader
suite = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = suite
_spec.loader.exec_module(suite)

FAQ_MATCH_JSON = json.dumps(
    {
        "faq_match_found": True,
        "faq_id": "FAQ-001",
        "confidence": 0.91,
        "required_customer_info_available": True,
        "reason": "Offline test fixture selected the FAQ branch.",
        "ticket_evidence": "test scenario",
        "faq_evidence": "FAQ-001",
    }
)
FAQ_NO_MATCH_JSON = json.dumps(
    {
        "faq_match_found": False,
        "faq_id": "",
        "confidence": 0.21,
        "required_customer_info_available": False,
        "reason": "Offline test fixture selected the specialist branch.",
        "ticket_evidence": "test scenario",
        "faq_evidence": "",
    }
)
SPECIALIST_SOLUTION_JSON = json.dumps(
    {
        "root_cause": "Provider-side state mismatch consistent with the ticket report.",
        "diagnostic_steps": [
            "Reviewed account audit log",
            "Replicated in staging",
            "Applied the documented mitigation",
        ],
        "evidence_reviewed": ["ticket description", "recent change history"],
        "solution_summary": ("Applied the documented mitigation; please sign back in to confirm the issue is gone."),
        "customer_action_required": ("Sign back in and reply to confirm whether the issue is resolved."),
        "confidence": 0.82,
        "requires_follow_up_flag": False,
        "reason": "Offline test fixture for the specialist branch.",
    }
)
FAQ_CANDIDATE_JSON = json.dumps(
    {
        "category": "billing_account",
        "system_name": "Billing System",
        "issue_pattern": "billing_api_502_after_invoice_post",
        "symptoms": ["502 from billing API", "Invoice post fails", "Retry succeeds intermittently"],
        "solution_steps": ["Retry the request once", "If still failing, contact billing on-call"],
        "required_customer_info": ["Invoice id", "Timestamp"],
        "confidence": 0.78,
        "reasoning": "Offline test fixture for FAQ candidate.",
    }
)


def test_suite_report_skill_runs_human_expert_scenario(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FAQ_RESOLUTION_MOCK_JSON", FAQ_NO_MATCH_JSON)
    monkeypatch.setenv("SPECIALIST_INVESTIGATION_MOCK_JSON", SPECIALIST_SOLUTION_JSON)
    monkeypatch.setenv("FAQ_CANDIDATE_MOCK_JSON", FAQ_CANDIDATE_JSON)

    summary = suite.run_suite(
        work_root=tmp_path / "runs",
        scenario_id="human_expert_billing_api_502",
    )

    assert summary["scenario_count"] == 1
    assert summary["failed_count"] == 0
    row = summary["rows"][0]
    assert row["actual_branch"] == "specialist"
    assert row["actual_terminal"] == "close_ticket"
    assert "escalate-to-specialist" in row["skills"]
    assert "Candidate FAQ after expert approval" in row["faq_candidate_note"]


def test_suite_report_writes_html(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FAQ_RESOLUTION_MOCK_JSON", FAQ_MATCH_JSON)

    summary = suite.run_suite(work_root=tmp_path / "runs", limit=2)
    report = suite.write_report(summary, tmp_path / "report.html")

    html = report.read_text(encoding="utf-8")
    assert report.exists()
    assert "Ticket Workflow Suite Report" in html
    assert "FAQ: customer portal SSO loop" in html
    assert "PASS" in html
