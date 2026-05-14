---
doc_id: KB-001
title: Tier-1 Password Reset Standard Operating Procedure
type: sop
owner: Identity & Security
audience: it_team
last_updated: 2026-04-02
classification: internal
related_systems: Identity Provider, Customer Portal
---

# Tier-1 Password Reset SOP

This is the verification-first procedure every Tier-1 support analyst must
follow before resetting a password for any employee or customer-facing
account. It supersedes the prior 2024 SOP (which allowed email-based
verification).

## Identity verification (required before any reset)

A Tier-1 analyst must complete **one** of the following checks before
triggering a reset in the Identity Provider admin console. Email-based
self-attestation is no longer acceptable.

1. **Slack manager DM** — Confirm the request via Slack DM with the
   employee's direct manager as listed in BambooHR. The manager must reply
   with the literal string `APPROVED: <ticket_id>` within 30 minutes.
2. **Video callback** — Place a Zoom callback to the number on file in
   BambooHR and visually confirm the requester matches their badge photo.
3. **Yubikey challenge** — For accounts already enrolled in hardware MFA,
   trigger a Yubikey challenge through the Identity Provider self-service
   portal at `idp.helios.internal/reset`.

For customer (non-employee) accounts, only option 3 applies. Tier-1 must
not reset customer credentials by phone — escalate to the Identity & Security
specialist group via the escalation matrix (KB-014).

## Reset procedure

1. Open the IDP admin console at `idp.helios.internal/admin`.
2. Search by employee ID (preferred) or username.
3. Choose **Force credential rotation**, NOT **Manual password set** —
   the latter bypasses the audit log and triggers a SOC alert.
4. The user receives a one-time reset link valid for 60 minutes.
5. Record the ticket id in the **Reason** field. Free-text reasons are
   pulled into the weekly SOC review.

## Time targets

- Verification: 15 minutes.
- Reset execution: 5 minutes.
- Total target: under 25 minutes from ticket assignment.

Tickets exceeding 25 minutes auto-flag for support-lead review (Maya Chen,
APAC-Day, or her delegate).

## Common failure modes

- Manager DM goes unanswered within 30 minutes → escalate to the manager's
  manager; do not reset on partial approval.
- BambooHR shows the employee as on leave → verify with the People team
  before resetting; off-boarded users must not be reset under any
  circumstances (route to security_request).
- User claims they "already tried" the self-service portal but no event
  appears in the IDP audit log → suspect phishing or impersonation; flag
  the ticket and notify the security on-call.
