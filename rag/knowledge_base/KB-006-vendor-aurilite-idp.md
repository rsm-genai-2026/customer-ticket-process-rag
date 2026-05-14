---
doc_id: KB-006
title: AuriLite Identity Provider — Vendor Contract Summary
type: vendor_contract
owner: Identity & Security
audience: it_specialists, it_leadership
last_updated: 2026-03-12
classification: internal
related_systems: Identity Provider, Customer Portal, CRM, Email
---

# AuriLite IDP — Vendor Contract Summary

This is a summary of the active AuriLite IDP master services agreement
(MSA-AURI-2024-014, signed 2024-08-01, renewed 2026-08-01 for two years).
The full PDF lives in Vendor Vault at `vendors/aurilite/MSA-2024-014.pdf`.

## Key contract terms

- **Term**: 2026-08-01 through 2028-07-31.
- **Auto-renewal**: 12 months, opt-out window 60 days before term end.
- **Annual fee**: $412,000 USD, billed quarterly.
- **Seat cap**: 4,500 active identities (current usage: 3,820 as of 2026-Q1).
- **Overage rate**: $9.20 per seat per month, billed in arrears.

## Support tier

- **Tier**: Premium 24/7.
- **Severity-1 first response**: 15 minutes.
- **Severity-2 first response**: 1 hour during business hours, 4 hours
  after-hours.
- **Support hotline**: +1-415-555-0148 (PIN: stored in Vendor Vault).
- **Account manager**: Priya Sundar — `priya.sundar@aurilite.example`.

## Service-level commitments (SLA)

- **Authentication availability**: 99.95% monthly, measured at the
  `/oauth2/token` endpoint.
- **Push-MFA delivery**: 99.5% of pushes delivered within 5 seconds.
- **SLA credits**: 10% of monthly fee per 0.1 percentage point below the
  99.95% target, capped at one full month's fee.

## Data processing addendum (DPA)

- **Data residency**: EU customer data is pinned to `eu-west-3` region by
  contract. T3 data may not cross regions without written approval.
- **Breach notification SLA**: AuriLite must notify Helios within 24
  hours of suspected breach; Helios must notify enterprise customers
  within 60 minutes per KB-005.
- **Sub-processor list**: refreshed quarterly at
  `aurilite.com/legal/subprocessors`.

## Known operational caveats

- AuriLite pushes a mandatory minor release on the first Tuesday of
  every month at 02:00 UTC. Pre-prod has historically been broken for
  3–6 hours after these pushes; do not schedule customer-facing
  identity changes on the first Tuesday.
- The "Magic Link" feature is **not enabled** for Helios — it conflicts
  with the Tier-1 password reset SOP (KB-001).
