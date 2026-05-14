---
doc_id: KB-015
title: Change Calendar — 2026 Q2
type: change_calendar
owner: Change Management Board
audience: it_team, it_specialists
last_updated: 2026-04-25
classification: internal
related_systems: all
---

# Change Calendar — 2026 Q2 (April–June)

The change calendar is the authoritative source for planned outages and
upgrades. When a customer's symptom timing aligns with a scheduled
window, Tier-1 should reference the calendar before deep-dive
troubleshooting.

## Recurring windows

| Window | When | Affects |
| --- | --- | --- |
| MeshGuard VPN maintenance | Every Sunday 02:00–04:00 UTC | VPN tunnels |
| AuriLite IDP minor release | First Tuesday of month, 02:00 UTC | Auth, SSO redirects |
| Warehouse loader | Daily 03:00–04:30 UTC | Analytics Dashboard freshness |
| QuotidianPay maintenance | Last Friday of month, 22:00–23:00 UTC | Billing System |

## Scheduled changes

### April 2026

- **2026-04-12 (Sun) 02:00–04:30 UTC** — Customer Portal database
  failover drill. Read-only mode for ~10 minutes mid-window.
- **2026-04-19 (Sun) 03:00–04:00 UTC** — Identity Provider TLS cert
  rotation. SSO clients with cached discovery docs may need a refresh
  (see KB-009 Issue 1).
- **2026-04-30 (Wed) 23:00 UTC** — CRM bulk import: 18,000 records
  from a sales-data refresh. Analytics Dashboard counts will lag for
  ~24 hours (KB-010 Issue 2).

### May 2026

- **2026-05-03 (Sun) 02:00–06:00 UTC** — Helios Kubernetes platform
  upgrade. Customer Portal expected to roll for ~6 minutes during the
  window; Analytics Dashboard rolls for ~3 minutes.
- **2026-05-10 (Sun) 02:00–04:00 UTC** — Routine MeshGuard maintenance,
  no extra scope.
- **2026-05-12 (Tue) 02:00–05:00 UTC** — AuriLite May minor release
  (extended window — vendor advised possible 3-hour pre-prod outage).
- **2026-05-19 (Tue) 14:00–15:00 UTC** — Analytics Dashboard upgrade to
  Metabase 0.50. New permission caching behaviour (KB-010 Issue 3
  cache window reduced from 6 hours to 1 hour).
- **2026-05-29 (Fri) 22:00–23:00 UTC** — QuotidianPay monthly maintenance.

### June 2026

- **2026-06-07 (Sun) 02:00–06:00 UTC** — Customer Portal upgrade to
  Next.js 14.3. Expect a 15-minute read-only window mid-upgrade.
- **2026-06-15 (Mon) 09:00 UTC** — Inventory App scheduled outage for
  Postgres major version upgrade (13 → 16). Expected duration 90
  minutes.
- **2026-06-30 (Tue) 02:00 UTC** — Final IDP minor before July renewal
  decision window.

## Change-freeze periods

- **2026-05-20 → 2026-05-22** — End-of-month financial close. No
  changes to Billing System or Analytics Dashboard.
- **2026-06-26 → 2026-06-30** — Half-year close. Same scope as above
  plus CRM.

## How to use this calendar

If a customer reports an issue and the timing falls inside a window
above, the first reply should reference the change rather than starting
a root-cause investigation. Example template:

> "Thanks for the report. We're currently in a scheduled maintenance
> window for {system} (see {KB-id}). We expect normal operation by
> {time UTC}. We'll reach back out if anything changes."
