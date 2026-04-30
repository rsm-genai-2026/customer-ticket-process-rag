"""Step 7: produce a specialist root cause + solution for an escalated ticket.

Reads the latest ``escalation_decisions.csv`` row (refusing if absent) and
builds a deterministic specialist solution from category/system templates.
Confidence is reduced when the upstream escalation flagged missing
information.

Run from the repo root::

    uv run python skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills.ticketing_common.ticketing_common import (  # noqa: E402
    append_action_log,
    append_csv_row,
    latest_working_row,
    now_iso,
    pipe_join,
    read_csv,
    require_ticket,
)

SKILL_NAME = "investigate-specialist-solution"
SOLUTIONS_TABLE = "specialist_solutions"

CATEGORY_TEMPLATES: dict[str, dict[str, object]] = {
    "login_access": {
        "root_cause": "Permission cache out of sync between SSO and the target system.",
        "diagnostic_steps": [
            "Reviewed audit log for recent SSO assertions",
            "Checked cached group membership in target system",
            "Confirmed user agent and browser version reported by customer",
        ],
        "evidence_reviewed": [
            "submitted_tickets.description",
            "audit log for last 24h",
            "SSO group propagation timing",
        ],
        "solution_summary": (
            "Force a refresh of the user's SSO group membership and clear server-side "
            "session cache; user signs back in to receive new permissions."
        ),
        "customer_action_required": (
            "Sign out completely, wait two minutes, then sign back in. Reply if the issue persists."
        ),
    },
    "password_reset": {
        "root_cause": "Privileged account reset workflow required manual approval.",
        "diagnostic_steps": [
            "Verified account type and approval policy",
            "Confirmed manager approval token",
            "Issued elevated reset link",
        ],
        "evidence_reviewed": ["account metadata", "approval policy"],
        "solution_summary": ("Reset the privileged account via the elevated workflow and confirm sign-in."),
        "customer_action_required": (
            "Use the reset link we just sent to set a new password and confirm you can sign in."
        ),
    },
    "billing_account": {
        "root_cause": "Tax engine cache stale after the customer's region change.",
        "diagnostic_steps": [
            "Inspected billing region setting",
            "Cleared tax engine cache for the account",
            "Recomputed affected invoice lines",
        ],
        "evidence_reviewed": ["account billing region", "invoice line items"],
        "solution_summary": (
            "Refresh the tax engine cache for the account and reissue the affected invoice with corrected line items."
        ),
        "customer_action_required": (
            "Confirm the reissued invoice totals match expectations and reply with any discrepancies."
        ),
    },
    "software_bug": {
        "root_cause": "Race condition in a background sync job; no permanent fix yet.",
        "diagnostic_steps": [
            "Reproduced in staging with a comparable record set",
            "Identified the sync job that re-queues affected records",
            "Logged a follow-up engineering ticket",
        ],
        "evidence_reviewed": [
            "ticket description",
            "staging reproduction",
            "recent deploy notes",
        ],
        "solution_summary": (
            "Apply the temporary mitigation (re-queue the affected records) so the user "
            "is unblocked. Engineering will track a permanent fix."
        ),
        "customer_action_required": (
            "Refresh the affected page once we confirm the records are re-queued; reply if the issue recurs."
        ),
    },
    "hardware_issue": {
        "root_cause": "Driver version on the device is incompatible with the current OS image.",
        "diagnostic_steps": [
            "Checked device model and current driver version via MDM",
            "Confirmed compatible driver in approved-driver catalog",
            "Pushed updated driver via MDM",
        ],
        "evidence_reviewed": ["device inventory", "MDM compliance status"],
        "solution_summary": (
            "Pushed the updated driver via MDM. The device should report healthy in the next inventory cycle."
        ),
        "customer_action_required": ("Restart the device and confirm the issue no longer occurs."),
    },
    "network_connectivity": {
        "root_cause": "Misconfigured route on the regional gateway.",
        "diagnostic_steps": [
            "Reviewed gateway logs for the user's region",
            "Identified the offending route entry",
            "Applied corrected route table entry",
        ],
        "evidence_reviewed": ["gateway logs", "route table snapshot"],
        "solution_summary": (
            "Apply the corrected route table entry and validate connectivity to the internal hosts the user reported."
        ),
        "customer_action_required": ("Reconnect to VPN and confirm the affected internal app loads normally."),
    },
    "email_calendar": {
        "root_cause": "Stale OAuth token in the calendar provider integration.",
        "diagnostic_steps": [
            "Verified token state in the identity provider",
            "Revoked the stale token",
            "Issued a fresh token and shared the device-prompt for reauthorization",
        ],
        "evidence_reviewed": ["OAuth token audit log"],
        "solution_summary": (
            "Revoke and reissue the OAuth token; the user reauthorizes the calendar app on their device."
        ),
        "customer_action_required": (
            "Reauthorize the calendar app on your device when prompted; confirm events are syncing again."
        ),
    },
    "data_reporting": {
        "root_cause": "Warehouse refresh job missed its scheduled run.",
        "diagnostic_steps": [
            "Inspected job scheduler for the affected pipeline",
            "Manually triggered the refresh",
            "Adjusted scheduler dependency to avoid recurrence",
        ],
        "evidence_reviewed": ["job scheduler logs", "warehouse run history"],
        "solution_summary": (
            "Manually triggered the warehouse refresh and tightened the scheduler so the next run does not slip."
        ),
        "customer_action_required": ("Reload the dashboard and confirm the latest data is visible."),
    },
    "security_request": {
        "root_cause": "Sensitive role required a manual access review.",
        "diagnostic_steps": [
            "Validated identity and role request",
            "Obtained manager and security approval",
            "Granted the scoped role with a documented expiry",
        ],
        "evidence_reviewed": ["identity record", "approval emails"],
        "solution_summary": (
            "Granted the requested scoped role with a documented expiry; access has been logged for audit."
        ),
        "customer_action_required": ("Sign in to confirm the new access works and reply if anything is missing."),
    },
    "other": {
        "root_cause": "Cross-team request requiring case-by-case handling.",
        "diagnostic_steps": [
            "Coordinated with the requested team",
            "Confirmed scope and any approvals",
            "Documented next steps",
        ],
        "evidence_reviewed": ["ticket description"],
        "solution_summary": ("Coordinated with the relevant team and produced tailored next steps for this request."),
        "customer_action_required": ("Review the next steps and confirm whether they are workable for you."),
    },
}


def load_investigation_context(data_dir: Path, out_dir: Path, ticket_id: str) -> dict:
    """Return ticket, escalation, specialist details, and dictionaries.

    Raises ``LookupError`` if no escalation exists for this ticket or
    the named specialist is not in ``it_specialists.csv``.
    """

    ticket = require_ticket(data_dir, ticket_id)
    escalation = latest_working_row(out_dir, "escalation_decisions", ticket_id)
    if escalation is None:
        raise LookupError(f"no escalation found for ticket {ticket_id}. Run the escalate-to-specialist skill first.")
    specialists = read_csv(data_dir, "raw/it_specialists.csv")
    sp_id = escalation.get("specialist_id", "")
    matching = specialists.filter(pl.col("specialist_id") == sp_id).to_dicts()
    if not matching:
        raise LookupError(f"escalation references specialist {sp_id!r} which is not in raw/it_specialists.csv.")
    return {
        "ticket": ticket,
        "escalation": escalation,
        "specialist": matching[0],
        "systems": read_csv(data_dir, "dictionaries/systems.csv"),
    }


def _category_for(context: dict) -> str:
    """Determine the category for template selection.

    Pulls the category from the escalation row's recorded triage if
    available, else falls back to the ticket's affected-system → category
    mapping. Defaults to ``other`` when nothing is available.
    """

    triage = latest_working_row(
        Path(context.get("_out_dir", ".")),
        "triage_decisions",
        context["ticket"]["ticket_id"],
    )
    if triage:
        return triage.get("assigned_category", "other") or "other"
    return "other"


def build_diagnostic_plan(context: dict) -> list[str]:
    """Return the list of diagnostic steps for the relevant category."""

    template = CATEGORY_TEMPLATES.get(context["category"], CATEGORY_TEMPLATES["other"])
    return list(template["diagnostic_steps"])


def infer_root_cause(context: dict) -> dict:
    """Return ``{"root_cause": str, "evidence_reviewed": list[str]}``."""

    template = CATEGORY_TEMPLATES.get(context["category"], CATEGORY_TEMPLATES["other"])
    return {
        "root_cause": template["root_cause"],
        "evidence_reviewed": list(template["evidence_reviewed"]),
    }


def build_solution_summary(context: dict, root_cause: dict) -> dict:
    """Build the customer-safe summary, customer action, and confidence.

    Confidence baseline depends on specialist seniority and is reduced
    when the upstream escalation flagged missing information.
    """

    template = CATEGORY_TEMPLATES.get(context["category"], CATEGORY_TEMPLATES["other"])
    seniority = (context["specialist"].get("seniority") or "").lower()
    base = {"junior": 0.65, "mid": 0.75, "senior": 0.85, "principal": 0.92}.get(seniority, 0.75)
    missing = str(context["escalation"].get("missing_information_flag", "")).lower() == "true"
    confidence = round(base * (0.85 if missing else 1.0), 2)
    notes = "Investigated within the documented runbook for this category. " + (
        "Customer did not provide reproduction details up front; added a request for missing information."
        if missing
        else "Customer-supplied detail was sufficient."
    )
    return {
        "solution_summary": template["solution_summary"],
        "customer_action_required": template["customer_action_required"],
        "confidence_score": confidence,
        "specialist_notes": notes,
        "requires_follow_up_flag": context["category"] == "software_bug",
    }


def write_specialist_solution(out_dir: Path, solution: dict) -> None:
    append_csv_row(Path(out_dir) / f"{SOLUTIONS_TABLE}.csv", solution)


def build_solution_row(context: dict) -> dict:
    """Run all helpers and produce the row written to the working CSV."""

    diagnostic_steps = build_diagnostic_plan(context)
    rc = infer_root_cause(context)
    summary = build_solution_summary(context, rc)
    return {
        "ticket_id": context["ticket"]["ticket_id"],
        "created_at": now_iso(),
        "skill_name": SKILL_NAME,
        "specialist_id": context["specialist"]["specialist_id"],
        "specialist_name": context["specialist"].get("name", ""),
        "specialist_group": context["specialist"].get("specialist_group", ""),
        "root_cause": rc["root_cause"],
        "diagnostic_steps": pipe_join(diagnostic_steps),
        "evidence_reviewed": pipe_join(rc["evidence_reviewed"]),
        "solution_summary": summary["solution_summary"],
        "customer_action_required": summary["customer_action_required"],
        "specialist_notes": summary["specialist_notes"],
        "requires_follow_up_flag": summary["requires_follow_up_flag"],
        "confidence_score": summary["confidence_score"],
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "raw/it_specialists.csv",
                "dictionaries/systems.csv",
                "working/escalation_decisions.csv",
                "working/triage_decisions.csv",
            ]
        ),
        "decision_summary": (
            f"specialist {context['specialist']['specialist_id']} produced solution "
            f"for category {context['category']}; confidence={summary['confidence_score']}"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="data/working")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()

    try:
        context = load_investigation_context(data_dir, out_dir, args.ticket_id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    context["_out_dir"] = str(out_dir)
    context["category"] = _category_for(context)
    row = build_solution_row(context)
    write_specialist_solution(out_dir, row)

    print(
        f"Specialist solution for {row['ticket_id']}:\n"
        f"  specialist        : {row['specialist_id']} ({row['specialist_name']})\n"
        f"  category          : {context['category']}\n"
        f"  root cause        : {row['root_cause']}\n"
        f"  diagnostic steps  : {row['diagnostic_steps']}\n"
        f"  evidence reviewed : {row['evidence_reviewed']}\n"
        f"  customer summary  : {row['solution_summary']}\n"
        f"  customer action   : {row['customer_action_required']}\n"
        f"  follow-up needed? : {row['requires_follow_up_flag']}\n"
        f"  confidence        : {row['confidence_score']}\n"
        f"\nNext valid action: draft-specialist-response."
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": row["ticket_id"],
            "created_at": row["created_at"],
            "skill_name": SKILL_NAME,
            "action": "specialist_solution_created",
            "inputs_used": row["inputs_used"],
            "decision_summary": row["decision_summary"],
            "confidence_score": row["confidence_score"],
            "notes": row["specialist_notes"],
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
