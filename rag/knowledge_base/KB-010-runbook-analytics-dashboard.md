---
doc_id: KB-010
title: Analytics Dashboard — Operations Runbook
type: runbook
owner: Data Platform
audience: it_team, it_specialists
last_updated: 2026-03-30
classification: internal
related_systems: Analytics Dashboard, CRM, Inventory App
---

# Analytics Dashboard — Operations Runbook

The Analytics Dashboard (`analytics.helios.internal`) is a Metabase
deployment fronted by a custom permissions layer (`helios-analytics-shim`).
Source data comes from the Helios warehouse (`snowflake://helios.data`),
which is loaded nightly from CRM, Billing System, and Inventory App.

## Health and freshness

- Dashboard liveness: `analytics.helios.internal/api/health` → 200.
- Warehouse freshness: tagged in the `staging.run_log` table — the
  `last_complete_at` column should be within 6 hours.
- Loader pipeline: `dbt-cloud` job `helios-warehouse-nightly`, scheduled
  03:00 UTC daily, average runtime 78 minutes.

## Known issues

### Issue 1 — "Yesterday" filter is wrong for APAC users

**Symptom**: A user in Sydney pulling the "yesterday" view at 10:00 local
time sees no data because the warehouse hasn't finished loading.

**Root cause**: The nightly loader runs in UTC; APAC business hours
start before it completes.

**Fix**: Tell users in APAC to pull "2 days ago" until the warehouse
freshness SLA improves. Engineering work to switch to streaming ingestion
is scheduled for Q3 2026.

### Issue 2 — Dashboard shows stale row counts after CRM bulk import

**Symptom**: After a CRM admin runs a bulk import, the dashboard's CRM
row counts lag by up to 24 hours.

**Root cause**: The CRM bulk import skips the change-data-capture stream
the loader subscribes to; only the next full snapshot picks up the new
rows.

**Fix**: CRM admins must notify Data Platform (`#data-platform-help`
Slack) before any bulk import so an out-of-cycle warehouse refresh can
be scheduled.

### Issue 3 — "Permission denied" on a dashboard the user used to access

**Symptom**: User reports a previously-accessible dashboard now returns
"Permission denied."

**Root cause**: The `helios-analytics-shim` reads permissions from the
IDP group list, which is cached for 6 hours. A recent group change has
not yet refreshed.

**Fix**: Tier-1 can force a permissions refresh by visiting
`analytics.helios.internal/admin/refresh-perms/{username}` (requires the
`analytics-admin` IDP group). If the issue persists past one refresh,
escalate to `data_analytics`.

## Common false alarms

- **"Numbers don't match my exported CSV"** — the dashboard rounds at
  display time, the CSV does not. Cross-check using the underlying SQL.
- **"Dashboard is slow"** between 03:00–04:30 UTC — this is the nightly
  loader contending for warehouse credits. Will self-resolve.
