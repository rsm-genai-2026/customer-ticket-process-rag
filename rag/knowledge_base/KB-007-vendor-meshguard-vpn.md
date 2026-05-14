---
doc_id: KB-007
title: MeshGuard VPN — Vendor Contract Summary
type: vendor_contract
owner: Network Infrastructure
audience: it_specialists, it_leadership
last_updated: 2026-02-28
classification: internal
related_systems: VPN
---

# MeshGuard VPN — Vendor Contract Summary

## Contract

- **Vendor**: MeshGuard Networks, Inc.
- **Agreement**: MSA-MSG-2023-007, signed 2023-09-15.
- **Term**: 2023-10-01 through 2026-09-30. **Renewal decision due
  2026-07-31.**
- **Annual fee**: $148,000 USD, billed annually in advance.
- **Tunnel cap**: 800 concurrent tunnels (current 95th percentile usage:
  612).

## Support tier

- **Tier**: Standard business hours (08:00–18:00 PT, M–F).
- **After-hours severity-1**: paid add-on, $1,200 per incident.
- **Severity-1 first response**: 4 hours business hours, best-effort
  after-hours.
- **Support contact**: support@meshguard.example, ticket portal
  `support.meshguard.example`.
- **TAM**: Hassan Olin, weekly office hours Tuesdays 16:00 UTC.

## Maintenance windows

- **Scheduled maintenance**: every Sunday 02:00–04:00 UTC.
- **Emergency maintenance**: vendor may take the gateway offline with
  60 minutes' notice; notifications land in `#network-alerts` Slack.
- **VPN client auto-update**: monthly on the 15th at 03:00 client local
  time. Auto-update has caused tunnel-drop regressions twice in the
  past 18 months (KB-012 references one of them).

## Operational caveats

- `MeshGuard Connect 3.4.6` and earlier have a known regression on macOS
  15.3+ — see KB-002 for the workaround. The fix is in `3.4.7`.
- The gateway does not support DTLS — falling back to TLS adds ~120ms
  latency that affects voice/video calls. Tickets reporting Zoom
  jitter while on VPN are usually this; not a Zoom problem.
- The gateway logs the **source NAT** address only, not the per-user
  internal IP, so incident forensics requires correlating with the IDP
  session log within 24 hours (after which IDP rotates).

## Renewal posture (as of 2026-02-28)

The Network team is evaluating two alternatives (BastionRoute and
ZephyrNet) ahead of the 2026-07-31 renewal decision. Do not commit to
new MeshGuard-specific integrations until after that decision.
