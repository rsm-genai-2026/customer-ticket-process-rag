---
doc_id: KB-014
title: Specialist Escalation Matrix & On-Call Rota
type: reference
owner: Support Operations
audience: it_team, it_specialists
last_updated: 2026-04-29
classification: internal
related_systems: all
---

# Specialist Escalation Matrix & On-Call Rota

This matrix tells Tier-1 which specialist group owns which ticket
category, and the on-call contact path for after-hours.

## Category → Specialist group

| Ticket category | Primary group | Secondary group | After-hours? |
| --- | --- | --- | --- |
| `login_access` | `identity_security` | `application_engineering` | Yes |
| `password_reset` | `identity_security` | — | Yes |
| `billing_account` | `billing_finance` | — | No (NA-Day only) |
| `software_bug` | `application_engineering` | (system owner) | Yes |
| `hardware_issue` | `hardware_field` | — | No (NA-Day only) |
| `network_connectivity` | `network_infra` | — | Yes |
| `email_calendar` | `identity_security` | — | Yes |
| `data_reporting` | `data_analytics` | (system owner) | No (NA-Day only) |
| `security_request` | `identity_security` | — | Yes (P1/P2 only) |
| `other` | `application_engineering` | — | NA-Day only |

A ticket whose primary group is unavailable (PTO, no on-call) escalates
to the secondary group. If neither is available, the support lead
(Maya Chen) is paged.

## On-call rota (April–May 2026)

| Group | Week of 2026-04-29 | Week of 2026-05-06 |
| --- | --- | --- |
| `identity_security` | Tomo Yamada (SP-002) | Lin Walsh (SP-006) |
| `network_infra` | Olu Chen (SP-004) | Sana Kapoor (SP-009) |
| `application_engineering` | Hassan Vega (SP-008) | Hassan Vega (SP-008) |
| `data_analytics` | Erin Rao (SP-007) | Tomo Vega (SP-005) |
| `billing_finance` | Theo Eskola (SP-010) | Theo Eskola (SP-010) |

Updates land in `#support-oncall` Slack at 22:00 UTC every Sunday.

## Paging paths

- **P1** — PagerDuty service `helios-support-p1`. SLA: 5-minute
  acknowledgement.
- **P2** — PagerDuty service `helios-support-p2`. SLA: 15-minute
  acknowledgement during business hours, 30 minutes after-hours.
- **P3 / P4** — Ticket queue only, no page.
- **Security exposure (T3/T4)** — PagerDuty service `helios-secops`
  AND the security on-call DM (`@secops-oncall`).

## Cross-team contacts

| Need | Who | Where |
| --- | --- | --- |
| Manager verification for password reset | Employee's BambooHR manager | Slack DM |
| AuriLite vendor escalation | Priya Sundar | KB-006 |
| MeshGuard vendor escalation | Hassan Olin | KB-007 |
| QuotidianPay vendor escalation | Lena Kowalski | KB-008 |
| Legal review of a customer-facing reply | Legal on-call rota | `#legal-help` |
| Public communications (status page) | Comms on-call | `#comms-oncall` |

## Loop prevention reminder

Per the workflow rule, a ticket can be reopened at most once before
being closed as `unresolved`. The reopen-then-second-reject path always
involves the same specialist group as the first investigation, unless
the second rejection introduces a new root cause indication.
