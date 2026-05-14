# Simulated Proprietary Knowledge Base

18 internal documents simulating the kind of proprietary content a Helios
IT support analyst (or an AI coworker) would need to do their job. The
documents are intentionally diverse in *type* (SOPs, policies, vendor
contracts, runbooks, RCAs, escalation matrix, change calendar, employee
handbook excerpt, internal user manual, vendor manual excerpt) and
cross-reference each other so that a real retrieval task has more than
one plausibly relevant hit per query.

All content is synthetic. Vendors, employees, contract numbers, and
incident IDs are fabricated.

## Why a separate KB

The customer-ticket workflow already uses a *public* FAQ knowledge base
at `data/raw/faq_knowledge_base.csv`. The KB here is different: it
represents the **internal** knowledge an LLM could not have seen during
training — specific SOPs, vendor SLAs, runbooks of known issues,
post-incident reviews, and the like. RAG over this corpus is what lets
the AI coworker say "this looks like the 2025-09 MeshGuard regression
(see KB-012)" instead of generic troubleshooting advice.

## Document index

| ID | Type | Title | Owner |
| --- | --- | --- | --- |
| KB-001 | SOP | Tier-1 Password Reset SOP | Identity & Security |
| KB-002 | Policy | VPN Access Provisioning Policy | Network Infrastructure |
| KB-003 | Policy | Data Classification Policy | Security & Compliance |
| KB-004 | Policy | BYOD Mobile Device Policy | Workplace IT |
| KB-005 | Policy | After-Hours Support Coverage Policy | Support Operations |
| KB-006 | Vendor contract | AuriLite IDP — Vendor Contract Summary | Identity & Security |
| KB-007 | Vendor contract | MeshGuard VPN — Vendor Contract Summary | Network Infrastructure |
| KB-008 | Vendor contract | QuotidianPay Billing — Vendor Contract Summary | Finance Operations |
| KB-009 | Runbook | Customer Portal — Operations Runbook | Application Engineering |
| KB-010 | Runbook | Analytics Dashboard — Operations Runbook | Data Platform |
| KB-011 | Runbook | MFA Troubleshooting — Tier-1 Runbook | Identity & Security |
| KB-012 | RCA | VPN Outage 2025-09-14 | Network Infrastructure |
| KB-013 | RCA | Billing Export Bug 2025-11-22 | Finance Operations |
| KB-014 | Reference | Specialist Escalation Matrix & On-Call Rota | Support Operations |
| KB-015 | Change calendar | Change Calendar — 2026 Q2 | Change Management Board |
| KB-016 | Handbook | Employee Handbook — IT Support Section | People Operations |
| KB-017 | User manual | Inventory App — User Manual (v3.2) | Operations Engineering |
| KB-018 | Vendor manual | MeshGuard Connect — Admin Manual Excerpt | Network Infrastructure |

## Coverage by ticket category

These are the rough mappings — a real retrieval run will hit more than
one document, which is the point.

| Category | Most-relevant docs |
| --- | --- |
| `login_access` | KB-001, KB-006, KB-011, KB-014 |
| `password_reset` | KB-001, KB-006, KB-011 |
| `billing_account` | KB-008, KB-013, KB-014 |
| `software_bug` | KB-009, KB-010, KB-017 |
| `hardware_issue` | KB-004, KB-014 |
| `network_connectivity` | KB-002, KB-007, KB-012, KB-015, KB-018 |
| `email_calendar` | KB-004, KB-011 |
| `data_reporting` | KB-010, KB-013, KB-017 |
| `security_request` | KB-001, KB-003, KB-005, KB-014 |
| `other` | KB-014, KB-016 |

## Frontmatter convention

Each document has YAML frontmatter:

```yaml
---
doc_id: KB-001                 # stable id used for citation
title: ...                     # human title
type: sop|policy|runbook|...   # document type
owner: <team>                  # who maintains it
audience: <comma list>         # who reads it
last_updated: YYYY-MM-DD
classification: internal       # or internal_use_only / restricted
related_systems: ...           # which systems the doc references
---
```

This is the metadata to load into the in-memory index along with the
chunks. See `../retrieval.py` (to be written) for the index layout.

## Suggested chunking

Most documents are short enough (≤ 80 lines) to be a single chunk. If
you want finer-grained retrieval, split on `##` headings — each
section is self-contained and roughly the right size for one chunk.
The vendor manual excerpt (KB-018) is the strongest candidate for
section-level chunking because each subsection answers a distinct kind
of question (error codes vs. log fields vs. deployment modes).
