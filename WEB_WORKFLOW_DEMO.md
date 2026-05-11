# Web Workflow Demo

This repo includes a local web demo for the customer-ticket workflow. It lets a user submit a new ticket, then either:

- run the workflow all at once, or
- step through one skill at a time like a debugger.

Run it from the repo root:

```bash
uv run python scripts/ticket_web_demo.py --host 127.0.0.1 --port 8767
```

Open:

```text
http://127.0.0.1:8767
```

## Where the Orchestrator Is

The orchestrator is `TicketWorkflowOrchestrator` in `scripts/ticket_web_demo.py`.

It is deliberately small. The skills remain the source of truth for decisions. The orchestrator does four things:

1. Creates an isolated demo run under `/tmp/customer-ticket-process-web-demo/<workflow_run_id>/`.
2. Copies baseline `data/raw/` and `data/dictionaries/` into that run and appends the submitted web ticket there.
3. Runs skill scripts by subprocess with `--json`, `--workflow-run-id`, and `--step-id`.
4. Reads each skill envelope's `next_action` to decide the next skill.

The web demo never writes submitted tickets into `data/raw/`.

## Important Terminology

In an LLM agent such as Codex or Claude, a skill is loaded from `SKILL.md`.
That file is active agent context: it tells the LLM when the skill applies and
what procedure to follow.

The browser demo is different. It is not an LLM agent, and it does not load
`SKILL.md` at runtime. Instead, each skill folder also contains an executable
Python script under `scripts/`, and the web orchestrator runs those scripts
directly.

So the repo has **skill packages**:

```text
skills/receive-ticket/
├── SKILL.md                  # loaded by an LLM agent
└── scripts/receive_ticket.py # executed by the web orchestrator
```

For the web demo, `SKILL.md` is a contract and teaching artifact. The executable
workflow behavior comes from the Python script in the same skill folder.

## Why the Orchestrator Is Code, Not a Skill

The orchestrator is the control plane. It does not decide what the ticket means
or what answer the customer should receive. Its job is deterministic plumbing:

- create an isolated run folder
- call exactly one executable script from a skill folder at a time
- pass `ticket_id`, `workflow_run_id`, `step_id`, `data_dir`, and `out_dir`
- read the skill's JSON envelope
- store the next runnable skill in `metadata.json`
- stop when the workflow needs external input, such as customer feedback

That should be code because it needs to be predictable, testable, and boring.
If orchestration were itself an AI skill, the demo would blur two different
responsibilities: deciding the workflow state versus doing the domain work.
Students should be able to inspect the control flow without wondering whether a
model improvised the route.

The scripts inside the skill folders are the work plane. They classify, check
FAQs, draft responses, escalate, investigate, send, and verify feedback. Every
transition still comes from the script's JSON output through `next_action`; the
orchestrator only validates whether that action maps to a runnable next script.

## Orchestration Walkthrough

1. The browser submits a ticket payload.
2. `normalize_submission()` validates required fields and fills safe defaults.
3. `create_web_ticket()` generates a `WEB-...` ticket id and `wf-web-...`
   workflow id.
4. `prepare_run_data()` copies `data/raw/` and `data/dictionaries/` into
   `/tmp/customer-ticket-process-web-demo/<workflow_run_id>/data/`, appends the
   new ticket there, and creates `metadata.json`.
5. Step mode starts with `next_skill=receive-ticket` and does not run a skill
   until the user presses **Next**.
6. `run_skill()` executes the mapped Python script from the skill folder as a
   subprocess and requires a JSON envelope.
7. The script writes its working CSV artifact and an action-log row, then
   returns `next_action`.
8. `_record_envelopes()` converts `next_action` into `metadata.next_skill` and
   stores a short `last_step` summary for the debugger panel.
9. `workflow_summary()` reads the latest working rows and builds the browser
   model: flow nodes, branch cards, narrative, skill input/output, response text,
   and step log.
10. The workflow pauses at `verify-feedback-close-or-reopen` because customer
    feedback is outside the system. The user must provide that text.

## API Endpoints

| Endpoint | Purpose |
| --- | --- |
| `POST /api/tickets/start` | Create a ticket and stop before the first skill. Used by step mode. |
| `POST /api/step` | Run exactly one skill for an existing `workflow_run_id`. |
| `POST /api/tickets` | Create a ticket and run skills until a customer response has been sent. |
| `POST /api/feedback` | Apply customer feedback and, if needed, auto-run the reopen/escalation path back to another response. |
| `GET /health` | Basic server health check. |

## Step Mode

Step mode persists state in each run's `metadata.json`:

- `next_skill`: the next skill the orchestrator will run
- `last_step_number`: the latest step number
- `last_step`: human-readable summary of what the last skill did
- `terminal`: whether there is no next runnable skill

The page has **Previous** and **Next** controls. Previous and Next first move
through the browser's snapshot history, so students can review what each skill
changed without re-running it. When the viewer is already on the newest snapshot,
**Next** calls `/api/step` and runs exactly one more skill.

The `/api/step` response includes:

- `orchestrator.nextSkill`
- `orchestrator.nextSkillLabel`
- `orchestrator.lastStep.summary`
- `narrative`
- `skillIO`
- `flow.nodes`
- `flow.branches`
- `steps`

This is what drives the debugger-style panel in the UI.

## Example Tickets

The ticket form has an **Example Ticket** dropdown backed by
`scripts/ticket_scenarios.py`. Selecting one example fills the form and resets
the workflow view. The catalog has 20 examples covering:

- direct FAQ resolution
- specialist escalation
- human-expert review with an FAQ-backlog candidate
- rejected FAQ response followed by specialist escalation
- second rejection closing as unresolved
- ambiguous feedback that requests clarification
- a broad FAQ false-positive edge case

## Narrative and Skill I/O

The `narrative` payload is a prepared teaching narrative with inline dynamic
values. Each sentence is a list of text parts and code-style parts, similar to
inline code in RMarkdown. The browser renders code-style parts as small
monospace chips, for example `check-faq-resolution`, the matched `faq_id`, or
the next skill name.

The `skillIO` payload shows the explicit data contract for each completed skill:

- the skill name and label
- the important input fields read by that skill
- the output fields written by that skill
- the working CSV artifact where those outputs were recorded

For example, `check-faq-resolution` shows triage/search inputs and outputs such
as `faq_match_found`, `faq_id`, `match_confidence`, and
`recommended_next_step`.

## Scenario Suite Report Skill

The repo also includes a skill-style report generator:

```bash
uv run python skills/summarize-workflow-suite/scripts/summarize_workflow_suite.py
```

It runs the curated examples through the same orchestrator and writes:

```text
/tmp/customer-ticket-process-suite-report.html
```

Useful quick checks:

```bash
uv run python skills/summarize-workflow-suite/scripts/summarize_workflow_suite.py --limit 5
uv run python skills/summarize-workflow-suite/scripts/summarize_workflow_suite.py --scenario-id human_expert_billing_api_502
```

The report shows expected versus actual branch, terminal feedback action, skills
run, review flags, and candidate FAQ-backlog notes for specialist cases. The
human-expert examples do not auto-update the FAQ knowledge base. They produce a
candidate note so a human can approve the new FAQ before it becomes automated
knowledge.

## Branching

The workflow has one explicit branch point after `check-faq-resolution`:

- FAQ branch: `draft-faq-response -> send-customer-response`
- Specialist branch: `escalate-to-specialist -> investigate-specialist-solution -> draft-specialist-response -> send-customer-response`

The UI shows both branch candidates. One branch is marked selected/completed and the other is marked skipped once the FAQ decision is known.

The current implementation does not run both branches in parallel because the specialist branch is not valid if the FAQ branch resolves the ticket. If students want to demonstrate parallel work in their own projects, the clean pattern is to run independent candidate skills after a shared upstream step, then have the orchestrator choose which result to commit to `data/working/`.

## Likely Stalls and Edge Cases

The workflow is most likely to stall when:

- a skill's required upstream CSV row is missing, such as FAQ check before
  triage
- a skill returns a `next_action` that is not registered in `SKILL_SCRIPTS`
- the workflow reaches `verify-feedback-close-or-reopen` and no customer
  feedback text is available
- feedback is ambiguous, which routes to `request_clarification`
- an FAQ match is broad but not truly appropriate, producing a customer reply
  that rejects the answer
- the specialist path produces `needs_human_review=true`, usually because the
  confidence score is low or the handoff is missing information
- a ticket is rejected twice; the guard closes it as
  `close_unresolved_vendor_followup` to avoid an infinite loop
- the data schema changes and a skill can no longer find the columns it expects

## Skill Contract

The orchestrator expects every skill script to:

- accept `--ticket-id`
- accept `--data-dir`
- accept `--out-dir`
- accept `--workflow-run-id`
- accept `--step-id`
- accept `--json`
- emit a JSON envelope with `skill_name`, `status`, and `next_action`
- write rows to working CSVs and/or `ticket_action_log.csv`

That contract is why the web demo can swap between full-run and debugger mode without changing the underlying skill code.
