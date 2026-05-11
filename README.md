# Milestone 02

Push all deliverables to GitHub before the start of class on **May 7th**. Make sure to have the required folders and files starting from the base provided in this repo. GitHub will run automated check to ensure each file is available.

- README.md
- `data/` Contains data and code for data preparation
- `skill/` Contains the _skills_ you create for your project

## Synthetic customer-support ticketing dataset

The `data/` folder ships with a synthetic dataset for the AI-assisted customer-support ticket workflow. The process keeps the original roles visible — **User**, **IT team**, and **IT specialist** — but the repo now implements those steps as deterministic, script-backed AI skills coordinated by a small orchestrator.

## Local web workflow demo

The repo includes a browser demo that lets you submit a ticket and either run the AI-skill workflow all at once or step through it one skill at a time:

```bash
uv run python scripts/ticket_web_demo.py --host 127.0.0.1 --port 8767
```

Then open `http://127.0.0.1:8767`. The step-mode view has Previous/Next controls, 20 example tickets, a prepared narrative with inline dynamic values, branch visualization, and a skill input/output panel that shows the exact fields each skill consumed and produced. See [`WEB_WORKFLOW_DEMO.md`](WEB_WORKFLOW_DEMO.md) for the orchestrator, endpoints, data isolation, branch visualization, example suite, and likely stall points.

For a nontechnical classroom tutorial, open the rendered MBA slide deck at [`slides/ticket-workflow-orchestration.html`](slides/ticket-workflow-orchestration.html). The source is [`slides/ticket-workflow-orchestration.qmd`](slides/ticket-workflow-orchestration.qmd), and the LLM companion guide is [`MBA_DEMO_LLM_GUIDE.md`](MBA_DEMO_LLM_GUIDE.md).

To run the scenario suite and generate an HTML report:

```bash
uv run python skills/summarize-workflow-suite/scripts/summarize_workflow_suite.py
```

Process steps modelled (per the PDF):

1. User submits a ticket.
2. IT team receives the ticket.
3. IT team classifies the ticket and assigns priority.
4. IT team checks whether an FAQ resolution exists.
5. If an FAQ resolution exists → IT team finds it, drafts the message, sends it.
6. If no FAQ resolution → IT team forwards the ticket to an IT specialist.
7. IT specialist analyzes and creates a solution.
8. IT team relays the specialist solution to the user.
9. User accepts or rejects the resolution.
10. If accepted → IT team closes the ticket and records feedback.
11. If rejected → IT team verifies the rejection and (optionally) routes back for one more review cycle.

Process-step → dataset mapping:

| Step | Dataset(s) |
| --- | --- |
| User submits a ticket | `raw/submitted_tickets.csv` |
| IT team triage (classify + prioritize) | `processed/ticket_triage.csv` |
| FAQ check | `processed/faq_checks.csv`, `raw/faq_knowledge_base.csv` |
| Escalation to specialist | `processed/specialist_escalations.csv` |
| Specialist investigation and solution | `processed/specialist_investigations.csv` |
| IT team drafts/sends customer message | `processed/customer_messages.csv` |
| User accept/reject + close + reopen | `processed/resolution_feedback.csv` |
| End-to-end timeline / process mining | `processed/ticket_lifecycle_events.csv` |
| One-row-per-ticket analytics view | `processed/ticket_summary.csv` |
| Reference dictionaries | `dictionaries/{categories,priority_rules,systems,status_codes}.csv` |
| People / customer master | `raw/customers.csv`, `raw/it_team_members.csv`, `raw/it_specialists.csv` |

Actions performed by a human IT team member carry an `it_member_id`; specialist actions carry a `specialist_id`; user actions are tied to `customer_id`. This separation maps directly to the swimlanes in the PDF.

### Regenerate the data

```bash
uv run python scripts/generate_human_ticket_data.py --n-tickets 250 --seed 49502
```

Output goes to `data/raw/`, `data/processed/`, and `data/dictionaries/` (folders are created if missing). The generator is deterministic for a given `--seed`.

### Validate the data

```bash
uv run python scripts/validate_human_ticket_data.py --data-dir data
```

The validator checks file presence, primary/foreign keys, timestamp ordering, terminal `closed_at`, and that key rates (FAQ match, escalation, acceptance, reopen) sit in plausible ranges. It exits non-zero if any check fails.

### Tests

```bash
uv run pytest tests/test_human_ticket_data.py
```

The tests run the generator into a temp directory, run the validator, and assert structural invariants and determinism for a fixed seed.

> All data is synthetic and safe for coursework — no real customer information is included.

## Claude usage logging

> **Heads up:** Your Claude Code usage in this repo is logged. Prompts you submit and the tools Claude runs are written to `.claude/usage-log/<username>-session-*.jsonl` and auto-staged on every `git commit`, so they end up on GitHub alongside your code. This repo lives in a **private GitHub organization** — only you, your teammates, and the instructional team can see it. The instructor uses these logs to see how the class is using the tool — please work normally and know that **file contents and command output are not captured**, only prompts and tool names/arguments. Details in [`.claude/usage-log/README.md`](.claude/usage-log/README.md).

## How to use Claude for milestone 02 (even if you're new to coding)

Claude Code is your coding partner for this assignment. **Describe what you want in plain English** — Claude writes the code, runs the tests, and tells you if something looks wrong. You don't have to remember any commands; the helpers below cover almost every situation you'll hit.

### Two ways to ask for help

**1) Slash commands — type `/` at the start of a message.** A menu appears. Pick one and Claude does the rest.

| Command | When to use it |
| --- | --- |
| `/run-tests` | Run the project tests and see what passed or failed. |
| `/review` | You changed some code — have Claude check it before you commit. |
| `/add-function` | You need a new Python function. Claude asks four short questions, then writes the function **and** its tests in one shot. |
| `/explain <name>` | You see a file, function, or term and want it explained in plain language. Example: `/explain data/examples/1-data-simulation.qmd`. |

You can get the path to a file by right clicking on it in VS Code.

**2) Just describe your problem — some helpers switch on automatically.** You don't have to remember the names below; just type naturally and Claude will pull in the right helper.

| Say something like… | What happens |
| --- | --- |
| _"my setup is broken"_, _"ModuleNotFoundError"_, _"uv sync isn't working"_ | Claude runs a diagnostic and tells you exactly what to fix. |
| _"VS Code opened the wrong folder"_, _"Claude can't find my project"_ | Claude checks your project folder and shows how to reopen the right one. |
| _"how do I push?"_, _"I have a merge conflict"_, _"my push was rejected"_ | Claude prints your git status and walks you through the correct commands. |

### A suggested path through milestone 02

**Before every work session.** Ask:
> _"Please confirm my workspace and Python environment are set up correctly."_

If anything fails, follow the one-line fix Claude prints.

**Deliverable 1 — data preparation (due April 30).**

1. Open the three tutorial files in [`data/examples/`](data/examples/). They show how to simulate data end-to-end with `numpy` and `polars`:
   - `1-data-simulation.qmd` — simulate a numeric dataset
   - `2-llm_persona_simulation.qmd` — use an LLM to generate realistic customer personas
   - `3-llm-dialog-simulation.qmd` — simulate conversation transcripts
2. Pick the closest one to your project and ask:
   > _"Walk me through `data/examples/1-data-simulation.qmd` step by step."_
3. Start your own file under `data/` (e.g., `data/orders.qmd`). Tell Claude:
   > _"Help me simulate weekly customer orders for the process in my Milestone 01 workflow. Use `data/examples/1-data-simulation.qmd` as a template."_
4. Update [`data/README.md`](data/README.md) with a one-line description of each dataset you create.

**Deliverable 2 — build at least four skills (due May 7). At least two must include analytics** (charts, forecasting, statistics, etc.).

1. Open [`skills/example/`](skills/example/) — it's a complete working skill you'll copy the layout from (`SKILL.md`, `scripts/`, `install.sh`, `README.qmd`, plus tests under `tests/skills/`). Ask:
   > _"Explain how `skills/example` works."_
2. For each of your four skills, pick one task from your Milestone 01 workflow and ask:
   > _"Help me build a skill that does `<task>`. Use `skills/example/` as the template."_
3. Review the draft with `/review`, then run the tests with `/run-tests`.
4. Once a skill is finished, install it so Claude can use it on the next session:

   ```bash
   bash skills/<your-skill-name>/install.sh
   ```

**Deliverable 3 — the top-level `README.md` (due May 7).** After your skills exist, ask:
> _"Draft a milestone 02 README that maps each Milestone 01 workflow task to the skill that addresses it, the dataset it uses, and the Python functions involved."_

Then edit the result in your own words so it sounds like your team.

**Deliverable 4 — 10-minute demo video (due May 7).** Nothing to type here. Record your team walking through each skill live with Claude, showing realistic inputs and outputs. Re-run every skill at least once before recording so nothing surprises you on camera.

**At the end of every work session.** Ask:
> _"Please commit my changes with a short message and push to GitHub."_

If the push is rejected (e.g., a teammate pushed first), just say _"my push failed, help"_ — Claude will pull up the git helper and walk you through it safely.

### Where to find working examples in this repo

- [`data/examples/`](data/examples/) — three tutorial Quarto (`.qmd`) files showing data simulation end-to-end with `numpy` + `polars`.
- [`skills/example/`](skills/example/) — a complete sample skill. Mirror its layout for every skill you build.
- [`CLAUDE.md`](CLAUDE.md) — rules Claude already follows automatically: use `uv` for packages, use the approved data stack, and add tests for functions. You don't have to read it, but it explains why Claude makes certain choices.

## Content

This repo contains some examples and settings to help you get started:

- `.claude/settings.json` — tells Claude what it can and can't do without asking for explicit permission
- `.claude/hooks/` — small scripts that run at key moments (auto-format, lint reminder, stale-test reminder, secret scanning, usage logging)
- `.claude/commands/` — project-local slash commands (`/run-tests`, `/review`, `/add-function`, `/explain`)
- `CLAUDE.md` — rules Claude reads automatically on every session (uv workflow, testing requirements, etc.)

See [this video](https://www.youtube.com/watch?v=ZlDnsf_DOzg) for a very nice overview video about providing guidance for Claude Code. Come to the worksession to discuss or ask questions (or post to piazza)

## Hooks — automatic helpers

A *hook* is a small script that Claude Code runs for you automatically at certain moments — you don't have to remember to call them. Think of them like the spell-checker in a word processor: it just runs in the background and catches mistakes before they cause trouble. The hooks configured in this repo are:

- **After Claude edits a Python file** → `ruff` formats it. Your code ends up tidy (correct spacing, quotes, etc.) without you learning the style rules first.
- **After you run `pytest`** → a timestamp is saved so Claude knows when tests were last green.
- **When Claude finishes its turn** → two soft reminders appear if (a) Python files changed since the last test run, or (b) `ruff` found any lint issues. Nothing is blocked — they're nudges.
- **Before every `git commit`** → the commit is paused if any staged file looks like a secret (`.env`, API keys, private keys, …). You'll get a prompt asking if you really want to proceed.
- **On every prompt you send and every tool Claude runs** → a short entry is appended to `.claude/usage-log/` for instructor review (see the Claude usage logging section above).

You don't need to run any of these yourself. They live in `.claude/hooks/` if you want to peek.

## Version control with git (and Claude's `/rewind`)

Version control = a time machine for your files. Every time you *commit*, git takes a snapshot of your whole project. If something later breaks, you can go back to any earlier snapshot.

### The basic loop

Whenever you finish something that works (even partly), ask Claude to commit:

> *"Commit these changes with a short message describing what we just did."*

Claude will run these commands for you:

- `git status` — shows which files changed
- `git add <files>` — marks files to include in the next snapshot
- `git commit -m "…"` — takes the snapshot with a message describing it

When you're done for the day, ask Claude to **push** — this uploads your commits to GitHub so they're backed up and your instructor/teammates can see them:

> *"Push my commits to GitHub."*

### Two different "rewind" tools — know which one you need

You have **two different ways to undo work**, and they do different things. Pick the right one.

| If you want to undo… | Use | What it does |
| --- | --- | --- |
| The last few things Claude said/did in *this conversation* | `/rewind` in Claude Code | Rolls the conversation back to an earlier message. Files Claude edited are restored to how they were at that point. Nothing is committed either way. |
| Code changes that are already committed to git | `git checkout <commit>` or "revert" | Uses git snapshots to bring back an older version of a file or the whole repo. Works even across sessions, days later, on any computer. |

**`/rewind`** is like hitting "Back" in a browser — great for "wait, that last edit was wrong, take it back." Type `/rewind` in the Claude Code prompt and pick the message to return to.

**Git commits** are the real safety net. `/rewind` only remembers the current conversation; if you close Claude Code or start a new session, that history is gone. Commits last forever.

### Rule of thumb

- **Commit often.** After every small piece of working progress — not just at the end. Five small commits beat one giant commit.
- **Push at the end of each work session** so your work is safely on GitHub.
- **Use `/rewind` for "oops" during the current conversation**; use git to recover from anything older than that.
- **Don't panic if something breaks.** Between `/rewind` and your git history, you can almost always get back to a working state. Ask Claude for help restoring if you're unsure.
