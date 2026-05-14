---
doc_id: KB-011
title: MFA Troubleshooting — Tier-1 Runbook (Internal-Only)
type: runbook
owner: Identity & Security
audience: it_team
last_updated: 2026-04-19
classification: internal
related_systems: Identity Provider
---

# MFA Troubleshooting — Tier-1 Runbook

This runbook covers MFA failures that are **not** in the public FAQ
(FAQ-002 covers basic "MFA code not received"). Use this when the
public FAQ doesn't resolve the issue.

## Decision tree

1. Does the user have a Yubikey enrolled?
   - **Yes** → try Yubikey before any SMS/push fallback (see §1).
   - **No** → check push-MFA enrollment status (see §2).

2. Did the user recently change phones or SIM?
   - **Yes** → SMS path is broken; treat as a fresh MFA enrollment (see §3).
   - **No** → continue.

3. Is the user reporting "push never arrives"?
   - **Yes** → see §4 (push-MFA delivery).
   - **No** → see §5 (code wrong / TOTP drift).

## §1 — Yubikey path

If the Yubikey blinks but the IDP shows no event:
- The Yubikey's NFC interface may be in `OTP-only` mode. Ask the user to
  switch to `FIDO2` via the YubiKey Manager app.
- USB-C-to-Lightning adaptors do not pass FIDO2 — the user must use the
  Yubikey's native connector.

## §2 — Push-MFA enrolment

If the user has the AuriLite app installed but no push prompt arrives:
- Verify the device shows up in `idp.helios.internal/admin/users/{id}`
  under **Registered Factors**.
- If the device is listed but stale (last-seen > 30 days), de-register
  the factor and re-enrol from scratch.
- AuriLite limits one push factor per device — if the user reinstalled
  the app, the old factor must be removed first.

## §3 — Phone or SIM change

- Confirm via the manager Slack DM (per KB-001).
- Update the phone number in the IDP under the user's profile **before**
  triggering an MFA reseed. The reseed sends an SMS to the *new* number.
- Do not rely on the user's response by email — phone-change tickets are
  a common phishing vector.

## §4 — Push delivery problems

Push delivery problems are usually one of:
- The device has push notifications disabled at the OS level (especially
  iOS Focus modes).
- The AuriLite app was force-quit and is not in the background; iOS
  silences push until the app is reopened once.
- The corporate Wi-Fi blocks APNs/FCM on guest SSIDs. Ask the user to
  test on cellular.

## §5 — TOTP drift / wrong code

- Confirm the device clock is on NTP — TOTP drifts by ~1 minute per
  month on unsynced devices.
- Codes display for 30 seconds but only the *current* window is valid;
  the previous 30s code is rejected after a +/-1 grace window.
- If multiple users in the same office report drift on the same day,
  suspect a captive-portal NTP block — escalate to `network_infra`.

## When to escalate

Escalate to `identity_security` when:
- The Yubikey path fails despite §1 checks.
- More than one user reports the same MFA symptom in the same hour
  (potential AuriLite incident — check status.aurilite.example).
- The user's account shows recent failed logins from unfamiliar IPs —
  this is a security event, not just MFA.
