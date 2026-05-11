# MBA Demo LLM Guide

This file is written for students to give to Claude Code, Codex, or another LLM
when they want help understanding the repo. It assumes the reader has limited
technical background but wants a real explanation of how the demo works.

## What This Repo Demonstrates

The repo turns a customer-support ticket process into a step-by-step AI workflow.
A user submits a ticket, the workflow decides whether an FAQ can resolve it, and
if not, it escalates to a specialist. The demo shows the workflow in a browser
and lets the user step through it like a debugger.

The main business idea is:

```text
Complex process -> small skills -> explicit handoffs -> auditable workflow
```

## Start Here

For a student-friendly overview, read:

1. `slides/mba-ticket-workflow-demo.qmd`
2. `WEB_WORKFLOW_DEMO.md`
3. `README.md`

To run the browser demo:

```bash
uv run python scripts/ticket_web_demo.py --host 127.0.0.1 --port 8767
```

Then open:

```text
http://127.0.0.1:8767
```

## Key Files

| File or folder | Why it matters |
| --- | --- |
| `scripts/ticket_web_demo.py` | The local web app and deterministic orchestrator. |
| `scripts/ticket_scenarios.py` | The 20 example tickets used by the dropdown and scenario suite. |
| `skills/<skill>/SKILL.md` | Human/LLM-facing instructions for each skill. |
| `skills/<skill>/scripts/*.py` | The executable version of each skill used by the web demo. |
| `skills/summarize-workflow-suite/scripts/summarize_workflow_suite.py` | Runs the 20 examples and writes an HTML report. |
| `data/raw/faq_knowledge_base.csv` | The FAQ knowledge base used by the FAQ skill. |
| `data/dictionaries/*.csv` | Reference tables for categories, priorities, systems, and statuses. |
| `WEB_WORKFLOW_DEMO.md` | More detailed technical explanation of the web demo and orchestrator. |

## Vocabulary

**Skill**
: A focused work unit such as classify, check FAQ, draft response, or verify
feedback. In the browser demo, each skill is executed through a Python script in
its skill folder.

**Orchestrator**
: The workflow engine in `scripts/ticket_web_demo.py`. It decides which skill to
run next based on the previous skill's explicit `next_action`.

**Skill package**
: A folder under `skills/` containing both `SKILL.md` and executable scripts.
`SKILL.md` is for LLM agents and humans. The scripts are what the web demo runs.

**Working data**
: CSV files produced during one workflow run. They live under a temporary `/tmp`
folder so the original dataset is not overwritten.

**JSON envelope**
: The structured result each skill returns to the orchestrator. It includes the
skill name, status, ticket id, workflow id, and next action.

## Explain the Orchestrator Carefully

Students often ask: "Why is the orchestrator Python code instead of a skill?"

Answer:

The orchestrator is the control system. It must be deterministic, testable, and
auditable. It does not decide the ticket category or write the customer answer.
It only runs the next skill, passes IDs and file locations, reads the skill's
result, and pauses when a human or customer input is needed.

Use this analogy:

```text
Orchestrator = workflow engine / air traffic controller
Skills = specialized workers
CSV files = shared case file
Web page = control room dashboard
```

## Typical Walkthrough

Use this sequence when explaining one ticket:

1. A ticket is loaded from the example dropdown.
2. `Start Step Mode` creates an isolated workflow run.
3. `Next` runs `receive-ticket`.
4. `Next` runs `classify-prioritize-ticket`.
5. `Next` runs `check-faq-resolution`.
6. The workflow branches:
   - FAQ match -> `draft-faq-response`
   - no FAQ match -> `escalate-to-specialist`
7. A response is drafted and sent.
8. The workflow pauses for customer feedback.
9. Feedback either closes, reopens, asks for clarification, or closes unresolved.

While walking through the web page, point to:

- flow chart: where we are
- narrative: what happened in plain English
- skill input/output: what data went into and came out of the skill
- next skill: what will run next
- customer response: what the customer would receive

## Recommended Demo Tickets

Use these examples from the dropdown:

- `FAQ: customer portal SSO loop` for a clean FAQ-resolution case
- `Human expert: billing API 502` for a human-expert escalation case
- `Reopen: FAQ rejected, then specialist` for a rejection and rework case
- `Reopen: second rejection closes unresolved` for the loop-prevention case
- `Feedback: ambiguous customer reply` for a clarification stall
- `Edge: vague request false-positive FAQ` for a discussion about broad matching

The source is `scripts/ticket_scenarios.py`.

## Scenario Suite

To test all curated examples:

```bash
uv run python skills/summarize-workflow-suite/scripts/summarize_workflow_suite.py
```

The report is written to:

```text
/tmp/customer-ticket-process-suite-report.html
```

Explain this as a management control: a workflow should be tested against a
portfolio of cases, not just demonstrated once.

## Useful Questions to Ask Claude or Codex

Copy one of these prompts:

```text
Using MBA_DEMO_LLM_GUIDE.md and WEB_WORKFLOW_DEMO.md, explain this repo to me
as an MBA student. Avoid deep code details unless they clarify the business
process.
```

```text
Walk me through the example ticket "Human expert: billing API 502" step by
step. For each step, name the skill, the input data, the output data, and why
the workflow chose the next step.
```

```text
Explain why the orchestrator is Python code but the work steps are called
skills. Use a business-process analogy.
```

```text
What are the main governance risks in this AI ticket workflow, and where does
the demo make those risks visible?
```

```text
If I wanted to adapt this repo to a loan approval, insurance claim, or HR
onboarding workflow, what files would I change first?
```

## What Not to Assume

- The browser demo is not calling an LLM live for every step.
- `SKILL.md` files are not loaded by the browser at runtime.
- The orchestrator is not deciding the business outcome by itself.
- Specialist answers are not automatically added to the FAQ knowledge base.
- Customer feedback is still an external input; the workflow pauses for it.

## Adaptation Pattern for Student Projects

To reuse this idea for another process:

1. Draw the business process.
2. Identify 5-10 focused skills.
3. Define the handoff data each skill must produce.
4. Decide where branching happens.
5. Decide where human review is required.
6. Build example scenarios covering normal, exception, and failure paths.
7. Create a suite report so the workflow can be tested repeatedly.

