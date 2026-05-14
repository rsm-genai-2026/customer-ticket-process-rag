---
doc_id: KB-009
title: Customer Portal — Operations Runbook
type: runbook
owner: Application Engineering
audience: it_team, it_specialists
last_updated: 2026-04-08
classification: internal
related_systems: Customer Portal, Identity Provider
---

# Customer Portal — Operations Runbook

The Customer Portal (`portal.helios.com`) is the primary external surface
for Helios customers. It is a Next.js 14 app, deployed on the Helios
Platform (Kubernetes), and uses AuriLite IDP for sign-in.

## Health endpoints

- `https://portal.helios.com/healthz` — liveness, returns 200 if the pod
  is alive (does not check downstreams).
- `https://portal.helios.com/api/ready` — readiness, returns 200 only if
  the IDP, CRM, and database are reachable.
- Grafana dashboard: `grafana.helios.internal/d/portal-ops`.

## Known issues (read these BEFORE creating a new ticket)

### Issue 1 — SSO redirect loop after IDP minor release

**Symptom**: User signs in, lands on `/auth/callback`, immediately
redirected back to `/login`. Loop continues until the browser cookie
limit is hit.

**Root cause**: AuriLite occasionally rotates the issuer URL on minor
releases. The portal's cached OIDC discovery document goes stale.

**Fix**: Restart the portal deployment to force OIDC discovery refresh.
`kubectl -n portal rollout restart deploy/portal-web`.

**Tier-1 customer guidance**: ask the user to retry in a private
window. Most users will be unaffected once the rollout completes
(~3 minutes).

### Issue 2 — Portal returns "We're sorry" page after a search

**Symptom**: Search query returns the generic 500 page; no other portal
function is affected.

**Root cause**: The portal's search uses Elasticsearch via a sidecar.
Sidecar OOMs every ~14 days because of a leaked compiled query cache.

**Fix**: Pending engineering fix (ticket APP-2391). Until then, the
nightly cron at 04:00 UTC restarts the sidecar; out-of-cycle OOMs need
a manual restart by the on-call engineer.

### Issue 3 — File upload silently fails for files >50MB

**Symptom**: Customer clicks **Upload**, sees a spinner forever, no
error message. Network panel shows `413 Payload Too Large`.

**Root cause**: The portal's nginx ingress has `client_max_body_size 50m`
hard-coded.

**Fix**: There is no fix planned — the 50MB limit is intentional. The
portal needs a frontend error toast; ticket UI-1042 tracks this.

**Tier-1 customer guidance**: ask the user to compress the file or
split it.

## Common false alarms

- **"Portal is down"** at the start of every Sunday — this is the
  MeshGuard maintenance window (KB-007). External customers are not
  affected; only employees on VPN.
- **"Two-factor not working"** between 02:00–05:00 UTC on the first
  Tuesday of the month — this is the AuriLite mandatory release
  window (KB-006).
