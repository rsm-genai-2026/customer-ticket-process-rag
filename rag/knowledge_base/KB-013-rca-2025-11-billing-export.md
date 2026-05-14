---
doc_id: KB-013
title: RCA — Billing Export Bug 2025-11-22
type: rca
owner: Finance Operations
audience: it_specialists, finance_team
last_updated: 2025-12-15
classification: internal
related_systems: Billing System, Analytics Dashboard
incident_id: INC-2025-11-22-003
severity: P2
---

# Post-Incident Review — Billing Export Bug 2025-11-22

## TL;DR

Between 2025-11-01 and 2025-11-22, the QuotidianPay CSV invoice export
silently truncated 318 customer records to 32 characters of customer_id,
causing 12 enterprise customer reports to misallocate roughly $48K of
revenue. The bug was a vendor regression in QuotidianPay release 8.4.
Helios's downstream Analytics Dashboard pipeline did not detect the
collision because its data quality check only flagged `null` customer
IDs, not duplicates.

## Timeline

- **2025-11-04** — QuotidianPay release 8.4 deployed.
- **2025-11-18** — Finance Operations notices invoice totals don't
  match the Analytics Dashboard quarterly view. Initial assumption:
  warehouse load lag.
- **2025-11-22** — Finance Ops files TKT-15883 against the data_analytics
  team after deeper investigation rules out warehouse lag.
- **2025-11-23** — `data_analytics` specialist (Erin Rao) traces the
  problem to truncated IDs in the QuotidianPay export.
- **2025-11-25** — Helios files vendor escalation; QuotidianPay confirms
  the regression.
- **2025-12-08** — QuotidianPay 8.4.3 fixes the truncation.
- **2025-12-12** — Helios re-runs the export and corrects the
  affected enterprise customer reports.

## Root cause

Two failures combined:

1. **Vendor regression**: QuotidianPay 8.4 changed an internal
   `customer_id` column from `varchar(36)` to `varchar(32)` while keeping
   the API schema unchanged. Vendor truncated silently rather than
   erroring on overflow.
2. **Helios data quality check was too narrow**: The Analytics Dashboard
   load only validated that `customer_id IS NOT NULL`. Truncated IDs
   that collided with other valid 32-char prefixes passed the check
   while pointing to the wrong customer.

## Customer impact

- 12 enterprise customers received incorrect revenue reports for
  2025-11.
- All 12 were notified within 60 minutes of confirmation per KB-005
  contamination procedure (the report misallocation was treated as a T3
  exposure between customer accounts).
- Revenue corrections totalling $48,221 were issued in the 2025-12
  billing cycle.

## What went well

- KB-005's 60-minute disclosure clock was triggered correctly.
- Erin Rao traced the root cause within 24 hours of the ticket landing
  in `data_analytics`.

## What went badly

- The bug ran for three weeks before Finance Ops noticed.
- The "totals don't match" signal was initially dismissed as warehouse
  lag — there was no fast way to distinguish lag from a data-integrity
  issue.

## Action items

| # | Owner | Action | Status |
| --- | --- | --- | --- |
| 1 | Data Platform | Add duplicate-customer-id check to nightly load | Done 2025-12-04 |
| 2 | Finance Ops | Add row-count sanity check to month-end close | Done 2026-01-15 |
| 3 | Vendor Mgmt | Add API schema diff check to QuotidianPay release notes review | Done 2026-01-22 |
| 4 | Vendor Mgmt | Push QuotidianPay for stricter overflow handling | Open — vendor declined |

## Lessons for Tier-1

Tickets mentioning "wrong customer on invoice" or "two invoices look
identical" should be escalated to `billing_finance` immediately, not
routed through Tier-1 FAQ matching. The category looks deceptively
simple but the root cause is often vendor-side.
