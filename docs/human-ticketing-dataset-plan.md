# Plan: Generate Example Datasets for the Human Ticketing Process

## Goal

Create realistic example datasets for the **current human customer support ticket resolution process**. These datasets should represent how work happens before GenAI automation:

1. A user submits a ticket.
2. A human IT team member receives it.
3. The IT team member classifies the issue and assigns priority.
4. The IT team member checks the FAQ/knowledge base.
5. If a FAQ solution exists, the IT team member drafts and sends the response.
6. If no FAQ solution exists, the ticket is forwarded to an IT specialist.
7. The specialist investigates and creates a solution.
8. The IT team member relays the specialist solution to the user.
9. The user accepts or rejects the resolution.
10. If accepted, the ticket is closed and feedback is recorded.
11. If rejected, the IT team verifies the rejection and routes the ticket back for review.

The output should support Milestone 02 data preparation and later skill development. Do **not** generate the GenAI-automated process yet. This plan is only for the human baseline.

## Authoritative Workflow Source

Before implementing anything, Claude Code must read:

```text
customer-ticket-process.pdf
```

Use the **Current process** / **human workflow** in that PDF as the authoritative source for the process logic. The PDF also includes an automation/GenAI version; do **not** use the automation version for this dataset-generation task except as background context. The generated data should show the baseline human process with these swimlanes:

- `User`
- `IT team`
- `IT specialist`

The required human workflow from the PDF is:

1. User submits a ticket.
2. IT team receives the ticket.
3. IT team classifies the ticket.
4. IT team assigns priority.
5. IT team checks whether there is a FAQ resolution.
6. If a FAQ resolution exists, IT team finds the resolution, drafts the solution message, sends the message, and waits for the user response.
7. If no FAQ resolution exists, IT team sends the ticket to an IT specialist.
8. IT specialist analyzes the issue and creates a solution.
9. IT team drafts/sends the solution message to the user.
10. User accepts or rejects the resolution.
11. If accepted, IT team closes the ticket and records feedback.
12. If rejected, IT team verifies the rejection and routes the ticket back for further review/edit.

Implementation should preserve this distinction in the data model: actions performed by a human IT team member should have an `it_member_id`, actions performed by an IT specialist should have a `specialist_id`, and user actions should be represented as customer/user events.

## Deliverables

Create this structure:

```text
milestone02/
  README.md
  data/
    README.md
    raw/
      customers.csv
      it_team_members.csv
      it_specialists.csv
      faq_knowledge_base.csv
      submitted_tickets.csv
    processed/
      ticket_triage.csv
      faq_checks.csv
      specialist_escalations.csv
      specialist_investigations.csv
      customer_messages.csv
      resolution_feedback.csv
      ticket_lifecycle_events.csv
      ticket_summary.csv
    dictionaries/
      categories.csv
      priority_rules.csv
      systems.csv
      status_codes.csv
  scripts/
    generate_human_ticket_data.py
    validate_human_ticket_data.py
  tests/
    test_human_ticket_data.py
```

Use CSV for all generated datasets. Keep generation deterministic using a fixed random seed.

## Implementation Steps

### 0. Inspect and Summarize the Workflow PDF

Before writing the generator, add a short workflow summary to `README.md` based on `customer-ticket-process.pdf`.

Implementation notes:

- Extract or manually inspect the PDF text.
- Document that the generated data represents the **human current process**.
- Explicitly list the process steps and actor responsible for each step.
- Confirm in the README that the automation/GenAI process in the PDF is intentionally out of scope for this dataset generator.

### 1. Build the Data Generator

Create `data/generate_human_ticket_data.py`.

The script should:

- Use only standard Python libraries plus `numpy` and `polars`.
- Accept command-line arguments:
  - `--n-tickets`, default `250`
  - `--seed`, default `49502`
  - `--out-dir`, default `data`
- Create all required folders if missing.
- Write all CSV files.
- Print a short summary of row counts and key rates.
- Preserve the operational detail needed to perform the workflow: customer symptom details, steps already tried,
  SLA targets, FAQ search terms, FAQ candidates, specialist handoff summaries, specialist diagnostic steps,
  customer action requirements, and rejection verification notes.

Example command:

```bash
python data/generate_human_ticket_data.py --n-tickets 250 --seed 49502
```

### 2. Define Reference Dictionaries

Generate these first because other tables depend on them.

#### `dictionaries/categories.csv`

Fields:

- `category_id`
- `category`
- `description`
- `default_specialist_group`
- `faq_coverage_rate`
- `typical_resolution_hours_min`
- `typical_resolution_hours_max`

Suggested categories:

- `login_access`
- `password_reset`
- `billing_account`
- `software_bug`
- `hardware_issue`
- `network_connectivity`
- `email_calendar`
- `data_reporting`
- `security_request`
- `other`

Make some categories more likely to have FAQ coverage, especially `password_reset`, `login_access`, and `email_calendar`.

#### `dictionaries/priority_rules.csv`

Fields:

- `priority`
- `priority_rank`
- `description`
- `target_first_response_hours`
- `target_resolution_hours`
- `example_conditions`

Priorities:

- `low`
- `medium`
- `high`
- `urgent`

#### `dictionaries/systems.csv`

Fields:

- `system_id`
- `system_name`
- `business_owner`
- `common_issue_types`

Suggested systems:

- `Customer Portal`
- `Billing System`
- `CRM`
- `Email`
- `VPN`
- `Analytics Dashboard`
- `Inventory App`
- `Identity Provider`

#### `dictionaries/status_codes.csv`

Fields:

- `status`
- `description`
- `terminal_flag`

Statuses:

- `submitted`
- `triaged`
- `faq_checked`
- `response_sent`
- `escalated`
- `specialist_review`
- `specialist_solution_created`
- `customer_replied`
- `reopened`
- `closed`

### 3. Generate Master Data

#### `raw/customers.csv`

Fields:

- `customer_id`
- `customer_name`
- `account_tier`: `standard`, `premium`, `enterprise`
- `industry`
- `region`
- `sla_plan`: `basic`, `business`, `critical`
- `active_users`
- `relationship_start_date`

Generation notes:

- Use realistic company names.
- Enterprise/critical customers should be more likely to receive higher priority when impact is high.
- `active_users` should vary by tier.

#### `raw/it_team_members.csv`

Human IT team members who do intake, classification, FAQ lookup, and customer communication.

Fields:

- `it_member_id`
- `name`
- `role`: e.g. `support_analyst`, `senior_support_analyst`, `support_lead`
- `shift`
- `max_daily_ticket_capacity`
- `quality_score`

Generate 6-10 team members.

#### `raw/it_specialists.csv`

Specialists who investigate escalated tickets.

Fields:

- `specialist_id`
- `name`
- `specialist_group`
- `systems_supported`
- `seniority`
- `max_daily_escalation_capacity`

Generate 8-12 specialists. Each should support one or more systems/categories.

#### `raw/faq_knowledge_base.csv`

Approved FAQ or known-resolution entries.

Fields:

- `faq_id`
- `category`
- `system_name`
- `issue_pattern`
- `symptoms`
- `solution_steps`
- `required_customer_info`
- `last_updated`
- `owner`
- `active_flag`

Generation notes:

- Create 30-50 FAQ entries.
- Entries should cover common, simple issues.
- Some categories should have few or no FAQ entries.
- `solution_steps` should be realistic short procedural text.

### 4. Generate Submitted Tickets

#### `raw/submitted_tickets.csv`

This table represents the user-submitted ticket before human processing.

Fields:

- `ticket_id`
- `submitted_at`
- `customer_id`
- `submitted_by_name`
- `submitted_by_email`
- `channel`: `portal`, `email`, `phone`
- `subject`
- `description`
- `affected_system`
- `customer_reported_urgency`: `low`, `medium`, `high`, `critical`
- `business_impact_text`
- `attachment_flag`

Generation notes:

- Generate tickets over a realistic 60-90 day period.
- Use category/system patterns to create realistic subjects and descriptions.
- Include messy human text: typos, vague descriptions, missing details, repeated frustration, urgency language.
- Some tickets should clearly match FAQs; others should require specialist review.
- Keep proprietary/commercial data out; all data must be synthetic.

### 5. Generate Human Triage

#### `processed/ticket_triage.csv`

This table captures the human IT team member’s classification and priority assignment.

Fields:

- `ticket_id`
- `triaged_at`
- `it_member_id`
- `assigned_category`
- `assigned_priority`
- `priority_reason`
- `classification_confidence`: numeric 0-1
- `needs_specialist_review_flag`

Generation logic:

- `triaged_at` should occur after `submitted_at`.
- Higher customer tier, critical urgency, affected users, and security/network categories should increase priority.
- Add realistic human inconsistency:
  - Some borderline tickets are over-prioritized.
  - Some vague tickets have lower classification confidence.
  - A small number of tickets are misclassified relative to their generated issue pattern.

### 6. Generate FAQ Checks

#### `processed/faq_checks.csv`

This table captures whether the IT team member found an existing FAQ solution.

Fields:

- `ticket_id`
- `faq_checked_at`
- `it_member_id`
- `faq_match_found`
- `faq_id`
- `match_confidence`
- `faq_match_notes`

Generation logic:

- Every ticket should have a FAQ check after triage.
- FAQ match probability depends on category FAQ coverage.
- If no FAQ match, `faq_id` should be blank and `match_confidence` should be low.
- If FAQ match is found, `faq_id` must exist in `faq_knowledge_base.csv`.

### 7. Generate Specialist Escalations

#### `processed/specialist_escalations.csv`

Only for tickets with no FAQ match or tickets that are too complex for FAQ resolution.

Fields:

- `ticket_id`
- `escalated_at`
- `it_member_id`
- `specialist_id`
- `escalation_reason`
- `information_provided`
- `missing_information_flag`

Generation logic:

- Escalated tickets must have `faq_match_found = false`, or a low-confidence FAQ match.
- Specialist should be selected based on category/system support.
- Some escalations should have missing information, causing longer investigation time.

### 8. Generate Specialist Investigations

#### `processed/specialist_investigations.csv`

Only for escalated tickets.

Fields:

- `ticket_id`
- `specialist_id`
- `investigation_started_at`
- `solution_created_at`
- `root_cause`
- `solution_summary`
- `specialist_notes`
- `requires_follow_up_flag`

Generation logic:

- Investigation starts after escalation.
- Resolution time depends on priority, category, specialist seniority, and missing information.
- `solution_summary` should be suitable for the IT team member to relay to the customer.

### 9. Generate Customer Messages

#### `processed/customer_messages.csv`

All human-drafted messages sent to customers.

Fields:

- `message_id`
- `ticket_id`
- `message_created_at`
- `it_member_id`
- `message_source`: `faq`, `specialist_solution`
- `draft_text`
- `sent_text`
- `sent_at`

Generation logic:

- FAQ tickets should use the FAQ solution as the source.
- Escalated tickets should use specialist solution as the source.
- `draft_text` and `sent_text` can be similar, but `sent_text` should look customer-facing.
- Include some variation in clarity and tone based on `it_member_id` quality score.

### 10. Generate Resolution Feedback

#### `processed/resolution_feedback.csv`

Customer acceptance/rejection of the sent resolution.

Fields:

- `ticket_id`
- `customer_reply_at`
- `resolution_accepted`
- `customer_feedback_text`
- `rejection_reason`
- `verified_rejection`
- `reopened_flag`
- `closed_at`

Generation logic:

- Most FAQ and specialist solutions should be accepted.
- Lower match confidence, missing information, and vague tickets should increase rejection probability.
- Rejected tickets should be verified by IT.
- Some rejected tickets should reopen and loop back for additional review.
- For this initial dataset, model only one possible reopen cycle. Do not create infinite loops.

### 11. Generate Lifecycle Events

#### `processed/ticket_lifecycle_events.csv`

Long-form event log for process mining and timeline analysis.

Fields:

- `event_id`
- `ticket_id`
- `event_time`
- `event_type`
- `actor_type`: `customer`, `it_team`, `it_specialist`
- `actor_id`
- `event_notes`

Required event types:

- `ticket_submitted`
- `ticket_triaged`
- `faq_checked`
- `faq_response_drafted`
- `faq_response_sent`
- `ticket_escalated`
- `specialist_investigation_started`
- `specialist_solution_created`
- `specialist_response_drafted`
- `specialist_response_sent`
- `customer_resolution_accepted`
- `customer_resolution_rejected`
- `rejection_verified`
- `ticket_reopened`
- `ticket_closed`

Generation logic:

- Use all previous tables to construct a consistent event log.
- Events must be chronologically ordered within each ticket.
- Include both FAQ-resolved and specialist-resolved paths.

### 12. Generate Ticket Summary

#### `processed/ticket_summary.csv`

One row per ticket for analysis and dashboards.

Fields:

- `ticket_id`
- `customer_id`
- `submitted_at`
- `closed_at`
- `final_status`
- `assigned_category`
- `assigned_priority`
- `affected_system`
- `faq_match_found`
- `escalated_flag`
- `specialist_id`
- `resolution_accepted`
- `reopened_flag`
- `time_to_triage_hours`
- `time_to_first_response_hours`
- `time_to_resolution_hours`
- `sla_met_first_response`
- `sla_met_resolution`

Generation logic:

- Derive from the generated process tables.
- This should be the main dataset for quick analytics.

## Data Quality Constraints

Implement validation checks in `data/validate_human_ticket_data.py`.

Required checks:

- All expected files exist.
- `ticket_id` is unique in `submitted_tickets.csv`.
- Every ticket has one triage row.
- Every ticket has one FAQ check row.
- Every ticket has at least one customer message.
- FAQ-resolved tickets have a valid `faq_id`.
- Escalated tickets have a specialist escalation row.
- Escalated tickets have a specialist investigation row.
- All foreign keys are valid:
  - `customer_id`
  - `it_member_id`
  - `specialist_id`
  - `faq_id`
- Timestamps are in the correct order:
  - submit < triage < FAQ check
  - FAQ path: FAQ check < message sent < customer reply < close/reopen
  - specialist path: FAQ check < escalation < investigation < solution < message sent < customer reply < close/reopen
- `ticket_summary.csv` has exactly one row per ticket.
- Event log has at least 4 events per ticket.
- No terminal ticket has missing `closed_at`.
- Rates are plausible:
  - FAQ match rate between 35% and 70%.
  - Escalation rate between 25% and 60%.
  - Resolution acceptance rate between 70% and 95%.
  - Reopen rate between 5% and 25%.

The validation script should exit with a nonzero status if checks fail.

Example command:

```bash
python data/validate_human_ticket_data.py --data-dir data
```

## README Requirements

Create or update `README.md` with:

- A brief description of the human ticket process.
- How to regenerate the data.
- How to validate the data.
- A table mapping process steps to datasets.
- A note that all data is synthetic and safe for coursework.

Create `data/README.md` with:

- One section per dataset.
- Schema description.
- Primary key.
- Foreign keys.
- How the dataset supports the human process.
- How it will later support skills.

## Testing Requirements

Create `tests/test_human_ticket_data.py`.

Use `pytest`. Tests should:

- Run the generator into a temporary directory.
- Run validation checks on the generated output.
- Assert expected files exist.
- Assert row counts are consistent.
- Assert key rate ranges are plausible.
- Assert timestamps are ordered for a sample or all tickets.

Example command:

```bash
pytest tests/test_human_ticket_data.py
```

## Suggested Synthetic Content Patterns

Use realistic but synthetic examples.

Ticket subject examples:

- `Cannot log into customer portal`
- `VPN disconnects every few minutes`
- `Invoice total looks wrong`
- `Analytics dashboard not loading`
- `Need access for new employee`
- `Email calendar sync issue`
- `CRM export missing records`
- `Password reset link expired`
- `Suspicious login notification`
- `Inventory app shows stale data`

Business impact examples:

- `One user blocked from logging in`
- `Entire finance team cannot access billing reports`
- `Customer demo delayed because dashboard is down`
- `New hire cannot access required systems`
- `Monthly close delayed by export issue`

FAQ solution examples:

- Password reset steps.
- Browser cache and cookie reset.
- VPN client restart and profile refresh.
- Calendar sync reauthorization.
- Known dashboard refresh delay.

Specialist solution examples:

- Backend permission correction.
- Database sync job rerun.
- Firewall rule update.
- Bug workaround with ticket for engineering.
- Manual account mapping correction.

## Acceptance Criteria

Claude Code implementation is complete when:

1. `python data/generate_human_ticket_data.py --n-tickets 250 --seed 49502` runs successfully.
2. All files listed in the deliverables are created.
3. `python data/validate_human_ticket_data.py --data-dir data` passes.
4. `pytest tests/test_human_ticket_data.py` passes.
5. `README.md` and `data/README.md` explain the datasets clearly.
6. The generated data visibly represents the **human** workflow, including human IT triage, FAQ lookup, specialist escalation, customer messaging, acceptance/rejection, and closure.
7. `README.md` explicitly states that `customer-ticket-process.pdf` was used as the authoritative source for the human workflow.
8. The data model clearly separates `User`, `IT team`, and `IT specialist` actions.

## Do Not Do Yet

- Do not generate GenAI assistant decisions.
- Do not create skills in this task.
- Do not use proprietary or real customer data.
- Do not connect to real ticketing systems.
- Do not require API keys.
