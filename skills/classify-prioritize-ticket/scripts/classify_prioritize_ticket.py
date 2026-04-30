"""Step 2: classify a ticket into a category and assign a priority.

Deterministic logic:

* Category — keyword overlap against per-category keyword lists, plus a
  bonus when the affected system is typical of that category.
* Priority — additive score over customer tier, customer-reported urgency,
  and category sensitivity. Bucketed into low / medium / high / urgent.
* SLA targets — added to triage time using ``priority_rules.target_*``.

Run from the repo root::

    uv run python skills/classify-prioritize-ticket/scripts/classify_prioritize_ticket.py \\
        --ticket-id TKT-00042
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills.ticketing_common.ticketing_common import (  # noqa: E402
    DEFAULT_MODE,
    MODE_DEMO,
    MODE_LIVE,
    STATUS_OK,
    STATUS_SKIPPED,
    append_action_log,
    append_csv_row,
    default_step_id,
    default_workflow_run_id,
    emit_envelope,
    find_step_row,
    make_envelope,
    needs_human_review,
    now_iso,
    pipe_join,
    read_csv,
    replace_step_row,
    require_ticket,
)

SKILL_NAME = "classify-prioritize-ticket"
TRIAGE_TABLE = "triage_decisions"
NEXT_ACTION = "check-faq-resolution"

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "login_access": [
        "login",
        "log in",
        "logging in",
        "sign in",
        "signin",
        "signing in",
        "credentials",
        "lockout",
        "locked",
        "sso",
        "redirect loop",
        "session expired",
    ],
    "password_reset": [
        "password",
        "reset",
        "passcode",
        "forgot password",
        "expired link",
    ],
    "billing_account": [
        "invoice",
        "billing",
        "payment",
        "tax",
        "charge",
        "refund",
        "monthly close",
    ],
    "software_bug": [
        "bug",
        "error",
        "crash",
        "broken",
        "freeze",
        "freezes",
        "stuck",
        "not working",
        "missing data",
        "stale data",
    ],
    "hardware_issue": [
        "mouse",
        "keyboard",
        "printer",
        "laptop",
        "battery",
        "monitor",
        "headset",
        "device",
        "peripheral",
    ],
    "network_connectivity": [
        "vpn",
        "wifi",
        "wi-fi",
        "network",
        "connection",
        "latency",
        "slow",
        "disconnect",
        "drops",
    ],
    "email_calendar": [
        "email",
        "calendar",
        "outlook",
        "gmail",
        "auto-reply",
        "out of office",
        "shared mailbox",
        "invite",
    ],
    "data_reporting": [
        "dashboard",
        "report",
        "export",
        "csv",
        "analytics",
        "qbr",
        "filter",
    ],
    "security_request": [
        "security",
        "suspicious",
        "audit",
        "breach",
        "access for",
        "new hire",
        "new employee",
        "vendor",
    ],
    "other": [],
}

CATEGORY_TYPICAL_SYSTEMS: dict[str, set[str]] = {
    "login_access": {"Customer Portal", "Identity Provider", "CRM"},
    "password_reset": {"Identity Provider", "CRM", "Customer Portal"},
    "billing_account": {"Billing System", "Customer Portal"},
    "software_bug": {"CRM", "Customer Portal", "Inventory App", "Analytics Dashboard", "Billing System"},
    "hardware_issue": set(),
    "network_connectivity": {"VPN", "Identity Provider"},
    "email_calendar": {"Email"},
    "data_reporting": {"Analytics Dashboard", "CRM", "Inventory App"},
    "security_request": {"Identity Provider"},
    "other": set(),
}

TIER_SCORES = {"standard": 0, "premium": 1, "enterprise": 2}
URGENCY_SCORES = {"low": 0, "medium": 1, "high": 2, "critical": 3}
CATEGORY_BUMP = {"security_request": 1, "network_connectivity": 1, "password_reset": -1}


def load_triage_inputs(data_dir: Path, ticket_id: str) -> dict:
    """Return everything needed to triage a ticket as a single dict."""

    ticket = require_ticket(data_dir, ticket_id)
    customers = read_csv(data_dir, "raw/customers.csv")
    customer_rows = customers.filter(pl.col("customer_id") == ticket["customer_id"]).to_dicts()
    if not customer_rows:
        raise LookupError(f"ticket {ticket_id} references unknown customer {ticket['customer_id']!r}")
    categories = read_csv(data_dir, "dictionaries/categories.csv")
    priority_rules = read_csv(data_dir, "dictionaries/priority_rules.csv")
    return {
        "ticket": ticket,
        "customer": customer_rows[0],
        "categories": categories,
        "priority_rules": priority_rules,
    }


def _ticket_text(ticket: dict) -> str:
    parts = [
        ticket.get("subject", ""),
        ticket.get("description", ""),
        ticket.get("error_or_symptom_detail", ""),
        ticket.get("steps_already_tried", ""),
        ticket.get("expected_outcome", ""),
        ticket.get("business_impact_text", ""),
    ]
    return " ".join(p for p in parts if p).lower()


def score_categories(context: dict, categories: pl.DataFrame) -> pl.DataFrame:
    """Score every category and return a sorted DataFrame.

    Output columns: ``category``, ``score``, ``matched_keywords``,
    ``system_match``. Sorted descending by ``score`` then by category id
    for determinism.
    """

    text = _ticket_text(context["ticket"])
    affected = context["ticket"].get("affected_system", "")
    cat_ids = {row["category"]: row["category_id"] for row in categories.to_dicts()}

    rows = []
    for cat in categories["category"].to_list():
        keywords = CATEGORY_KEYWORDS.get(cat, [])
        matched: list[str] = []
        for kw in keywords:
            pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, text):
                matched.append(kw)
        keyword_score = len(matched)
        system_match = bool(affected) and affected in CATEGORY_TYPICAL_SYSTEMS.get(cat, set())
        score = keyword_score + (1.5 if system_match else 0)
        rows.append(
            {
                "category": cat,
                "category_id": cat_ids.get(cat, ""),
                "score": score,
                "matched_keywords": pipe_join(matched),
                "system_match": system_match,
            }
        )
    return pl.DataFrame(rows).sort(["score", "category_id"], descending=[True, False])


def assign_priority(context: dict, priority_rules: pl.DataFrame, category: str) -> dict:
    """Return ``{"priority": ..., "reason": ..., "score": int}``.

    Higher customer tier and higher reported urgency monotonically
    increase priority — so an enterprise-tier ticket with the same
    urgency and category as a standard-tier ticket will never rank lower.
    """

    customer = context["customer"]
    ticket = context["ticket"]
    tier = (customer.get("account_tier") or "").lower()
    urgency = (ticket.get("customer_reported_urgency") or "").lower()
    score = TIER_SCORES.get(tier, 0) + URGENCY_SCORES.get(urgency, 0) + CATEGORY_BUMP.get(category, 0)
    if score >= 5:
        priority = "urgent"
    elif score >= 3:
        priority = "high"
    elif score >= 1:
        priority = "medium"
    else:
        priority = "low"
    # Make sure the chosen priority exists in the rules table — defensive
    # in case the dictionaries change in the future.
    valid = set(priority_rules["priority"].to_list())
    if priority not in valid:
        priority = "medium"
    reason = f"tier={tier or '?'}, urgency={urgency or '?'}, category={category}; score={score}"
    return {"priority": priority, "reason": reason, "score": score}


def _sla_targets(triage_iso: str, priority: str, priority_rules: pl.DataFrame) -> tuple[str, str]:
    """Compute first-response and resolution target timestamps."""

    rule = priority_rules.filter(pl.col("priority") == priority).to_dicts()[0]
    base = datetime.fromisoformat(triage_iso)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    first = base + timedelta(hours=float(rule["target_first_response_hours"]))
    resolved = base + timedelta(hours=float(rule["target_resolution_hours"]))
    return first.isoformat(), resolved.isoformat()


def build_triage_decision(context: dict) -> dict:
    """Run scoring + priority + SLA and return the working-table row."""

    scored = score_categories(context, context["categories"])
    top = scored.row(0, named=True)
    second_score = float(scored.row(1, named=True)["score"]) if scored.height >= 2 else 0.0
    top_score = float(top["score"])

    if top_score == 0.0:
        chosen_category = "other"
        evidence = "no keyword or system match — fell back to 'other'"
        confidence = 0.30
    else:
        chosen_category = top["category"]
        evidence_bits = []
        if top["matched_keywords"]:
            evidence_bits.append(f"keywords=[{top['matched_keywords']}]")
        if top["system_match"]:
            evidence_bits.append(f"system={context['ticket'].get('affected_system', '')}")
        evidence = "; ".join(evidence_bits) or "weak match"
        # Confidence: higher when top score clearly exceeds runner-up,
        # capped at 0.95.
        margin = top_score - second_score
        confidence = max(0.40, min(0.95, 0.55 + 0.10 * top_score + 0.10 * margin))

    cat_row = context["categories"].filter(pl.col("category") == chosen_category).to_dicts()[0]
    recommended_group = cat_row["default_specialist_group"]

    priority_info = assign_priority(context, context["priority_rules"], chosen_category)

    triage_iso = now_iso()
    target_first, target_resolution = _sla_targets(triage_iso, priority_info["priority"], context["priority_rules"])

    decision_summary = (
        f"category={chosen_category}; priority={priority_info['priority']}; specialist_group={recommended_group}"
    )
    decision = {
        "ticket_id": context["ticket"]["ticket_id"],
        "created_at": triage_iso,
        "skill_name": SKILL_NAME,
        "assigned_category": chosen_category,
        "assigned_priority": priority_info["priority"],
        "recommended_specialist_group": recommended_group,
        "target_first_response_at": target_first,
        "target_resolution_at": target_resolution,
        "classification_evidence": evidence,
        "priority_reason": priority_info["reason"],
        "confidence_score": round(confidence, 2),
        "inputs_used": pipe_join(
            [
                "raw/submitted_tickets.csv",
                "raw/customers.csv",
                "dictionaries/categories.csv",
                "dictionaries/priority_rules.csv",
            ]
        ),
        "decision_summary": decision_summary,
    }
    return decision


def write_triage_decision(out_dir: Path, decision: dict) -> None:
    append_csv_row(Path(out_dir) / f"{TRIAGE_TABLE}.csv", decision)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="data/working")
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument("--step-id", default="")
    parser.add_argument("--mode", choices=[MODE_LIVE, MODE_DEMO], default=DEFAULT_MODE)
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--idempotency-mode", choices=["skip", "replace"], default="skip")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    workflow_run_id = args.workflow_run_id or default_workflow_run_id()
    step_id = args.step_id or default_step_id(SKILL_NAME)

    existing = find_step_row(out_dir, TRIAGE_TABLE, workflow_run_id, step_id)
    if existing and args.idempotency_mode == "skip":
        env = make_envelope(
            status=STATUS_SKIPPED,
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            next_action=NEXT_ACTION,
            outputs={"existing_row": existing},
            artifact_refs=[f"working/{TRIAGE_TABLE}.csv"],
        )
        emit_envelope(
            env,
            as_json=args.as_json,
            text_summary=(
                f"triage already recorded for workflow_run_id={workflow_run_id} step_id={step_id} — skipping."
            ),
        )
        return 0

    try:
        context = load_triage_inputs(data_dir, args.ticket_id)
    except KeyError as exc:
        env = make_envelope(
            status="error",
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            error={"code": "ticket_not_found", "message": str(exc)},
        )
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, LookupError) as exc:
        env = make_envelope(
            status="error",
            skill_name=SKILL_NAME,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            ticket_id=args.ticket_id,
            error={"code": "missing_data", "message": str(exc)},
        )
        emit_envelope(env, as_json=args.as_json, text_summary=f"error: {exc}")
        if not args.as_json:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    decision = build_triage_decision(context)
    decision["workflow_run_id"] = workflow_run_id
    decision["step_id"] = step_id
    if args.idempotency_mode == "replace":
        replace_step_row(
            Path(out_dir) / f"{TRIAGE_TABLE}.csv",
            decision,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
    else:
        write_triage_decision(out_dir, decision)

    review_required = needs_human_review(decision["confidence_score"])
    text_summary = (
        f"Triage for {decision['ticket_id']}:\n"
        f"  category            : {decision['assigned_category']}  "
        f"(confidence={decision['confidence_score']})\n"
        f"  priority            : {decision['assigned_priority']}\n"
        f"  specialist group    : {decision['recommended_specialist_group']}\n"
        f"  classification      : {decision['classification_evidence']}\n"
        f"  priority reason     : {decision['priority_reason']}\n"
        f"  first-response by   : {decision['target_first_response_at']}\n"
        f"  resolution by       : {decision['target_resolution_at']}\n"
        f"  review required?    : {review_required}\n"
        f"\nNext valid action: {NEXT_ACTION}."
    )

    append_action_log(
        out_dir,
        {
            "ticket_id": decision["ticket_id"],
            "created_at": decision["created_at"],
            "skill_name": SKILL_NAME,
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "action": "triage_decision",
            "inputs_used": decision["inputs_used"],
            "decision_summary": decision["decision_summary"],
            "confidence_score": decision["confidence_score"],
            "needs_human_review": "true" if review_required else "false",
            "notes": f"mode={args.mode}",
        },
    )

    env = make_envelope(
        status=STATUS_OK,
        skill_name=SKILL_NAME,
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        ticket_id=decision["ticket_id"],
        next_action=NEXT_ACTION,
        confidence=decision["confidence_score"],
        review_required=review_required,
        artifact_refs=[f"working/{TRIAGE_TABLE}.csv"],
        outputs={
            "assigned_category": decision["assigned_category"],
            "assigned_priority": decision["assigned_priority"],
            "recommended_specialist_group": decision["recommended_specialist_group"],
            "target_first_response_at": decision["target_first_response_at"],
            "target_resolution_at": decision["target_resolution_at"],
            "classification_evidence": decision["classification_evidence"],
            "priority_reason": decision["priority_reason"],
        },
    )
    emit_envelope(env, as_json=args.as_json, text_summary=text_summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
