---
doc_id: KB-004
title: BYOD Mobile Device Policy
type: policy
owner: Workplace IT
audience: all_staff
last_updated: 2025-12-04
classification: internal
related_systems: Email, Identity Provider
---

# BYOD Mobile Device Policy

Helios allows employees to use a personal phone or tablet for work email
and authenticator apps, subject to enrolment in the Workplace IT MDM
profile (`helios-byod-mdm.mobileconfig`).

## What enrolment does

- Installs the Helios root CA so email and intranet sites validate.
- Installs the Helios certificate for the AuriLite IDP push-MFA app.
- Enforces a minimum 6-digit passcode and 10-minute auto-lock.
- Enables remote wipe of the work profile **only** (not the device).
  Personal photos, contacts, and apps outside the work profile are not
  affected.

## What enrolment does NOT do

- It does not give Helios access to personal SMS, call logs, location,
  microphone, or camera. The MDM agent reports only: device model, OS
  version, jailbreak status, and the inventory of Helios-managed apps.
- It does not permit Helios to remotely lock the entire device.

## Eligibility

- Employees in a `support_analyst` or higher role can enroll.
- Contractors can enroll only with manager + Security on-call approval.
- Devices below the minimum supported OS (iOS 17, Android 13) are not
  eligible. The Workplace IT team will not troubleshoot mail issues on
  unsupported OS versions.

## Common support issues

- **Push MFA stops working after OS upgrade.** Re-enroll the device in
  MDM; the AuriLite push token is rebuilt on first launch after
  re-enrolment.
- **Mail no longer syncs after password change.** The native iOS Mail app
  caches the old token; remove and re-add the account.
- **"Cannot verify server identity" on first launch.** The Helios root CA
  was not installed — re-run the MDM enrolment.

## Off-boarding

When an employee leaves Helios, the MDM profile is removed automatically
within 30 minutes of their IDP account deactivation. This wipes only the
work profile.
