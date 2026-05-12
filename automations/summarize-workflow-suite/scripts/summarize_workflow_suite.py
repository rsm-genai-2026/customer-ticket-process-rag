"""Run curated ticket scenarios and write an HTML workflow report.

This script is intentionally a skill rather than part of the web server:
it is a reusable evaluation activity. The web orchestrator is still the
system under test, and this script simply feeds it representative tickets.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from time import perf_counter

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data.examples.ticket_scenarios import EXAMPLE_TICKETS, scenario_by_id  # noqa: E402
from scripts.orchestrator import TicketWorkflowOrchestrator  # noqa: E402

DEFAULT_WORK_ROOT = Path("/tmp/customer-ticket-process-suite")
DEFAULT_REPORT = Path("/tmp/customer-ticket-process-suite-report.html")


def _skills(result: dict) -> list[str]:
    """Return completed skill names from a workflow summary."""

    return [step.get("skill", "") for step in result.get("steps", []) if step.get("skill")]


def _initial_branch(result: dict) -> str:
    """Infer which branch produced the first customer response."""

    skills = _skills(result)
    if "draft-faq-response" in skills:
        return "faq"
    if "escalate-to-specialist" in skills:
        return "specialist"
    return "unknown"


def _review_required(result: dict) -> bool:
    """Return True when any completed skill asked for human review."""

    return any(bool(step.get("reviewRequired")) for step in result.get("steps", []))


def _faq_candidate_note(scenario: dict, result: dict) -> str:
    """Describe the FAQ-backlog candidate for specialist paths.

    Specialist answers are not written into the FAQ knowledge base
    automatically. They become a suggested backlog item that a human expert
    should approve before the FAQ is updated.
    """

    skills = _skills(result)
    if "escalate-to-specialist" not in skills:
        return ""
    solution = result.get("solution") or {}
    root_cause = solution.get("rootCause") or "specialist solution"
    if scenario.get("faq_backlog_candidate"):
        return f"Candidate FAQ after expert approval: {root_cause}"
    return f"Specialist path completed: {root_cause}"


def _auto_approve_hitl_gates(orchestrator: TicketWorkflowOrchestrator, workflow_run_id: str, current: dict) -> dict:
    """Drive past supervisor-review gates for sweep/test runs.

    The web demo pauses on ``review-specialist-draft`` and
    ``approve-faq-promotion`` because a real supervisor must click. The
    suite report is a synthetic sweep, not an interactive demo, so it
    auto-approves both gates without edits to keep the end-to-end
    summary running.
    """

    seen_gates: set[str] = set()
    while True:
        next_step = current.get("orchestrator", {}).get("nextStep", "")
        if next_step in seen_gates:
            # Defensive: never approve the same gate twice in a row.
            return current
        if next_step == "review-specialist-draft":
            seen_gates.add(next_step)
            current = orchestrator.review_specialist_draft(workflow_run_id, decision="approve")
            seen_gates.discard(next_step)
        elif next_step == "approve-faq-promotion":
            seen_gates.add(next_step)
            current = orchestrator.approve_faq_promotion(workflow_run_id, decision="approve")
            seen_gates.discard(next_step)
        else:
            return current


def run_scenario(scenario: dict, *, work_root: Path) -> dict:
    """Run one scenario through the orchestrator and return a report row."""

    started = perf_counter()
    orchestrator = TicketWorkflowOrchestrator(work_root=work_root)
    first_response = orchestrator.run_until_response(scenario["payload"])
    workflow_run_id = first_response["workflowRunId"]
    first_response = _auto_approve_hitl_gates(orchestrator, workflow_run_id, first_response)
    current = first_response
    feedback_actions: list[str] = []

    for feedback_text in scenario.get("feedback_sequence", []):
        current = orchestrator.process_feedback(workflow_run_id, feedback_text)
        current = _auto_approve_hitl_gates(orchestrator, workflow_run_id, current)
        feedback_actions.append(current.get("feedback", {}).get("nextAction", ""))

    expected_branch = scenario.get("expected_initial_branch", "")
    actual_branch = _initial_branch(first_response)
    expected_terminal = scenario.get("expected_terminal_action", "")
    actual_terminal = feedback_actions[-1] if feedback_actions else "awaiting_feedback"
    passed = actual_branch == expected_branch and actual_terminal == expected_terminal

    return {
        "id": scenario["id"],
        "label": scenario["label"],
        "scenario": scenario["scenario"],
        "workflow_run_id": workflow_run_id,
        "ticket_id": first_response["ticketId"],
        "expected_branch": expected_branch,
        "actual_branch": actual_branch,
        "expected_terminal": expected_terminal,
        "actual_terminal": actual_terminal,
        "passed": passed,
        "review_required": _review_required(current),
        "skills": _skills(current),
        "feedback_actions": feedback_actions,
        "faq_candidate_note": _faq_candidate_note(scenario, current),
        "elapsed_seconds": round(perf_counter() - started, 2),
    }


def select_scenarios(*, limit: int | None = None, scenario_id: str = "") -> list[dict]:
    """Return scenarios requested by the CLI."""

    if scenario_id:
        return [scenario_by_id(scenario_id)]
    scenarios = list(EXAMPLE_TICKETS)
    if limit is not None:
        scenarios = scenarios[:limit]
    return scenarios


def run_suite(*, work_root: Path, limit: int | None = None, scenario_id: str = "") -> dict:
    """Run selected scenarios and return aggregate results."""

    scenarios = select_scenarios(limit=limit, scenario_id=scenario_id)
    rows = [run_scenario(scenario, work_root=work_root) for scenario in scenarios]
    passed = sum(1 for row in rows if row["passed"])
    return {
        "scenario_count": len(rows),
        "passed_count": passed,
        "failed_count": len(rows) - passed,
        "rows": rows,
    }


def _html_row(row: dict) -> str:
    status = "pass" if row["passed"] else "fail"
    review = "yes" if row["review_required"] else "no"
    skills = " -> ".join(row["skills"])
    feedback = ", ".join(row["feedback_actions"]) or "awaiting_feedback"
    cells = [
        row["label"],
        status.upper(),
        row["actual_branch"],
        row["expected_branch"],
        row["actual_terminal"],
        row["expected_terminal"],
        review,
        feedback,
        row["faq_candidate_note"],
        skills,
    ]
    body = "".join(f"<td>{html.escape(str(value))}</td>" for value in cells)
    return f'<tr class="{status}">{body}</tr>'


def render_html_report(summary: dict) -> str:
    """Return a complete HTML report for suite results."""

    rows_html = "\n".join(_html_row(row) for row in summary["rows"])
    failed = [row for row in summary["rows"] if not row["passed"]]
    failures = "".join(
        f"<li>{html.escape(row['label'])}: expected {html.escape(row['expected_branch'])}/"
        f"{html.escape(row['expected_terminal'])}, got {html.escape(row['actual_branch'])}/"
        f"{html.escape(row['actual_terminal'])}</li>"
        for row in failed
    )
    if not failures:
        failures = "<li>No failures.</li>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Ticket Workflow Suite Report</title>
  <style>
    body {{
      margin: 32px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #17201b;
      background: #f6f7f4;
    }}
    h1 {{ margin-bottom: 4px; }}
    .summary {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin: 18px 0;
    }}
    .metric {{
      border: 1px solid #d8dfd8;
      border-radius: 8px;
      background: #fff;
      padding: 12px 14px;
      min-width: 140px;
    }}
    .metric span {{
      display: block;
      color: #5b665f;
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .metric strong {{ font-size: 22px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #fff;
      border: 1px solid #d8dfd8;
    }}
    th, td {{
      border-bottom: 1px solid #e4e9e4;
      padding: 9px 10px;
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }}
    th {{
      background: #eef4f0;
      font-size: 12px;
      text-transform: uppercase;
      color: #5b665f;
    }}
    tr.pass td:first-child {{ border-left: 4px solid #4d9b5f; }}
    tr.fail td:first-child {{ border-left: 4px solid #b94a48; }}
    code {{
      background: #eef4f0;
      border: 1px solid #cdd8d2;
      border-radius: 5px;
      padding: 1px 5px;
    }}
  </style>
</head>
<body>
  <h1>Ticket Workflow Suite Report</h1>
  <p>Generated from the curated examples in <code>data/examples/ticket_scenarios.py</code>.</p>
  <div class="summary">
    <div class="metric"><span>Scenarios</span><strong>{summary["scenario_count"]}</strong></div>
    <div class="metric"><span>Passed</span><strong>{summary["passed_count"]}</strong></div>
    <div class="metric"><span>Failed</span><strong>{summary["failed_count"]}</strong></div>
  </div>
  <h2>Failures</h2>
  <ul>{failures}</ul>
  <h2>Scenario Details</h2>
  <table>
    <thead>
      <tr>
        <th>Scenario</th>
        <th>Status</th>
        <th>Actual branch</th>
        <th>Expected branch</th>
        <th>Actual terminal</th>
        <th>Expected terminal</th>
        <th>Review?</th>
        <th>Feedback actions</th>
        <th>FAQ backlog note</th>
        <th>Skills run</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</body>
</html>"""


def write_report(summary: dict, report_path: Path) -> Path:
    """Write the HTML report and return the resolved path."""

    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_html_report(summary), encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--scenario-id", default="")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    summary = run_suite(
        work_root=Path(args.work_root),
        limit=args.limit,
        scenario_id=args.scenario_id,
    )
    report_path = write_report(summary, Path(args.report))
    payload = {
        "status": "ok" if summary["failed_count"] == 0 else "failed",
        "report": str(report_path),
        **summary,
    }

    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"Ran {summary['scenario_count']} scenarios: "
            f"{summary['passed_count']} passed, {summary['failed_count']} failed."
        )
        print(f"HTML report: {report_path}")
        for row in summary["rows"]:
            if not row["passed"]:
                print(
                    f"FAILED {row['label']}: expected "
                    f"{row['expected_branch']}/{row['expected_terminal']}, got "
                    f"{row['actual_branch']}/{row['actual_terminal']}"
                )
    return 0 if summary["failed_count"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
