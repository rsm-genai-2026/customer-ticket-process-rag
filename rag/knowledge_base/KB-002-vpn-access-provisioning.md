---
doc_id: KB-002
title: VPN Access Provisioning Policy
type: policy
owner: Network Infrastructure
audience: it_team, it_specialists
last_updated: 2026-03-18
classification: internal
related_systems: VPN, Identity Provider
---

# VPN Access Provisioning Policy

All remote access to Helios production networks is brokered through the
MeshGuard VPN gateway cluster (`vpn-gw-{1,2,3}.helios.internal`). This
policy defines who can request which VPN profile, the approval chain, and
the maximum certificate lifetime.

## VPN profile catalogue

| Profile | Audience | Cert lifetime | Approver |
| --- | --- | --- | --- |
| `vpn-employee` | Full-time staff | 90 days | Manager + Network on-call |
| `vpn-contractor` | Contractors (W-9 or W-8) | 30 days | Security on-call |
| `vpn-vendor` | External vendor support engineers | 7 days | Network lead + CISO delegate |
| `vpn-emergency` | Major-incident pages, oncall break-glass | 24 hours | CISO or IR commander |

The `vpn-emergency` profile is provisioned only during an active P1 or P2
incident and is automatically revoked when the incident is closed in
PagerDuty. Profiles `vpn-vendor` and `vpn-emergency` are logged to the SOC
SIEM in addition to the standard MeshGuard audit log.

## Provisioning steps (Tier-1 cannot do these)

VPN access provisioning is **specialist-only**. Tier-1 analysts triaging a
ticket that requests VPN access must escalate to `network_infra` with the
following collected:

1. Employee ID and BambooHR confirmation that the employee is in an
   active status.
2. Manager name and Slack handle (required for `vpn-employee`).
3. Requested profile.
4. Business justification (one or two sentences).
5. Whether the requester already has a Yubikey enrolled — if not, the
   Identity & Security specialist must enroll one before the VPN profile
   is issued.

## Common quirks

- The MeshGuard client (`MeshGuard Connect 3.4.x`) requires the system
  clock to be within 30 seconds of NTP. Out-of-skew clients fail with
  `ERR_HANDSHAKE_DRIFT` and not the more obvious certificate error.
- macOS 15.3 introduced a regression where the VPN tunnel drops on Wi-Fi
  network change. The MeshGuard-published fix is to enable the
  **Keep-Alive** option in profile settings; the fix is rolled into
  client `3.4.7` and later.
- Split-tunnel is disabled by policy for `vpn-vendor` and `vpn-emergency`.
  Any vendor reporting access issues to non-Helios sites is expected — do
  not change the tunnel mode.

## Revocation

Revocation is automatic on the cert lifetime above, or immediate via the
MeshGuard admin console under **Profiles → Revoke**. Revoked certs cannot
be re-issued — a new request must be filed.
