---
doc_id: KB-003
title: Helios Data Classification Policy
type: policy
owner: Security & Compliance
audience: all_staff
last_updated: 2026-01-09
classification: internal
related_systems: all
---

# Data Classification Policy

Helios classifies every piece of data into one of four tiers. The tier
determines who can access the data, how it must be stored, and what
support analysts can include in a ticket response.

| Tier | Label | Examples | May appear in ticket reply? |
| --- | --- | --- | --- |
| T1 | Public | Marketing pages, public docs | Yes |
| T2 | Internal | Org charts, runbooks, this policy | Yes, to employee requesters only |
| T3 | Confidential | Customer PII, billing details, ticket bodies | Only the requester's own data |
| T4 | Restricted | Credentials, encryption keys, payroll, audit findings | **Never** in a ticket reply |

## Rules for support analysts

1. **Never paste a T4 value into a ticket reply.** This includes API keys,
   password hashes, MFA seeds, and the contents of the IDP `secrets`
   table. If a customer pastes one in their ticket, treat the ticket as
   contaminated — see KB-005 §3.
2. T3 data from one customer must never be referenced when replying to a
   different customer, even as an example.
3. Internal runbooks (T2) can be summarised in replies to *employee*
   requesters but not to external customers.
4. When in doubt, escalate to `identity_security` before sending the
   reply. The reviewer is paid to read your draft.

## Storage rules

- T3 and T4 data must live in systems explicitly listed in the
  `data_locations.csv` register. Local CSV exports to laptops are not
  permitted.
- T4 data must be encrypted at rest with the Helios KMS key
  (`alias/helios-t4-2026`). Manual rotation cadence: 180 days. The
  rotation playbook lives in the security wiki under
  `Security → KMS → Tier-4 Rotation`.

## Reporting suspected exposures

Suspected T3 or T4 exposures must be reported to the security on-call via
PagerDuty within 60 minutes of discovery — see KB-014 for the
escalation matrix. The 60-minute window is contractual under the AuriLite
IDP DPA (KB-006) and several enterprise customer contracts.
