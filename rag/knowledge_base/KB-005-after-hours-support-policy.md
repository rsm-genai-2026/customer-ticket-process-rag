---
doc_id: KB-005
title: After-Hours Support Coverage Policy
type: policy
owner: Support Operations
audience: it_team, it_specialists
last_updated: 2026-02-20
classification: internal
related_systems: all
---

# After-Hours Support Coverage Policy

Defines which tickets receive after-hours attention and which wait for
the next business-day shift.

## Coverage windows by shift

| Shift | Hours (UTC) | Coverage |
| --- | --- | --- |
| NA-Day | 14:00–22:00 | All tickets, all priorities |
| NA-Evening | 22:00–06:00 | P1 + P2 only; P3/P4 queue to NA-Day |
| EU-Day | 07:00–15:00 | All tickets, all priorities |
| APAC-Day | 00:00–08:00 | All tickets, all priorities |

A ticket arriving during a "P1+P2 only" window with a lower priority is
parked in the **Overnight Queue** and surfaces at the start of the next
NA-Day shift. The orchestrator's `next_action` field will still indicate
the correct downstream skill — the queue is a scheduling concept layered
on top of the workflow.

## Definition of P1 / P2 (this overrides any vendor SLA)

- **P1** — A production system is down for more than 10% of users, OR a
  T4 data exposure (KB-003) is suspected.
- **P2** — A production system is degraded (>20% slower than baseline)
  OR a customer-paying integration is failing for a Helios enterprise
  customer.

If a ticket is logged as P3 but exhibits P1/P2 symptoms, the on-call
analyst must reprioritise before routing. Do not wait for triage.

## Contaminated tickets

When a customer's ticket body contains a credential, MFA seed, or other
T4 value (see KB-003), the analyst must:

1. Mark the ticket **contaminated** in the action log
   (`extra.contaminated=true`).
2. Redact the value with `[REDACTED-T4]` before any reply.
3. Notify the security on-call within 60 minutes.
4. Force a credential rotation on the affected account.

The contamination flag must not be cleared without security review.

## On-call escalation paths

After-hours escalation paths are listed in KB-014 (Escalation Matrix).
The current after-hours specialist groups are: `identity_security`,
`network_infra`, and `application_engineering`. `billing_finance` and
`data_analytics` are NA-Day only.
