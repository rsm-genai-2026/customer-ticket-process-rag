---
doc_id: KB-012
title: RCA — VPN Outage 2025-09-14
type: rca
owner: Network Infrastructure
audience: it_specialists, it_leadership
last_updated: 2025-10-03
classification: internal
related_systems: VPN, Identity Provider
incident_id: INC-2025-09-14-001
severity: P1
---

# Post-Incident Review — VPN Outage 2025-09-14

## TL;DR

A MeshGuard client auto-update pushed at 03:00 UTC on Sunday
2025-09-14 contained a regression that dropped tunnels on any client
running macOS 14.6+ after the first Wi-Fi network change. ~340 employees
were unable to reach internal services for ~6 hours, of which 2 hours
fell inside the NA-Day shift.

## Timeline (UTC)

- **03:00** — MeshGuard pushes client `3.4.6` via auto-update.
- **05:12** — First ticket arrives from a Berlin employee
  (TKT-12041): "VPN drops every 60 seconds on my Mac."
- **06:30** — `network_infra` on-call observes a pattern; opens
  INC-2025-09-14-001 at P2.
- **08:15** — Volume hits 90+ open tickets; incident upgraded to P1.
- **09:40** — MeshGuard support confirms regression; promises
  hotfix `3.4.7`.
- **11:55** — Hotfix `3.4.7` published.
- **13:00** — Helios pushes hotfix via MDM (KB-004); residual ticket
  arrivals taper.

## Root cause

`MeshGuard Connect 3.4.6` shipped with a refactor of the
`network-monitor` daemon that incorrectly invalidated the tunnel session
key on every `kSCNetworkReachabilityFlagsTransientConnection` event,
which fires on every Wi-Fi roam. macOS-only because the daemon's Linux
and Windows builds use a different event API.

## What went well

- The Sunday 02:00–04:00 UTC window is exactly intended to absorb this
  kind of vendor push — most users were asleep.
- The MDM-driven hotfix push reached 92% of impacted devices within 4
  hours of release.

## What went badly

- Helios had no canary path for MeshGuard client updates. We rely on
  the vendor's QA, and on this occasion the vendor had no macOS
  network-roaming test in CI.
- The auto-update default in our managed `MeshGuard Connect` profile
  was `auto`. After this incident it has been changed to
  `staged-canary` (50 employees first, the rest 24 hours later).

## Action items

| # | Owner | Action | Status |
| --- | --- | --- | --- |
| 1 | Network Infra | Switch client auto-update to `staged-canary` | Done 2025-09-20 |
| 2 | Workplace IT | Add a macOS roaming test to the BYOD/MDM matrix | Done 2025-11-04 |
| 3 | Network Infra | Press MeshGuard for a per-platform release notes feed | In progress |
| 4 | Procurement | Factor MeshGuard QA gaps into 2026 renewal eval | Done 2026-02-28 (KB-007) |

## Lessons for Tier-1

Tickets describing "VPN drops every 60 seconds on Mac" within ~24 hours
of a MeshGuard client release are almost always vendor regressions. Check
`status.meshguard.example` and the `#network-alerts` Slack before
spending time on the user's network configuration.
