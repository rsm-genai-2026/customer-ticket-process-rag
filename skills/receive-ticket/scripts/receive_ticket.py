"""Step 1 of the human IT ticketing workflow: receive and summarize a ticket.

Loads the ticket from ``raw/submitted_tickets.csv`` and the customer from
``raw/customers.csv``, prints a human-readable intake summary, and appends
one row to ``data/working/ticket_action_log.csv``.

Run from the repo root::

    uv run python skills/receive-ticket/scripts/receive_ticket.py --ticket-id TKT-00042
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills.ticketing_common.ticketing_common import (  # noqa: E402
    append_action_log,
    now_iso,
    pipe_join,
    read_csv,
    require_ticket,
)

SKILL_NAME = "receive-ticket"


def load_ticket_context(data_dir: Path, ticket_id: str) -> dict:
    """Return ``{"ticket": ..., "customer": ...}`` for the given ticket.

    Raises ``KeyError`` if the ticket does not exist and ``LookupError``
    if the ticket references a customer that is not in ``customers.csv``.
    """

    ticket = require_ticket(data_dir, ticket_id)
    customers = read_csv(data_dir, "raw/customers.csv")
    matching = customers.filter(customers["customer_id"] == ticket["customer_id"]).to_dicts()
    if not matching:
        raise LookupError(f"ticket {ticket_id} references unknown customer {ticket['customer_id']!r}")
    return {"ticket": ticket, "customer": matching[0]}


def build_intake_summary(context: dict) -> dict:
    """Build a structured intake summary from the ticket+customer context.

    The summary is the same shape regardless of the ticket so downstream
    consumers (the human IT member or Claude relaying to the user) can
    rely on a stable structure.
    """

    t = context["ticket"]
    c = context["customer"]
    has_attachment = str(t.get("attachment_flag", "")).lower() == "true"
    attachment_desc = t.get("attachment_description") or ""
    return {
        "ticket_id": t["ticket_id"],
        "submitted_at": t["submitted_at"],
        "channel": t.get("channel", ""),
        "customer_id": c["customer_id"],
        "customer_name": c["customer_name"],
        "account_tier": c.get("account_tier", ""),
        "sla_plan": c.get("sla_plan", ""),
        "region": c.get("region", ""),
        "industry": c.get("industry", ""),
        "subject": t.get("subject", ""),
        "affected_system": t.get("affected_system", ""),
        "customer_reported_urgency": t.get("customer_reported_urgency", ""),
        "business_impact_text": t.get("business_impact_text", ""),
        "symptom_detail": t.get("error_or_symptom_detail", ""),
        "steps_already_tried": t.get("steps_already_tried", "") or "(none reported)",
        "expected_outcome": t.get("expected_outcome", ""),
        "availability_window": t.get("availability_window", ""),
        "attachment": attachment_desc if has_attachment else "(none)",
    }


def render_summary(summary: dict) -> str:
    """Render an intake summary as plain text for the IT-team operator.

    Stable layout: a header line followed by labeled fields. Empty fields
    print as ``(blank)`` so the operator notices missing data rather than
    silently moving on.
    """

    def show(value: object) -> str:
        s = "" if value is None else str(value)
        return s if s.strip() else "(blank)"

    lines = [
        f"Ticket {summary['ticket_id']} received via {summary['channel'] or '(unknown channel)'}",
        f"  Submitted at        : {show(summary['submitted_at'])}",
        f"  Customer            : {show(summary['customer_name'])} "
        f"(tier={show(summary['account_tier'])}, sla={show(summary['sla_plan'])}, "
        f"region={show(summary['region'])})",
        f"  Subject             : {show(summary['subject'])}",
        f"  Affected system     : {show(summary['affected_system'])}",
        f"  Reported urgency    : {show(summary['customer_reported_urgency'])}",
        f"  Business impact     : {show(summary['business_impact_text'])}",
        f"  Symptom detail      : {show(summary['symptom_detail'])}",
        f"  Steps already tried : {show(summary['steps_already_tried'])}",
        f"  Expected outcome    : {show(summary['expected_outcome'])}",
        f"  Availability window : {show(summary['availability_window'])}",
        f"  Attachment          : {show(summary['attachment'])}",
        "",
        "Next valid action: classify and prioritize this ticket (skill: classify-prioritize-ticket).",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="data/working")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()

    try:
        context = load_ticket_context(data_dir, args.ticket_id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = build_intake_summary(context)
    print(render_summary(summary))

    append_action_log(
        out_dir,
        {
            "ticket_id": summary["ticket_id"],
            "created_at": now_iso(),
            "skill_name": SKILL_NAME,
            "action": "intake_summary",
            "inputs_used": pipe_join(["raw/submitted_tickets.csv", "raw/customers.csv"]),
            "decision_summary": (
                f"received via {summary['channel']}; "
                f"urgency={summary['customer_reported_urgency']}; "
                f"system={summary['affected_system']}"
            ),
            "confidence_score": "",
            "notes": "",
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
