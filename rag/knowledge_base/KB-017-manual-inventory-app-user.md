---
doc_id: KB-017
title: Inventory App — User Manual (Internal Edition v3.2)
type: user_manual
owner: Operations Engineering
audience: all_staff, ops_team
last_updated: 2026-03-25
classification: internal
related_systems: Inventory App
---

# Inventory App — User Manual (v3.2)

The Inventory App is the home-grown stock-tracking tool used by the
Helios Operations team. It runs at `inventory.helios.internal` and is
NOT customer-facing.

## Getting started

1. Sign in with your Helios SSO at `inventory.helios.internal`. You must
   be a member of the IDP group `ops-inventory-users` to see anything
   beyond the login screen — membership is requested through your
   manager.
2. The landing page is **My Locations**. Locations you "own" appear
   first; you can pin/unpin any location with the star icon.
3. Use the search box (or `⌘K`) to jump to a SKU, location, or shipment
   number.

## Screen reference

### Stock View

The default screen for a location. Shows on-hand counts grouped by
**bin** (the physical shelf identifier). Bins are colour-coded:

| Colour | Meaning |
| --- | --- |
| Green | On hand ≥ reorder point |
| Yellow | On hand < reorder point but > zero |
| Red | On hand = 0 |
| Grey | SKU disabled at this location |

Hover any cell to see the last-counted timestamp; clicking opens the
**Movement Log**.

### Movement Log

A chronological list of every adjustment to a SKU at a location:
receipts, picks, transfers, cycle counts, and write-offs. Every row is
attributed to a user (SSO identity) and a reason code. Reason codes
that begin with `Z-` are reserved for admin overrides and trigger an
audit notification.

### Cycle Count

Used during the monthly physical count. Two-pane layout — left pane is
the system count, right pane is your physical tally. Discrepancies of
more than 5 units (or any non-zero discrepancy for restricted SKUs
flagged with the lock icon) require a second-person sign-off before
they post.

### Shipments

The Shipments tab shows inbound and outbound moves. Use **Receive** to
post an inbound shipment; the app expects the shipment number from the
carrier label and validates against the purchase order.

## Common settings

- **Default location**: Profile → Preferences → Default Location.
  Defaults to the location matching your office in BambooHR.
- **Notification rules**: Profile → Notifications. You can subscribe
  to "Reorder point crossed" or "Restricted SKU adjusted" alerts at any
  location you own.
- **Dark mode**: keyboard shortcut `Shift+D`.

## Known quirks (v3.2)

- The CSV export of the Movement Log is capped at 10,000 rows. For
  larger pulls, use the dataset query in the Analytics Dashboard
  (KB-010) — `analytics_models.inventory_movements`.
- The mobile (responsive) view does not show the Reason Code dropdown
  on shipments — use a laptop for any adjustment.
- Cycle Count occasionally double-counts a SKU if the same bin appears
  twice in the location's bin map. Workaround: re-run the cycle count
  for that bin only. Engineering fix tracked as `INV-882`.

## Where to get help

- In-app help: `?` icon in the top right opens the help panel.
- Operations help channel: `#ops-inventory-help` in Slack.
- IT support: file a ticket under category `software_bug` with system
  `Inventory App` if the app itself is broken (see KB-010 for the
  related Analytics dashboard runbook).
