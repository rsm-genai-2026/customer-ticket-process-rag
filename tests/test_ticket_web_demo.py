from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "scripts" / "ticket_web_demo.py"
_spec = importlib.util.spec_from_file_location("ticket_web_demo", _MODULE_PATH)
assert _spec and _spec.loader
ticket_web_demo = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ticket_web_demo
_spec.loader.exec_module(ticket_web_demo)


def test_normalize_submission_applies_defaults() -> None:
    payload = {
        "subject": "Cannot export dashboard",
        "description": "The Analytics Dashboard CSV export is missing records.",
        "requester_email": "avery@example.com",
    }

    form = ticket_web_demo.normalize_submission(payload)

    assert form["requester_name"] == "Web Demo User"
    assert form["affected_system"] == "Customer Portal"
    assert form["customer_reported_urgency"] == "medium"
    assert form["sla_plan"] == "basic"
    assert form["error_or_symptom_detail"] == payload["description"]


def test_build_rows_match_csv_contract() -> None:
    form = ticket_web_demo.normalize_submission(
        {
            "requester_name": "Avery Chen",
            "requester_email": "avery@example.com",
            "company": "Northstar Retail",
            "account_tier": "premium",
            "subject": "Invoice looks wrong",
            "description": "The tax rate on the invoice looks wrong.",
            "affected_system": "Billing System",
            "customer_reported_urgency": "high",
        }
    )

    customer = ticket_web_demo.build_customer_row(form, "WEB-CUST-TEST")
    ticket = ticket_web_demo.build_ticket_row(form, "WEB-TEST", "WEB-CUST-TEST")

    assert customer["customer_id"] == "WEB-CUST-TEST"
    assert customer["sla_plan"] == "business"
    assert ticket["ticket_id"] == "WEB-TEST"
    assert ticket["customer_id"] == "WEB-CUST-TEST"
    assert ticket["channel"] == "web"
    assert ticket["attachment_flag"] == "false"


def test_prepare_run_data_copies_and_appends_rows(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "raw").mkdir(parents=True)
    (source / "dictionaries").mkdir()
    (source / "dictionaries" / "categories.csv").write_text("category\nother\n", encoding="utf-8")
    (source / "raw" / "customers.csv").write_text(
        "customer_id,customer_name,account_tier,industry,region,sla_plan,active_users,relationship_start_date\n"
        "CUST-001,Acme,standard,tech,NA,basic,10,2024-01-01\n",
        encoding="utf-8",
    )
    (source / "raw" / "submitted_tickets.csv").write_text(
        "ticket_id,submitted_at,customer_id,submitted_by_name,submitted_by_email,channel,subject,description,"
        "affected_system,customer_reported_urgency,business_impact_text,attachment_flag,error_or_symptom_detail,"
        "steps_already_tried,expected_outcome,availability_window,attachment_description\n",
        encoding="utf-8",
    )
    form = ticket_web_demo.normalize_submission(
        {
            "subject": "Test ticket",
            "description": "Something is not working.",
            "requester_email": "avery@example.com",
        }
    )
    ticket = ticket_web_demo.build_ticket_row(form, "WEB-TEST", "WEB-CUST-TEST")
    customer = ticket_web_demo.build_customer_row(form, "WEB-CUST-TEST")

    paths = ticket_web_demo.prepare_run_data(source, tmp_path / "wf-web-testcase", ticket, customer)

    assert paths.ticket_id == "WEB-TEST"
    with (paths.data_dir / "raw" / "submitted_tickets.csv").open(newline="", encoding="utf-8") as f:
        tickets = list(csv.DictReader(f))
    with (paths.data_dir / "raw" / "customers.csv").open(newline="", encoding="utf-8") as f:
        customers = list(csv.DictReader(f))
    assert tickets[-1]["ticket_id"] == "WEB-TEST"
    assert customers[-1]["customer_id"] == "WEB-CUST-TEST"
    assert (paths.run_dir / "metadata.json").exists()


def test_render_index_includes_example_ticket_dropdown() -> None:
    html = ticket_web_demo.render_index()

    assert 'id="example-ticket"' in html
    assert "faq_login_sso_loop" in html
    assert "human_expert_billing_api_502" in html


def test_process_submission_generates_customer_response(tmp_path: Path) -> None:
    result = ticket_web_demo.process_submission(
        {
            "requester_name": "Avery Chen",
            "requester_email": "avery@example.com",
            "company": "Northstar Retail",
            "account_tier": "premium",
            "subject": "Invoice total looks wrong",
            "description": "The invoice total is much higher than expected and the tax rate looks wrong.",
            "affected_system": "Billing System",
            "customer_reported_urgency": "medium",
            "business_impact_text": "Finance is holding payment until this is corrected.",
            "steps_already_tried": "Checked the invoice page twice.",
        },
        work_root=tmp_path,
    )

    assert result["ticketId"].startswith("WEB-")
    assert result["response"]["text"]
    assert result["response"]["deliveryId"].startswith(f"DEL-{result['ticketId']}")
    assert "send-customer-response" in [step["skill"] for step in result["steps"]]
    assert result["flow"]["nodes"]
    assert [node["id"] for node in result["flow"]["nodes"][:4]] == ["submit", "receive", "triage", "faq"]
    assert any(node["id"] == "feedback" and node["state"] == "current" for node in result["flow"]["nodes"])
    assert result["orchestrator"]["nextSkill"] == "verify-feedback-close-or-reopen"
    assert {branch["id"] for branch in result["flow"]["branches"]} == {"faq", "specialist"}
    assert all(step["label"] for step in result["steps"])
    assert result["narrative"]["sentences"]

    narrative_codes = [
        part["code"] for sentence in result["narrative"]["sentences"] for part in sentence["parts"] if "code" in part
    ]
    assert "check-faq-resolution" in narrative_codes

    io_by_skill = {record["skill"]: record for record in result["skillIO"]}
    assert "receive-ticket" in io_by_skill
    assert "check-faq-resolution" in io_by_skill
    faq_outputs = {field["label"]: field["value"] for field in io_by_skill["check-faq-resolution"]["outputs"]}
    assert "faq_match_found" in faq_outputs
    assert "recommended_next_step" in faq_outputs
    assert io_by_skill["check-faq-resolution"]["artifacts"] == ["working/faq_decisions.csv"]


def test_step_mode_runs_one_skill_at_a_time(tmp_path: Path) -> None:
    start = ticket_web_demo.start_submission(
        {
            "requester_name": "Avery Chen",
            "requester_email": "avery@example.com",
            "company": "Northstar Retail",
            "subject": "Invoice total looks wrong",
            "description": "The invoice total is much higher than expected and the tax rate looks wrong.",
            "affected_system": "Billing System",
        },
        work_root=tmp_path,
    )

    assert start["orchestrator"]["nextSkill"] == "receive-ticket"
    assert [node for node in start["flow"]["nodes"] if node["id"] == "receive"][0]["state"] == "current"
    assert start["skillIO"] == []
    assert start["narrative"]["sentences"][-1]["parts"][-2]["code"] == "receive-ticket"

    first_step = ticket_web_demo.process_step(
        {"workflow_run_id": start["workflowRunId"]},
        work_root=tmp_path,
    )

    assert first_step["orchestrator"]["lastStep"]["skill"] == "receive-ticket"
    assert first_step["orchestrator"]["nextSkill"] == "classify-prioritize-ticket"
    assert "received via" in first_step["orchestrator"]["lastStep"]["summary"]
    assert [node for node in first_step["flow"]["nodes"] if node["id"] == "receive"][0]["state"] == "completed"
    assert first_step["skillIO"][0]["skill"] == "receive-ticket"


def test_process_feedback_updates_flow_to_closed(tmp_path: Path) -> None:
    result = ticket_web_demo.process_submission(
        {
            "requester_name": "Avery Chen",
            "requester_email": "avery@example.com",
            "company": "Northstar Retail",
            "subject": "Invoice total looks wrong",
            "description": "The invoice total is much higher than expected and the tax rate looks wrong.",
            "affected_system": "Billing System",
        },
        work_root=tmp_path,
    )

    updated = ticket_web_demo.process_feedback(
        {
            "workflow_run_id": result["workflowRunId"],
            "feedback_text": "Thanks, that fixed it!",
        },
        work_root=tmp_path,
    )

    close_nodes = [node for node in updated["flow"]["nodes"] if node["id"] == "close"]
    assert close_nodes[0]["state"] == "completed"
    assert updated["feedback"]["nextAction"] == "close_ticket"
    assert "verify-feedback-close-or-reopen" in [step["skill"] for step in updated["steps"]]
