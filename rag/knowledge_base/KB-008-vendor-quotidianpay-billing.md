---
doc_id: KB-008
title: QuotidianPay Billing Platform — Vendor Contract Summary
type: vendor_contract
owner: Finance Operations
audience: it_specialists, finance_team
last_updated: 2026-01-22
classification: internal
related_systems: Billing System
---

# QuotidianPay — Vendor Contract Summary

QuotidianPay hosts the Helios Billing System and acts as the merchant of
record for customer invoices in the EU and APAC.

## Contract

- **Vendor**: QuotidianPay Holdings, Ltd. (Ireland).
- **Agreement**: MSA-QP-2022-031, signed 2022-04-11.
- **Term**: rolling 24-month, current term ends 2026-11-30.
- **Annual fee**: 0.6% of processed GMV plus $48,000 platform fee.
- **Transaction fee**: 2.4% + €0.18 per EU card transaction, 2.9% + $0.30
  per US card transaction.

## Support

- **Tier**: Enterprise 24/5 (M–F UTC).
- **Sev-1 first response**: 30 minutes.
- **Support email**: enterprise-support@quotidianpay.example.
- **Engineering escalation**: paid; $400 per escalation.
- **TAM**: Lena Kowalski.

## SLA

- **API availability**: 99.9% monthly across `/v1/invoice` and
  `/v1/charge`.
- **Payout latency**: T+2 business days (EU), T+3 business days (US).
- **Refund latency**: T+5 business days for credit card refunds.

## Operational caveats

- QuotidianPay does **not** generate invoices for amounts below €1.00 /
  $1.00; below-threshold charges accumulate and bill at the end of the
  customer's billing cycle. Customers occasionally raise "missing
  invoice" tickets — see KB-013 RCA for the export bug that masked
  this in 2025-11.
- The CSV export from the QuotidianPay console truncates `customer_id`
  to 32 characters as of release 8.4. Helios customer IDs are 36 chars
  (UUID-style) — collisions are rare but happen at the 1-in-10K rate
  in the export.
- Tax computation is delegated to a sub-processor (StratoTax). When
  StratoTax is degraded, QuotidianPay falls back to a hard-coded local
  rate that is often wrong for non-US jurisdictions.

## Compliance

- PCI DSS Level 1 (attestation 2025-09-18 in Vendor Vault).
- SOC 2 Type II — annual, current report dated 2025-07-30.
- Card data tokenised at QuotidianPay; Helios never stores PANs.
