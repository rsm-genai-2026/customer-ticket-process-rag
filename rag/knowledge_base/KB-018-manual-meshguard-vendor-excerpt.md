---
doc_id: KB-018
title: MeshGuard Connect — Vendor Admin Manual Excerpt (v3.4)
type: vendor_manual
owner: Network Infrastructure
audience: it_specialists
last_updated: 2026-04-05
classification: internal_use_only
related_systems: VPN
source: MeshGuard Connect Admin Manual, Chapter 9 (excerpt, used under MSA-MSG-2023-007)
---

# MeshGuard Connect — Admin Manual (Chapter 9 Excerpt)

This is an internal-use excerpt from the MeshGuard Connect 3.4 admin
manual. Quoted under our MSA. Do not redistribute outside Helios.

## Chapter 9 — Diagnostic Reference

### 9.1 Client error codes

| Code | Meaning | Typical cause | Resolution |
| --- | --- | --- | --- |
| `ERR_HANDSHAKE_DRIFT` | Clock skew > 30s vs gateway NTP | Local NTP drift or VM paused | Force `ntpdate` and retry |
| `ERR_CERT_REVOKED` | Profile certificate revoked | Manual revocation or expiry | Re-issue per KB-002 |
| `ERR_CERT_UNKNOWN_CA` | Helios root CA missing | New device, BYOD enrolment skipped | Re-run MDM enrolment (KB-004) |
| `ERR_PROFILE_MISMATCH` | Profile not authorised for this gateway | Trying to use a contractor profile against a vendor gateway | Confirm correct profile in KB-002 |
| `ERR_NO_ROUTE` | Tunnel established but no route returned | Misconfigured ACL on gateway | Specialist escalation |
| `ERR_DTLS_NEGOTIATE` | Falling back to TLS | Carrier blocks UDP 4500 | Expected; user can ignore |
| `ERR_LICENSE_EXHAUSTED` | Concurrent tunnel cap hit | All 800 tunnels in use | Page network on-call |

### 9.2 Gateway-side log fields

The gateway emits one log line per session event. Fields, in order:

1. `timestamp` — RFC3339 UTC.
2. `gateway_id` — one of `vpn-gw-1`, `vpn-gw-2`, `vpn-gw-3`.
3. `session_id` — opaque, used for correlation.
4. `user_cert_cn` — the certificate common name (= IDP username).
5. `event` — one of `connect`, `disconnect`, `roam`, `keepalive`, `error`.
6. `client_version` — e.g., `3.4.7-macos`.
7. `client_ip` — public IP of the client (post-NAT).
8. `error_code` — populated only on `event=error`.

Note that the per-user *internal* IP is **not** logged at the gateway —
the source-NAT IP is what appears. To correlate forensically, join on
`session_id` against the IDP session log (KB-007 §"Operational caveats").

### 9.3 Recommended client deployment modes

The vendor recommends one of:

- **`auto`** — Auto-update on release. Not recommended for fleets > 50.
  Helios uses this only on the canary group.
- **`staged-canary`** — 10% canary for 24h, then full fleet. Helios
  default since 2025-09-20 (KB-012).
- **`pinned`** — Pinned to a specific minor version. Use only when
  troubleshooting a vendor regression.

### 9.4 Tunnel limits and license model

Each concurrent tunnel consumes one license. The license is released
either by clean disconnect or after a 4-minute idle timeout. A user with
two devices simultaneously connected counts as **two** tunnels, not one.

The gateway returns `ERR_LICENSE_EXHAUSTED` when the global cap is hit.
Helios's contracted cap is 800; the 95th percentile observed usage in
2026-Q1 is 612. See KB-007 for the renewal posture.

### 9.5 Operational warnings (vendor)

> "The Keep-Alive option in client profile settings must be enabled for
> any deployment that crosses cellular ↔ Wi-Fi network boundaries.
> Failure to enable Keep-Alive on macOS 14.6+ will produce session drops
> on every network change." — MeshGuard Connect Admin Manual 3.4, §9.5

(This is the warning that Helios's 2025-09 RCA flagged as missing from
the QA test matrix — see KB-012.)
