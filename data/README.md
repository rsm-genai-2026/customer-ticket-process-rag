# Data

This folder contains the synthetic dataset for the **AI-assisted customer-support ticket resolution process**. Three process roles are preserved for traceability: **User**, **IT team**, and **IT specialist**. The repo's skills automate the ticketing steps while keeping those role labels in the data.

```text
data/
├── raw/                       # source-of-truth tables (people, FAQ, raw tickets)
├── processed/                 # synthetic historical tables for each ticketing step
└── dictionaries/              # reference enumerations
```

Regenerate everything (run from the repo root):

```bash
uv run python scripts/generate_human_ticket_data.py --n-tickets 250 --seed 49502 --out-dir data
```

Validate everything:

```bash
uv run python scripts/validate_human_ticket_data.py --data-dir data
```

> All data is synthetic. No real customers, employees, or systems.

---

## raw/customers.csv

The customer accounts that submit tickets.

| Column | Type | Notes |
| --- | --- | --- |
| `customer_id` | string | **Primary key** (e.g. `CUST-007`). |
| `customer_name` | string | Synthetic company name. |
| `account_tier` | enum | `standard`, `premium`, `enterprise`. |
| `industry` | string | Industry segment. |
| `region` | string | Customer region (e.g. `EU-West`). |
| `sla_plan` | enum | `basic`, `business`, `critical`. Tied to `account_tier`. |
| `active_users` | int | Number of named users on the account. |
| `relationship_start_date` | date | When the customer first signed up. |

- **PK**: `customer_id`
- **Used by**: `submitted_tickets.customer_id`, `ticket_summary.customer_id`.
- **Skill support**: customer profile lookup, segmentation, SLA-aware prioritization.

## raw/it_team_members.csv

Human IT team members. They handle intake, triage, FAQ lookup, customer messaging, and rejection verification.

| Column | Type | Notes |
| --- | --- | --- |
| `it_member_id` | string | **Primary key** (e.g. `IT-003`). |
| `name` | string | |
| `role` | enum | `support_analyst`, `senior_support_analyst`, `support_lead`. |
| `shift` | string | Working shift / region. |
| `max_daily_ticket_capacity` | int | Soft capacity. |
| `quality_score` | float | 0–1; influences message tone in generation. |

- **PK**: `it_member_id`
- **Used by**: `ticket_triage.it_member_id`, `faq_checks.it_member_id`, `specialist_escalations.it_member_id`, `customer_messages.it_member_id`.
- **Skill support**: assignment heuristics, workload balancing, quality coaching skills.

## raw/it_specialists.csv

IT specialists who investigate escalated tickets.

| Column | Type | Notes |
| --- | --- | --- |
| `specialist_id` | string | **Primary key** (e.g. `SP-002`). |
| `name` | string | |
| `specialist_group` | string | e.g. `identity_security`, `network_infra`. |
| `systems_supported` | string | Pipe-delimited system names. |
| `seniority` | enum | `junior`, `mid`, `senior`, `principal`. |
| `max_daily_escalation_capacity` | int | Soft capacity. |

- **PK**: `specialist_id`
- **Used by**: `specialist_escalations.specialist_id`, `specialist_investigations.specialist_id`, `ticket_summary.specialist_id`.
- **Skill support**: routing skill, root-cause-by-group analytics, escalation queue health.

## raw/faq_knowledge_base.csv

Approved FAQ entries that the IT team consults during triage.

| Column | Type | Notes |
| --- | --- | --- |
| `faq_id` | string | **Primary key** (e.g. `FAQ-018`). |
| `category` | string | Maps to `dictionaries/categories.csv`. |
| `system_name` | string | Maps to `dictionaries/systems.csv`. |
| `issue_pattern` | string | Short slug describing the symptom class. |
| `symptoms` | text | Plain-English symptoms. |
| `solution_steps` | text | Procedural fix used to draft the FAQ-based message. |
| `required_customer_info` | text | Information the IT member should ask for. |
| `last_updated` | date | |
| `owner` | string | FAQ author. |
| `active_flag` | bool | Inactive entries are not used for matches. |

- **PK**: `faq_id`
- **Used by**: `faq_checks.faq_id`.
- **Skill support**: FAQ retrieval / classification skills, FAQ coverage analysis.

## raw/submitted_tickets.csv

The user-submitted ticket exactly as it arrives — before any IT processing.

| Column | Type | Notes |
| --- | --- | --- |
| `ticket_id` | string | **Primary key** (e.g. `TKT-00042`). |
| `submitted_at` | datetime (ISO) | Submission timestamp. |
| `customer_id` | string | FK → `customers.customer_id`. |
| `submitted_by_name` | string | End-user name. |
| `submitted_by_email` | string | End-user email. |
| `channel` | enum | `portal`, `email`, `phone`. |
| `subject` | string | Short subject line. |
| `description` | text | Free-form, intentionally messy. |
| `affected_system` | string | FK → `systems.system_name`. |
| `customer_reported_urgency` | enum | `low`, `medium`, `high`, `critical`. |
| `business_impact_text` | text | Customer's stated impact. |
| `attachment_flag` | bool | |
| `error_or_symptom_detail` | text | Operational detail the worker can use during triage. |
| `steps_already_tried` | text | What the customer already attempted. |
| `expected_outcome` | text | What “fixed” should look like from the customer perspective. |
| `availability_window` | string | When the customer is available for follow-up. |
| `attachment_description` | text | Synthetic attachment label when `attachment_flag` is true; empty otherwise. |

- **PK**: `ticket_id`
- **Skill support**: classification skill input; first prompt for any LLM-driven triage.

## processed/ticket_triage.csv

The IT team's classification and priority decision. Exactly one row per ticket.

| Column | Type | Notes |
| --- | --- | --- |
| `ticket_id` | string | FK → `submitted_tickets.ticket_id`. |
| `triaged_at` | datetime (ISO) | After `submitted_at`. |
| `it_member_id` | string | FK → `it_team_members.it_member_id`. |
| `assigned_category` | string | FK → `categories.category`. May differ from the true issue (~5% misclassified). |
| `assigned_priority` | enum | `low`, `medium`, `high`, `urgent`. |
| `priority_reason` | string | Short explanation. |
| `classification_confidence` | float | 0–1. |
| `needs_specialist_review_flag` | bool | Triage hint that this should escalate even if FAQ matches. |
| `intake_summary` | text | Human-readable summary of the issue and impact. |
| `classification_evidence` | text | Evidence used for the assigned category. |
| `recommended_specialist_group` | string | Specialist group implied by the assigned category. |
| `target_first_response_at` | datetime (ISO) | SLA target based on assigned priority. |
| `target_resolution_at` | datetime (ISO) | SLA target based on assigned priority. |

- **PK**: `ticket_id`
- **Skill support**: ground-truth labels for classification skills; priority calibration.

## processed/faq_checks.csv

The result of the IT team's FAQ lookup. Exactly one row per ticket.

| Column | Type | Notes |
| --- | --- | --- |
| `ticket_id` | string | FK → `submitted_tickets.ticket_id`. |
| `faq_checked_at` | datetime (ISO) | After `triaged_at`. |
| `it_member_id` | string | FK → `it_team_members.it_member_id`. |
| `faq_match_found` | bool | Whether an FAQ entry was deemed applicable. |
| `faq_id` | string | FK → `faq_knowledge_base.faq_id` when match found; empty otherwise. |
| `match_confidence` | float | 0–1. |
| `faq_match_notes` | text | Explanation of the matching decision. |
| `search_terms` | text | Terms the IT team used to search the FAQ. |
| `candidate_faq_ids` | string | Pipe-delimited FAQ candidates considered. |
| `required_customer_info_available` | bool | Whether enough customer detail was available to apply the FAQ. |
| `faq_application_reason` | text | Why the FAQ path was or was not sufficient. |

- **PK**: `ticket_id`
- **Skill support**: retrieval evaluation, comparing historical FAQ matching with AI-assisted matching.

## processed/specialist_escalations.csv

Tickets handed to an IT specialist (initial escalation **and** post-rejection re-escalation).

| Column | Type | Notes |
| --- | --- | --- |
| `ticket_id` | string | FK → `submitted_tickets.ticket_id`. May appear up to twice (initial + post-reopen). |
| `escalated_at` | datetime (ISO) | After `faq_checked_at`. |
| `it_member_id` | string | FK → `it_team_members.it_member_id`. |
| `specialist_id` | string | FK → `it_specialists.specialist_id`. |
| `escalation_reason` | string | e.g. `no FAQ match found`, `re-escalation after rejection`. |
| `information_provided` | text | What the IT team passed along. |
| `missing_information_flag` | bool | If the specialist had to ask for more detail. |
| `requested_specialist_group` | string | Specialist group requested by the IT team. |
| `handoff_summary` | text | Actionable issue summary for the specialist. |
| `specific_question_for_specialist` | text | The question the specialist is expected to answer. |
| `customer_evidence_included` | string | Pipe-delimited evidence artifacts included in the handoff. |

- **Skill support**: routing accuracy, queue analytics.

## processed/specialist_investigations.csv

Specialist investigations and solutions. One row per investigation; reopened tickets get a second row.

| Column | Type | Notes |
| --- | --- | --- |
| `ticket_id` | string | FK → `submitted_tickets.ticket_id`. |
| `specialist_id` | string | FK → `it_specialists.specialist_id`. |
| `investigation_started_at` | datetime (ISO) | After the matching `escalated_at`. |
| `solution_created_at` | datetime (ISO) | After `investigation_started_at`. |
| `root_cause` | text | Plain-English root cause. |
| `solution_summary` | text | Suitable for the IT team to relay. |
| `specialist_notes` | text | Internal notes. |
| `requires_follow_up_flag` | bool | Engineering follow-up flag. |
| `diagnostic_steps` | text | What the specialist checked. |
| `evidence_reviewed` | string | Pipe-delimited evidence reviewed. |
| `customer_action_required` | text | What the customer or IT team must do with the solution. |
| `confidence_score` | float | Specialist confidence in the solution. |

- **Skill support**: solution-summary generation, post-mortem clustering.

## processed/customer_messages.csv

All workflow-drafted messages sent to the customer (FAQ-based or specialist-based). One row per message; reopened tickets get two messages.

| Column | Type | Notes |
| --- | --- | --- |
| `message_id` | string | **Primary key** (e.g. `MSG-000123`). |
| `ticket_id` | string | FK → `submitted_tickets.ticket_id`. |
| `message_created_at` | datetime (ISO) | Drafted timestamp. |
| `it_member_id` | string | FK → `it_team_members.it_member_id`. |
| `message_source` | enum | `faq` or `specialist_solution`. |
| `draft_text` | text | Internal draft. |
| `sent_text` | text | Customer-facing version. |
| `sent_at` | datetime (ISO) | After `message_created_at` and after the upstream FAQ/solution event. |
| `customer_action_required` | text | Action the customer is asked to take. |
| `included_context` | string | Pipe-delimited content included in the message. |
| `follow_up_request` | text | What the customer should report back. |
| `quality_check_notes` | text | Internal note on message completeness. |

- **Skill support**: message-quality evaluation; LLM-rewriting skill ground truth.

## processed/resolution_feedback.csv

Customer reply + IT team's verification/closure. One row per customer reply event (so reopened tickets have two rows; the second carries `closed_at`).

| Column | Type | Notes |
| --- | --- | --- |
| `ticket_id` | string | FK → `submitted_tickets.ticket_id`. |
| `customer_reply_at` | datetime (ISO) | After the corresponding `sent_at`. |
| `resolution_accepted` | bool | Customer's accept/reject decision. |
| `customer_feedback_text` | text | Customer's words. |
| `rejection_reason` | string | Empty when accepted. |
| `verified_rejection` | bool | Whether the IT team confirmed the rejection. |
| `reopened_flag` | bool | True on the first row when the ticket reopens; the second row closes the ticket. |
| `closed_at` | datetime (ISO) | Populated only on the row that closes the ticket. |
| `verified_by_it_member_id` | string | IT member who verified the rejection; empty when no verification is needed. |
| `verification_notes` | text | Human-readable verification/closure rationale. |
| `next_action` | enum | `close_ticket`, `reopen_and_escalate`, `close_unresolved`, or related terminal action. |
| `closure_reason` | string | Closure state or pending reopened-review state. |

- **Skill support**: acceptance prediction; reopen-risk scoring; CSAT analysis.

## processed/ticket_lifecycle_events.csv

Long-form event log — one row per discrete state transition. Suitable for process mining and timeline visualizations.

| Column | Type | Notes |
| --- | --- | --- |
| `event_id` | string | **Primary key** (e.g. `EVT-0001234`). |
| `ticket_id` | string | FK → `submitted_tickets.ticket_id`. |
| `event_time` | datetime (ISO) | Chronologically ordered within ticket. |
| `event_type` | enum | `ticket_submitted`, `ticket_triaged`, `faq_checked`, `faq_response_drafted`, `faq_response_sent`, `ticket_escalated`, `specialist_investigation_started`, `specialist_solution_created`, `specialist_response_drafted`, `specialist_response_sent`, `customer_resolution_accepted`, `customer_resolution_rejected`, `rejection_verified`, `ticket_reopened`, `ticket_closed`. |
| `actor_type` | enum | `customer`, `it_team`, `it_specialist`. |
| `actor_id` | string | The acting party's id. |
| `event_notes` | text | Optional context. |

- **Skill support**: process-mining skills, cycle-time analytics, anomaly detection in ticket flow.

## processed/ticket_summary.csv

One row per ticket — the analytics-friendly dataset.

| Column | Type | Notes |
| --- | --- | --- |
| `ticket_id` | string | **Primary key** (also FK → `submitted_tickets.ticket_id`). |
| `customer_id` | string | FK → `customers.customer_id`. |
| `submitted_at` | datetime (ISO) | |
| `closed_at` | datetime (ISO) | Final close time. |
| `final_status` | string | Currently always `closed` after one possible reopen. |
| `assigned_category` | string | |
| `true_category` | string | Synthetic ground-truth category for evaluation. |
| `assigned_priority` | enum | |
| `affected_system` | string | |
| `faq_match_found` | bool | |
| `escalated_flag` | bool | True if the ticket ever escalated (initial or post-reopen). |
| `specialist_id` | string | Last specialist who handled the ticket; empty if FAQ-only. |
| `resolution_accepted` | bool | Final outcome (after any reopen). |
| `reopened_flag` | bool | True if the ticket was reopened once. |
| `first_resolution_source` | enum | `faq` or `specialist_solution`. |
| `time_to_triage_hours` | float | |
| `time_to_first_response_hours` | float | Submit → first message sent. |
| `time_to_resolution_hours` | float | Submit → final close. |
| `sla_met_first_response` | bool | Compared with `priority_rules.target_first_response_hours`. |
| `sla_met_resolution` | bool | Compared with `priority_rules.target_resolution_hours`. |

- **Skill support**: dashboards, model training, evaluation harness for any GenAI ticket workflow.

## dictionaries/categories.csv

Issue categories and their default specialist group, FAQ coverage rate, and typical resolution windows.

## dictionaries/priority_rules.csv

Priority rank, target first-response hours, target resolution hours, and example conditions used for triage reasoning.

## dictionaries/systems.csv

Business systems referenced by tickets and FAQs (`Customer Portal`, `VPN`, `Identity Provider`, …) along with the business owner and common issue types.

## dictionaries/status_codes.csv

Allowable status values that appear in the lifecycle (`submitted`, `triaged`, `faq_checked`, `escalated`, `closed`, …) and which are terminal.
