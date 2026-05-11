# IT Ticketing Skills Implementation Plan

This plan is for Claude Code to implement skills that automate the IT ticketing workflow described in
`customer-ticket-process-genai.pdf` using the generated synthetic data in `data/`.

## Current Data Status

The data is good to go. It has already been regenerated after the workflow-detail fields were added.

All files under `data/raw/`, `data/processed/`, and `data/dictionaries/` must be treated as generated
artifacts from `scripts/generate_human_ticket_data.py`. Do not manually edit those CSV files. If the data
needs to change, update the generator, update the validator/tests, and regenerate with:

```bash
uv run python scripts/generate_human_ticket_data.py --n-tickets 250 --seed 49502 --out-dir data
```

The manually authored/maintained files are the generator, validator, tests, documentation, examples, and
skill source files. The generated CSVs are outputs, not source of truth.

Current validation command:

```bash
uv run python scripts/validate_human_ticket_data.py --data-dir data
```

Expected result:

```text
Validation summary: 62 passed, 0 failed.
```

Do not regenerate data unless one of these is true:

- The workflow PDF changes.
- The required schema in `scripts/validate_human_ticket_data.py` changes.
- A skill needs a missing field that cannot be derived reliably from the existing CSVs.

Use only standard Python libraries, `numpy`, and `polars`. Do not add dataframe, plotting, or analytics libraries beyond this stack.

## Workflow Source

Before implementing skills, read `customer-ticket-process-genai.pdf` and `data/README.md`.

The skills implement the AI-assisted ticketing workflow. The original process roles are still visible in the
data model, but each step below is performed by a deterministic, script-backed AI skill:

1. User submits ticket.
2. IT team receives ticket.
3. IT team classifies ticket.
4. IT team assigns priority.
5. IT team checks FAQ.
6. If FAQ works, IT team drafts and sends a customer response.
7. If FAQ is insufficient, IT team escalates to an IT specialist.
8. IT specialist investigates and creates a solution.
9. IT team drafts and sends specialist-based response to customer.
10. Customer accepts or rejects.
11. If accepted, IT team closes the ticket and records feedback.
12. If rejected, IT team verifies rejection and either reopens/escalates or closes unresolved.

## Skill Architecture

Claude Code must use its skill-creation workflow/tool when implementing each skill. In prompts, refer to
this explicitly as:

```text
Use the skill-creator workflow/tool to create or update this Claude Code skill.
```

The skill-creation workflow is responsible for creating a concise, triggerable `SKILL.md` with clear
frontmatter and instructions. The script-backed workflow below is still required because these skills must
perform repeatable data operations, not just provide prose instructions.

Each skill should follow the ticketing skill pattern used in `skills/check-faq-resolution/`:

```text
skills/<skill-name>/
├── SKILL.md
├── README.qmd
├── install.sh
└── scripts/
    └── <skill_name>.py

tests/skills/
└── test_<skill_name>.py
```

Implementation rules:

- Start each skill implementation by invoking Claude Code's skill-creator workflow/tool for the target
  skill folder.
- Scripts must be deterministic and Polars-only for tabular operations.
- Scripts must run from the repo root with `uv run python ...`.
- Every public function in each script needs unit tests.
- Every skill must have tests under `tests/skills/` before it is considered complete.
- Every skill must include at least one CLI smoke test, either as a unit test invoking `main(...)` or a
  subprocess test running the script with `uv run python`.
- After each skill is implemented, run the targeted skill tests before moving to the next skill.
- Skill instructions must tell Claude what data to inspect, what script to run, and what to report back.
- Skills should write output records to `data/working/` by default, not overwrite `data/raw/` or `data/processed/`.
- Skills may read `data/processed/` as historical examples and ground truth, but should treat `data/working/` as the live run log for new workflow execution.

Create this shared working-output structure:

```text
data/working/
├── triage_decisions.csv
├── faq_decisions.csv
├── escalation_decisions.csv
├── specialist_solutions.csv
├── customer_response_drafts.csv
├── feedback_decisions.csv
└── ticket_action_log.csv
```

Shared conventions:

- Every script should accept `--ticket-id`.
- Every script should accept `--data-dir data`.
- Every script should accept `--out-dir data/working`.
- Every output row should include `ticket_id`, `created_at`, `skill_name`, `inputs_used`, `decision_summary`, and `confidence_score` where relevant.
- Use ISO timestamps.
- If a required field is missing, print a clear error and exit non-zero.
- If the ticket does not exist, print a clear error and exit non-zero.
- Do not silently invent customer facts that are not in the data.

## Data Contracts by Skill

These contracts are hard requirements. A skill may read additional historical examples from `data/processed/`,
but it must not ignore the listed source tables and must write the listed output when it makes a decision.

| Skill | Required source data | Required upstream working data | Output written |
| --- | --- | --- | --- |
| `receive-ticket` | `data/raw/submitted_tickets.csv`, `data/raw/customers.csv` | none | `data/working/ticket_action_log.csv` |
| `classify-prioritize-ticket` | `data/raw/submitted_tickets.csv`, `data/raw/customers.csv`, `data/dictionaries/categories.csv`, `data/dictionaries/priority_rules.csv` | none | `data/working/triage_decisions.csv`, action log |
| `check-faq-resolution` | `data/raw/submitted_tickets.csv`, `data/raw/faq_knowledge_base.csv` | `data/working/triage_decisions.csv` preferred; fallback to `data/processed/ticket_triage.csv` only for demo/historical tickets | `data/working/faq_decisions.csv`, action log |
| `draft-faq-response` | `data/raw/submitted_tickets.csv`, `data/raw/faq_knowledge_base.csv` | `data/working/faq_decisions.csv` with `faq_match_found=true` | `data/working/customer_response_drafts.csv`, action log |
| `escalate-to-specialist` | `data/raw/submitted_tickets.csv`, `data/raw/it_specialists.csv` | `data/working/triage_decisions.csv`, plus `data/working/faq_decisions.csv` or `data/working/feedback_decisions.csv` depending on escalation reason | `data/working/escalation_decisions.csv`, action log |
| `investigate-specialist-solution` | `data/raw/submitted_tickets.csv`, `data/raw/it_specialists.csv`, `data/dictionaries/systems.csv` | `data/working/escalation_decisions.csv` | `data/working/specialist_solutions.csv`, action log |
| `draft-specialist-response` | `data/raw/submitted_tickets.csv` | `data/working/escalation_decisions.csv`, `data/working/specialist_solutions.csv` | `data/working/customer_response_drafts.csv`, action log |
| `verify-feedback-close-or-reopen` | `data/raw/submitted_tickets.csv` | `data/working/customer_response_drafts.csv`; optional prior `data/working/feedback_decisions.csv` for reopened state | `data/working/feedback_decisions.csv`, action log |
| `audit-ticket-process` | all `data/raw/`, `data/processed/`, and `data/dictionaries/` tables | all available `data/working/` tables | audit report, action log |

If a required upstream working table is missing because the workflow step has not happened yet, the skill
must stop and explain the valid previous step. Example: `draft-specialist-response` must not invent a
specialist solution if `data/working/specialist_solutions.csv` has no row for the ticket.

## Testing Requirements

Every skill needs tests at three levels:

1. Unit tests for each public function.
2. CLI behavior tests for happy path and required-input failure path.
3. Workflow-state tests showing that the skill refuses to skip required upstream steps.

Minimum commands after each skill:

```bash
uv run pytest tests/skills/test_<skill_name>.py
uv run python scripts/validate_human_ticket_data.py --data-dir data
```

Minimum commands before declaring the full skills implementation complete:

```bash
uv run pytest
uv run python scripts/validate_human_ticket_data.py --data-dir data
rg -n "<disallowed dataframe library name>" .
```

## Skill 1: Receive and Summarize Ticket

Directory:

```text
skills/receive-ticket/
```

Purpose:

Use when the IT team receives a new ticket and needs a concise intake summary before triage.

Input tables:

- `data/raw/submitted_tickets.csv`
- `data/raw/customers.csv`

Important fields:

- `subject`
- `description`
- `affected_system`
- `customer_reported_urgency`
- `business_impact_text`
- `error_or_symptom_detail`
- `steps_already_tried`
- `expected_outcome`
- `availability_window`
- `attachment_flag`
- `attachment_description`
- `account_tier`
- `sla_plan`

Script:

```text
skills/receive-ticket/scripts/receive_ticket.py
```

Core functions:

- `load_ticket_context(data_dir: Path, ticket_id: str) -> dict`
- `build_intake_summary(context: dict) -> dict`
- `append_action_log(out_dir: Path, record: dict) -> None`

CLI example:

```bash
uv run python skills/receive-ticket/scripts/receive_ticket.py --ticket-id TKT-00042 --data-dir data --out-dir data/working
```

Output:

- Print a human-readable intake summary.
- Append a row to `data/working/ticket_action_log.csv`.

Acceptance tests:

- Valid ticket returns customer and ticket fields.
- Missing ticket exits non-zero.
- Summary includes symptom, impact, urgency, and steps already tried.
- Action log is created if missing and appended if present.

## Skill 2: Classify and Prioritize Ticket

Directory:

```text
skills/classify-prioritize-ticket/
```

Purpose:

Use when the IT team needs to assign category, priority, SLA targets, and likely specialist group.

Input tables:

- `data/raw/submitted_tickets.csv`
- `data/raw/customers.csv`
- `data/dictionaries/categories.csv`
- `data/dictionaries/priority_rules.csv`
- `data/processed/ticket_triage.csv` as historical labeled examples

Important fields:

- Ticket text and operational detail from `submitted_tickets.csv`.
- `account_tier`, `sla_plan`, `active_users` from customers.
- Category descriptions and default specialist groups.
- Priority target response/resolution hours.

Script:

```text
skills/classify-prioritize-ticket/scripts/classify_prioritize_ticket.py
```

Core functions:

- `load_triage_inputs(data_dir: Path, ticket_id: str) -> dict`
- `score_categories(context: dict, categories: pl.DataFrame) -> pl.DataFrame`
- `assign_priority(context: dict, priority_rules: pl.DataFrame) -> dict`
- `build_triage_decision(context: dict) -> dict`
- `write_triage_decision(out_dir: Path, decision: dict) -> None`

Minimum deterministic logic:

- Use keyword and system matching to propose a category.
- Use customer tier, reported urgency, affected system, and business impact to assign priority.
- Use `categories.default_specialist_group` as the recommended specialist group.
- Use `priority_rules.target_first_response_hours` and `priority_rules.target_resolution_hours` to calculate SLA target timestamps.

Output file:

```text
data/working/triage_decisions.csv
```

Recommended columns:

- `ticket_id`
- `created_at`
- `assigned_category`
- `assigned_priority`
- `recommended_specialist_group`
- `target_first_response_at`
- `target_resolution_at`
- `classification_evidence`
- `priority_reason`
- `confidence_score`
- `inputs_used`
- `decision_summary`

Acceptance tests:

- Known login/password tickets classify to an identity/access category.
- Higher urgency and enterprise tier should not receive lower priority than an equivalent standard-tier ticket.
- SLA timestamps are after `submitted_at`.
- Output row has no blank decision fields.

## Skill 3: Check FAQ Resolution

Directory:

```text
skills/check-faq-resolution/
```

Purpose:

Use after triage to decide whether an FAQ entry can resolve the ticket without specialist escalation.

Input tables:

- `data/raw/submitted_tickets.csv`
- `data/raw/faq_knowledge_base.csv`
- `data/working/triage_decisions.csv`, falling back to `data/processed/ticket_triage.csv` for existing synthetic examples
- `data/processed/faq_checks.csv` as historical examples

Script:

```text
skills/check-faq-resolution/scripts/check_faq_resolution.py
```

Core functions:

- `load_faq_context(data_dir: Path, out_dir: Path, ticket_id: str) -> dict`
- `build_search_terms(context: dict) -> list[str]`
- `rank_faq_candidates(context: dict, faqs: pl.DataFrame) -> pl.DataFrame`
- `decide_faq_applicability(context: dict, ranked: pl.DataFrame) -> dict`
- `write_faq_decision(out_dir: Path, decision: dict) -> None`

Minimum deterministic logic:

- Filter active FAQ entries.
- Prefer same category and same affected system.
- Score candidates using overlap between ticket subject/description/symptom detail and FAQ symptoms/issue pattern.
- Mark `faq_match_found` true only when score and required-customer-info availability are sufficient.
- If match is weak or required information is missing, recommend escalation.

Output file:

```text
data/working/faq_decisions.csv
```

Recommended columns:

- `ticket_id`
- `created_at`
- `faq_match_found`
- `faq_id`
- `match_confidence`
- `search_terms`
- `candidate_faq_ids`
- `required_customer_info_available`
- `faq_application_reason`
- `recommended_next_step`
- `inputs_used`
- `decision_summary`

Acceptance tests:

- FAQ candidates are active FAQ entries only.
- A same-category/same-system FAQ ranks above unrelated entries.
- No-match case recommends escalation.
- Matched case includes `faq_id`, reason, and customer action.

## Skill 4: Draft FAQ-Based Customer Response

Directory:

```text
skills/draft-faq-response/
```

Purpose:

Use when FAQ resolution is sufficient and the IT team needs a customer-safe message.

Input tables:

- `data/raw/submitted_tickets.csv`
- `data/raw/faq_knowledge_base.csv`
- `data/working/faq_decisions.csv`
- `data/processed/customer_messages.csv` as historical examples

Script:

```text
skills/draft-faq-response/scripts/draft_faq_response.py
```

Core functions:

- `load_response_context(data_dir: Path, out_dir: Path, ticket_id: str) -> dict`
- `draft_faq_response(context: dict) -> dict`
- `quality_check_response(draft: dict) -> dict`
- `write_customer_response(out_dir: Path, response: dict) -> None`

Minimum deterministic logic:

- Confirm the latest FAQ decision has `faq_match_found=true`.
- Use FAQ `solution_steps` and `required_customer_info`.
- Include acknowledgement of the reported impact.
- Ask the customer to confirm resolution.
- Do not expose internal-only notes.

Output file:

```text
data/working/customer_response_drafts.csv
```

Recommended columns:

- `message_id`
- `ticket_id`
- `created_at`
- `message_source=faq`
- `draft_text`
- `sent_text`
- `customer_action_required`
- `included_context`
- `follow_up_request`
- `quality_check_notes`
- `inputs_used`

Acceptance tests:

- Refuses to draft when no FAQ match exists.
- Draft includes solution steps and follow-up request.
- Draft excludes specialist/internal notes.
- Quality check fails if customer action is missing.

## Skill 5: Escalate to Specialist

Directory:

```text
skills/escalate-to-specialist/
```

Purpose:

Use when FAQ resolution is absent, weak, incomplete, or rejected and the IT team must route the ticket to a specialist.

Input tables:

- `data/raw/submitted_tickets.csv`
- `data/raw/it_specialists.csv`
- `data/working/triage_decisions.csv`
- `data/working/faq_decisions.csv`
- `data/working/feedback_decisions.csv` for reopened tickets
- `data/processed/specialist_escalations.csv` as historical examples

Script:

```text
skills/escalate-to-specialist/scripts/escalate_to_specialist.py
```

Core functions:

- `load_escalation_context(data_dir: Path, out_dir: Path, ticket_id: str) -> dict`
- `select_specialist(context: dict, specialists: pl.DataFrame) -> dict`
- `build_handoff(context: dict, specialist: dict) -> dict`
- `write_escalation_decision(out_dir: Path, decision: dict) -> None`

Minimum deterministic logic:

- Use `recommended_specialist_group` from triage when available.
- Select a specialist whose `specialist_group` matches.
- Prefer specialists whose `systems_supported` contains the affected system.
- Include a handoff summary, evidence included, and a specific question.
- For rejected tickets, include the first response and customer rejection reason.

Output file:

```text
data/working/escalation_decisions.csv
```

Recommended columns:

- `ticket_id`
- `created_at`
- `specialist_id`
- `requested_specialist_group`
- `escalation_reason`
- `handoff_summary`
- `specific_question_for_specialist`
- `customer_evidence_included`
- `missing_information_flag`
- `inputs_used`
- `decision_summary`

Acceptance tests:

- Specialist group matches triage recommendation when possible.
- System-supported specialist is preferred over same-group non-system specialist.
- Handoff summary includes symptom, impact, and steps already tried.
- Reopen escalation includes customer rejection reason.

## Skill 6: Investigate Specialist Solution

Directory:

```text
skills/investigate-specialist-solution/
```

Purpose:

Use when acting as the IT specialist to produce a root cause, diagnostic notes, and a customer-safe solution summary.

Input tables:

- `data/raw/submitted_tickets.csv`
- `data/raw/it_specialists.csv`
- `data/working/escalation_decisions.csv`
- `data/processed/specialist_investigations.csv` as historical examples
- `data/dictionaries/systems.csv`

Script:

```text
skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py
```

Core functions:

- `load_investigation_context(data_dir: Path, out_dir: Path, ticket_id: str) -> dict`
- `build_diagnostic_plan(context: dict) -> list[str]`
- `infer_root_cause(context: dict) -> dict`
- `build_solution_summary(context: dict, root_cause: dict) -> dict`
- `write_specialist_solution(out_dir: Path, solution: dict) -> None`

Minimum deterministic logic:

- Use category/system templates for plausible root causes.
- Use evidence from escalation handoff.
- If `missing_information_flag=true`, note what information is missing and reduce confidence.
- Produce a customer-safe `solution_summary` and internal `specialist_notes`.

Output file:

```text
data/working/specialist_solutions.csv
```

Recommended columns:

- `ticket_id`
- `created_at`
- `specialist_id`
- `root_cause`
- `diagnostic_steps`
- `evidence_reviewed`
- `solution_summary`
- `specialist_notes`
- `customer_action_required`
- `requires_follow_up_flag`
- `confidence_score`
- `inputs_used`
- `decision_summary`

Acceptance tests:

- Missing escalation exits non-zero.
- Solution includes root cause, diagnostic steps, and customer action.
- Missing-information cases have lower confidence than complete-information cases.
- Customer-safe summary does not include internal-only language such as raw logs, credentials, or unsupported guarantees.

## Skill 7: Draft Specialist-Based Customer Response

Directory:

```text
skills/draft-specialist-response/
```

Purpose:

Use when the IT team needs to translate a specialist solution into a customer-facing response.

Input tables:

- `data/raw/submitted_tickets.csv`
- `data/working/specialist_solutions.csv`
- `data/working/escalation_decisions.csv`
- `data/processed/customer_messages.csv` as historical examples

Script:

```text
skills/draft-specialist-response/scripts/draft_specialist_response.py
```

Core functions:

- `load_specialist_response_context(data_dir: Path, out_dir: Path, ticket_id: str) -> dict`
- `draft_specialist_response(context: dict) -> dict`
- `quality_check_response(draft: dict) -> dict`
- `write_customer_response(out_dir: Path, response: dict) -> None`

Minimum deterministic logic:

- Acknowledge the ticket and customer impact.
- Summarize the specialist's recommended fix in plain language.
- Include the customer action required.
- Ask for confirmation.
- If this is post-rejection, acknowledge the earlier attempt briefly.

Output file:

```text
data/working/customer_response_drafts.csv
```

Recommended columns:

- Same as FAQ response, with `message_source=specialist_solution`.

Acceptance tests:

- Refuses to draft if no specialist solution exists.
- Draft includes specialist solution summary and customer action.
- Post-rejection draft references the revised/corrected nature of the response.
- Quality check fails if follow-up request is missing.

## Skill 8: Verify Customer Feedback and Close or Reopen

Directory:

```text
skills/verify-feedback-close-or-reopen/
```

Purpose:

Use when customer feedback arrives after a response and the IT team needs to decide whether to close, reopen, or close unresolved.

Input tables:

- `data/raw/submitted_tickets.csv`
- `data/working/customer_response_drafts.csv`
- `data/processed/resolution_feedback.csv` as historical examples
- optionally a user-provided feedback string via `--feedback-text`

Script:

```text
skills/verify-feedback-close-or-reopen/scripts/verify_feedback.py
```

Core functions:

- `load_feedback_context(data_dir: Path, out_dir: Path, ticket_id: str, feedback_text: str) -> dict`
- `classify_feedback(feedback_text: str) -> dict`
- `decide_next_action(context: dict, classification: dict) -> dict`
- `write_feedback_decision(out_dir: Path, decision: dict) -> None`

Minimum deterministic logic:

- Positive feedback means `next_action=close_ticket`.
- Negative feedback on first attempt means `next_action=reopen_and_escalate`.
- Negative feedback after reopened specialist response can become `close_unresolved_vendor_followup` if no further internal action is available.
- Verification notes must explain the decision.

Output file:

```text
data/working/feedback_decisions.csv
```

Recommended columns:

- `ticket_id`
- `created_at`
- `resolution_accepted`
- `customer_feedback_text`
- `rejection_reason`
- `verified_rejection`
- `reopened_flag`
- `verified_by_it_member_id`
- `verification_notes`
- `next_action`
- `closure_reason`
- `inputs_used`
- `decision_summary`

Acceptance tests:

- Positive feedback closes the ticket.
- Negative first-attempt feedback reopens and escalates.
- Negative post-reopen feedback can close unresolved with clear rationale.
- Blank feedback exits non-zero.

## Skill 9: Ticket Process Auditor

Directory:

```text
skills/audit-ticket-process/
```

Purpose:

Use to inspect a ticket and explain where it is in the workflow, what has happened, what data supports that, and what the next valid action is.

Input tables:

- All `data/raw/`, `data/processed/`, and `data/working/` workflow tables.

Script:

```text
skills/audit-ticket-process/scripts/audit_ticket_process.py
```

Core functions:

- `load_ticket_history(data_dir: Path, out_dir: Path, ticket_id: str) -> dict`
- `infer_current_state(history: dict) -> dict`
- `list_valid_next_actions(state: dict) -> list[str]`
- `build_audit_report(history: dict, state: dict) -> str`

Minimum deterministic logic:

- Prefer `data/working/` for live skill-created rows.
- Fall back to `data/processed/` for synthetic historical examples.
- Explain missing fields or invalid state transitions.
- Never skip ahead in the workflow.

Output:

- Print a concise audit report.
- Append audit event to `data/working/ticket_action_log.csv`.

Acceptance tests:

- Fresh submitted ticket next action is classify/prioritize.
- Triaged ticket with no FAQ decision next action is FAQ check.
- FAQ match with no message next action is draft FAQ response.
- No FAQ match with no escalation next action is escalate.
- Specialist solution with no response next action is draft specialist response.
- Response with no feedback next action is wait for/verify feedback.

## Recommended Build Order

Implement in this order:

1. Shared utilities under `skills/ticketing_common/` or duplicated minimal helpers if simpler.
2. `receive-ticket`.
3. `classify-prioritize-ticket`.
4. `check-faq-resolution`.
5. `draft-faq-response`.
6. `escalate-to-specialist`.
7. `investigate-specialist-solution`.
8. `draft-specialist-response`.
9. `verify-feedback-close-or-reopen`.
10. `audit-ticket-process`.

Shared utility candidates:

- `read_csv(data_dir, rel_path)`
- `require_ticket(data_dir, ticket_id)`
- `append_csv_row(path, row)`
- `latest_working_row(out_dir, table_name, ticket_id)`
- `pipe_join(values)`
- `now_iso()`

If shared utilities are added, put them in:

```text
skills/ticketing_common/ticketing_common.py
```

and test them in:

```text
tests/skills/test_ticketing_common.py
```

## End-to-End Demo Path

Claude Code should support this scripted demo:

```bash
uv run python skills/receive-ticket/scripts/receive_ticket.py --ticket-id TKT-00042
uv run python skills/classify-prioritize-ticket/scripts/classify_prioritize_ticket.py --ticket-id TKT-00042
uv run python skills/check-faq-resolution/scripts/check_faq_resolution.py --ticket-id TKT-00042
```

Then branch:

- If FAQ match:

```bash
uv run python skills/draft-faq-response/scripts/draft_faq_response.py --ticket-id TKT-00042
uv run python skills/verify-feedback-close-or-reopen/scripts/verify_feedback.py --ticket-id TKT-00042 --feedback-text "That fixed it, thanks."
```

- If no FAQ match:

```bash
uv run python skills/escalate-to-specialist/scripts/escalate_to_specialist.py --ticket-id TKT-00042
uv run python skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py --ticket-id TKT-00042
uv run python skills/draft-specialist-response/scripts/draft_specialist_response.py --ticket-id TKT-00042
uv run python skills/verify-feedback-close-or-reopen/scripts/verify_feedback.py --ticket-id TKT-00042 --feedback-text "The issue is resolved."
```

At any point:

```bash
uv run python skills/audit-ticket-process/scripts/audit_ticket_process.py --ticket-id TKT-00042
```

## Definition of Done

The skills implementation is complete when:

- Each skill has `SKILL.md`, `README.qmd`, `install.sh`, script, and tests.
- All scripts run from the repo root with `uv run python`.
- No script overwrites source data.
- `data/working/` is created automatically when needed.
- Every workflow step can be executed through one of the skills.
- `audit-ticket-process` can identify the next valid action.
- `uv run pytest` passes.
- `uv run python scripts/validate_human_ticket_data.py --data-dir data` still passes.
- Repository search for disallowed dataframe-library references returns no matches.
