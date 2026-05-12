"""Validate the generated synthetic ticketing dataset.

Reads CSVs produced by ``generate_human_ticket_data.py`` and runs the
schema, foreign-key, ordering, and rate-band checks listed in
``docs/human-ticketing-dataset-plan.md``. Exits with status 1 if any
check fails.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import polars as pl

REQUIRED_FILES = [
    "raw/customers.csv",
    "raw/it_team_members.csv",
    "raw/it_specialists.csv",
    "raw/faq_knowledge_base.csv",
    "raw/submitted_tickets.csv",
    "processed/ticket_triage.csv",
    "processed/faq_checks.csv",
    "processed/specialist_escalations.csv",
    "processed/specialist_investigations.csv",
    "processed/customer_messages.csv",
    "processed/resolution_feedback.csv",
    "processed/ticket_lifecycle_events.csv",
    "processed/ticket_summary.csv",
    "dictionaries/categories.csv",
    "dictionaries/priority_rules.csv",
    "dictionaries/systems.csv",
    "dictionaries/status_codes.csv",
]

REQUIRED_COLUMNS = {
    "raw/submitted_tickets.csv": [
        "error_or_symptom_detail",
        "steps_already_tried",
        "expected_outcome",
        "availability_window",
        "attachment_description",
    ],
    "processed/ticket_triage.csv": [
        "intake_summary",
        "classification_evidence",
        "recommended_specialist_group",
        "target_first_response_at",
        "target_resolution_at",
    ],
    "processed/faq_checks.csv": [
        "search_terms",
        "candidate_faq_ids",
        "required_customer_info_available",
        "faq_application_reason",
    ],
    "processed/specialist_escalations.csv": [
        "requested_specialist_group",
        "handoff_summary",
        "specific_question_for_specialist",
        "customer_evidence_included",
    ],
    "processed/specialist_investigations.csv": [
        "diagnostic_steps",
        "evidence_reviewed",
        "customer_action_required",
        "confidence_score",
    ],
    "processed/customer_messages.csv": [
        "customer_action_required",
        "included_context",
        "follow_up_request",
        "quality_check_notes",
    ],
    "processed/resolution_feedback.csv": [
        "verified_by_it_member_id",
        "verification_notes",
        "next_action",
        "closure_reason",
    ],
    "processed/ticket_summary.csv": [
        "true_category",
        "first_resolution_source",
    ],
}


class CheckLog:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passes: list[str] = []

    def fail(self, msg: str) -> None:
        self.failures.append(msg)

    def ok(self, msg: str) -> None:
        self.passes.append(msg)


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def check_files_exist(data_dir: Path, log: CheckLog) -> None:
    for rel in REQUIRED_FILES:
        if not (data_dir / rel).exists():
            log.fail(f"missing file: {rel}")
        else:
            log.ok(f"file present: {rel}")


def load_csv(data_dir: Path, rel: str) -> pl.DataFrame:
    return pl.read_csv(data_dir / rel)


def check_required_columns(data_dir: Path, log: CheckLog) -> None:
    for rel, columns in REQUIRED_COLUMNS.items():
        df = load_csv(data_dir, rel)
        missing = [col for col in columns if col not in df.columns]
        if missing:
            log.fail(f"{rel} missing required columns: {', '.join(missing)}")
        else:
            log.ok(f"{rel} includes required workflow-detail columns")


def check_dataset(data_dir: Path, log: CheckLog) -> None:
    submitted = load_csv(data_dir, "raw/submitted_tickets.csv")
    triage = load_csv(data_dir, "processed/ticket_triage.csv")
    faq_checks = load_csv(data_dir, "processed/faq_checks.csv")
    escalations = load_csv(data_dir, "processed/specialist_escalations.csv")
    investigations = load_csv(data_dir, "processed/specialist_investigations.csv")
    messages = load_csv(data_dir, "processed/customer_messages.csv")
    feedback = load_csv(data_dir, "processed/resolution_feedback.csv")
    events = load_csv(data_dir, "processed/ticket_lifecycle_events.csv")
    summary = load_csv(data_dir, "processed/ticket_summary.csv")

    customers = load_csv(data_dir, "raw/customers.csv")
    it_team = load_csv(data_dir, "raw/it_team_members.csv")
    specialists = load_csv(data_dir, "raw/it_specialists.csv")
    faq_kb = load_csv(data_dir, "raw/faq_knowledge_base.csv")

    # Workflow-detail fields must carry operational content, not just state labels.
    detail_checks = [
        ("submitted ticket symptom detail", submitted, "error_or_symptom_detail"),
        ("submitted ticket steps already tried", submitted, "steps_already_tried"),
        ("triage intake summary", triage, "intake_summary"),
        ("triage classification evidence", triage, "classification_evidence"),
        ("FAQ search terms", faq_checks, "search_terms"),
        ("FAQ application reason", faq_checks, "faq_application_reason"),
        ("customer message action", messages, "customer_action_required"),
        ("feedback verification notes", feedback, "verification_notes"),
    ]
    for label, df, col in detail_checks:
        blanks = df.filter(pl.col(col).is_null() | (pl.col(col).str.len_chars() == 0))
        if blanks.height:
            log.fail(f"{blanks.height} rows have blank {label}")
        else:
            log.ok(f"{label} populated")

    # ticket_id uniqueness in submitted_tickets
    if submitted["ticket_id"].n_unique() != submitted.height:
        log.fail("submitted_tickets.ticket_id is not unique")
    else:
        log.ok("submitted_tickets.ticket_id is unique")

    ticket_ids = set(submitted["ticket_id"].to_list())
    n_tickets = len(ticket_ids)

    # Every ticket has one triage row
    if triage["ticket_id"].n_unique() != n_tickets or triage.height != n_tickets:
        log.fail(
            f"ticket_triage row count mismatch: tickets={n_tickets}, triage rows={triage.height}, "
            f"unique tickets in triage={triage['ticket_id'].n_unique()}"
        )
    else:
        log.ok("ticket_triage has exactly one row per ticket")

    # Every ticket has one FAQ check row
    if faq_checks["ticket_id"].n_unique() != n_tickets or faq_checks.height != n_tickets:
        log.fail(f"faq_checks row count mismatch: tickets={n_tickets}, faq_checks rows={faq_checks.height}")
    else:
        log.ok("faq_checks has exactly one row per ticket")

    # Every ticket has at least one customer message
    msg_tickets = set(messages["ticket_id"].to_list())
    missing_msg = ticket_ids - msg_tickets
    if missing_msg:
        log.fail(f"{len(missing_msg)} tickets have no customer message")
    else:
        log.ok("every ticket has at least one customer message")

    # FAQ-resolved tickets have a valid faq_id
    faq_ids = set(faq_kb["faq_id"].to_list())
    matched = faq_checks.filter(pl.col("faq_match_found"))
    bad_faq = matched.filter(~pl.col("faq_id").is_in(list(faq_ids)) | (pl.col("faq_id") == ""))
    if bad_faq.height > 0:
        log.fail(f"{bad_faq.height} faq matches reference invalid faq_id")
    else:
        log.ok("FAQ-matched rows reference valid faq_id values")

    # Escalated tickets have at least one escalation and at least one investigation
    esc_tickets = set(escalations["ticket_id"].to_list())
    inv_tickets = set(investigations["ticket_id"].to_list())
    missing_inv = esc_tickets - inv_tickets
    if missing_inv:
        log.fail(f"{len(missing_inv)} escalated tickets have no investigation row")
    else:
        log.ok("every escalated ticket has at least one investigation row")
    inv_only = inv_tickets - esc_tickets
    if inv_only:
        log.fail(f"{len(inv_only)} investigations reference tickets with no escalation row")
    else:
        log.ok("every investigation has a corresponding escalation row")

    # Foreign keys
    customer_ids = set(customers["customer_id"].to_list())
    it_member_ids = set(it_team["it_member_id"].to_list())
    specialist_ids = set(specialists["specialist_id"].to_list())

    bad_cust = submitted.filter(~pl.col("customer_id").is_in(list(customer_ids)))
    if bad_cust.height > 0:
        log.fail(f"{bad_cust.height} submitted tickets reference invalid customer_id")
    else:
        log.ok("submitted_tickets.customer_id all resolve in customers")

    bad_it = triage.filter(~pl.col("it_member_id").is_in(list(it_member_ids)))
    if bad_it.height > 0:
        log.fail(f"{bad_it.height} triage rows reference invalid it_member_id")
    else:
        log.ok("ticket_triage.it_member_id all resolve in it_team_members")

    bad_sp = escalations.filter(~pl.col("specialist_id").is_in(list(specialist_ids)))
    if bad_sp.height > 0:
        log.fail(f"{bad_sp.height} escalations reference invalid specialist_id")
    else:
        log.ok("specialist_escalations.specialist_id all resolve in it_specialists")

    bad_inv_sp = investigations.filter(~pl.col("specialist_id").is_in(list(specialist_ids)))
    if bad_inv_sp.height > 0:
        log.fail(f"{bad_inv_sp.height} investigations reference invalid specialist_id")
    else:
        log.ok("specialist_investigations.specialist_id all resolve in it_specialists")

    # Escalation/investigation detail must be actionable when a specialist is involved.
    for label, df, col in [
        ("escalation handoff summary", escalations, "handoff_summary"),
        ("specialist question", escalations, "specific_question_for_specialist"),
        ("specialist diagnostic steps", investigations, "diagnostic_steps"),
        ("specialist evidence reviewed", investigations, "evidence_reviewed"),
    ]:
        blanks = df.filter(pl.col(col).is_null() | (pl.col(col).str.len_chars() == 0))
        if blanks.height:
            log.fail(f"{blanks.height} rows have blank {label}")
        else:
            log.ok(f"{label} populated")

    # Timestamp ordering checks
    submitted_map = {r["ticket_id"]: _parse_ts(r["submitted_at"]) for r in submitted.iter_rows(named=True)}
    triage_map = {r["ticket_id"]: _parse_ts(r["triaged_at"]) for r in triage.iter_rows(named=True)}
    faq_map = {r["ticket_id"]: _parse_ts(r["faq_checked_at"]) for r in faq_checks.iter_rows(named=True)}

    bad_order = 0
    for tid in ticket_ids:
        if not (submitted_map[tid] < triage_map[tid] < faq_map[tid]):
            bad_order += 1
    if bad_order:
        log.fail(f"{bad_order} tickets violate submit < triage < FAQ check ordering")
    else:
        log.ok("submit < triage < FAQ check ordering holds for all tickets")

    bad_targets = 0
    for r in triage.iter_rows(named=True):
        submitted_at = submitted_map[r["ticket_id"]]
        first_target = _parse_ts(r["target_first_response_at"])
        resolution_target = _parse_ts(r["target_resolution_at"])
        if not (submitted_at < first_target <= resolution_target):
            bad_targets += 1
    if bad_targets:
        log.fail(f"{bad_targets} triage rows have invalid SLA target timestamps")
    else:
        log.ok("triage SLA target timestamps are valid")

    # Per-ticket cross-table ordering
    msg_per_ticket: dict[str, list[dict]] = {}
    for r in messages.iter_rows(named=True):
        msg_per_ticket.setdefault(r["ticket_id"], []).append(r)
    for v in msg_per_ticket.values():
        v.sort(key=lambda x: _parse_ts(x["sent_at"]))

    fb_per_ticket: dict[str, list[dict]] = {}
    for r in feedback.iter_rows(named=True):
        fb_per_ticket.setdefault(r["ticket_id"], []).append(r)
    for v in fb_per_ticket.values():
        v.sort(key=lambda x: _parse_ts(x["customer_reply_at"]))

    esc_per_ticket: dict[str, list[dict]] = {}
    for r in escalations.iter_rows(named=True):
        esc_per_ticket.setdefault(r["ticket_id"], []).append(r)
    for v in esc_per_ticket.values():
        v.sort(key=lambda x: _parse_ts(x["escalated_at"]))

    inv_per_ticket: dict[str, list[dict]] = {}
    for r in investigations.iter_rows(named=True):
        inv_per_ticket.setdefault(r["ticket_id"], []).append(r)
    for v in inv_per_ticket.values():
        v.sort(key=lambda x: _parse_ts(x["investigation_started_at"]))

    bad_msg_order = 0
    bad_fb_order = 0
    bad_esc_order = 0
    bad_inv_order = 0
    bad_close = 0
    for tid in ticket_ids:
        msgs = msg_per_ticket.get(tid, [])
        fbs = fb_per_ticket.get(tid, [])
        if msgs:
            first_msg = msgs[0]
            if not (faq_map[tid] < _parse_ts(first_msg["sent_at"])):
                bad_msg_order += 1
        if fbs:
            first_fb = fbs[0]
            if msgs and not (_parse_ts(msgs[0]["sent_at"]) < _parse_ts(first_fb["customer_reply_at"])):
                bad_fb_order += 1
        # specialist path ordering
        escs = esc_per_ticket.get(tid, [])
        invs = inv_per_ticket.get(tid, [])
        if escs:
            if not (faq_map[tid] <= _parse_ts(escs[0]["escalated_at"])):
                bad_esc_order += 1
            if invs:
                if not (_parse_ts(escs[0]["escalated_at"]) <= _parse_ts(invs[0]["investigation_started_at"])):
                    bad_inv_order += 1
                if not (_parse_ts(invs[0]["investigation_started_at"]) < _parse_ts(invs[0]["solution_created_at"])):
                    bad_inv_order += 1
        # closed_at must exist for every ticket (terminal)
        if not fbs:
            bad_close += 1
        else:
            terminal = fbs[-1]
            if not terminal["closed_at"]:
                bad_close += 1

    if bad_msg_order:
        log.fail(f"{bad_msg_order} tickets have first message before FAQ check")
    else:
        log.ok("first customer message is after FAQ check for all tickets")
    if bad_fb_order:
        log.fail(f"{bad_fb_order} tickets have customer reply before message sent")
    else:
        log.ok("customer reply happens after first message sent for all tickets")
    if bad_esc_order:
        log.fail(f"{bad_esc_order} escalations occur before FAQ check")
    else:
        log.ok("escalations occur at/after FAQ check")
    if bad_inv_order:
        log.fail(f"{bad_inv_order} investigations violate start<solution or escalation<start ordering")
    else:
        log.ok("investigation timestamps are well-ordered")
    if bad_close:
        log.fail(f"{bad_close} tickets are missing a terminal closed_at")
    else:
        log.ok("every ticket has a terminal closed_at")

    # ticket_summary row count
    if summary["ticket_id"].n_unique() != n_tickets or summary.height != n_tickets:
        log.fail(
            f"ticket_summary should have exactly one row per ticket "
            f"(got {summary.height} rows, {summary['ticket_id'].n_unique()} unique)"
        )
    else:
        log.ok("ticket_summary has exactly one row per ticket")

    # Event log: at least 4 events per ticket
    counts = events.group_by("ticket_id").agg(pl.len().alias("n"))
    short = counts.filter(pl.col("n") < 4)
    if short.height > 0:
        log.fail(f"{short.height} tickets have fewer than 4 lifecycle events")
    else:
        log.ok("every ticket has at least 4 lifecycle events")

    # Final-status: closed_at present in summary
    bad_summary_close = summary.filter(
        (pl.col("final_status") == "closed") & ((pl.col("closed_at").is_null()) | (pl.col("closed_at") == ""))
    )
    if bad_summary_close.height > 0:
        log.fail(f"{bad_summary_close.height} closed-status summary rows missing closed_at")
    else:
        log.ok("every closed summary row has closed_at populated")

    # Plausibility ranges
    n_faq_match = int(summary["faq_match_found"].sum())
    n_escalated = int(summary["escalated_flag"].sum())
    n_accepted = int(summary["resolution_accepted"].sum())
    n_reopened = int(summary["reopened_flag"].sum())
    faq_rate = n_faq_match / n_tickets
    esc_rate = n_escalated / n_tickets
    acc_rate = n_accepted / n_tickets
    reopen_rate = n_reopened / n_tickets

    def in_range(name: str, value: float, lo: float, hi: float) -> None:
        if lo <= value <= hi:
            log.ok(f"{name} = {value:.1%} in [{lo:.0%}, {hi:.0%}]")
        else:
            log.fail(f"{name} = {value:.1%} outside [{lo:.0%}, {hi:.0%}]")

    in_range("faq_match_rate", faq_rate, 0.35, 0.70)
    in_range("escalation_rate", esc_rate, 0.25, 0.60)
    in_range("resolution_acceptance_rate", acc_rate, 0.70, 0.95)
    in_range("reopen_rate", reopen_rate, 0.05, 0.25)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--data-dir", default="data", help="Directory containing the generated CSVs.")
    parser.add_argument("--verbose", action="store_true", help="Print every passing check.")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    log = CheckLog()

    check_files_exist(data_dir, log)
    if not log.failures:
        check_required_columns(data_dir, log)
    if not log.failures:
        check_dataset(data_dir, log)

    if args.verbose:
        for msg in log.passes:
            print(f"OK   {msg}")
    for msg in log.failures:
        print(f"FAIL {msg}", file=sys.stderr)

    print()
    print(f"Validation summary: {len(log.passes)} passed, {len(log.failures)} failed.")
    return 1 if log.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
