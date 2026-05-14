# Customer-ticket process

A reference repo that turns a customer-support workflow into a sequence of
small, testable **steps** — LLM-based **skills** and deterministic
**automations** — coordinated by a thin **orchestrator**. The intended
audience is graduate business-school students who want a complete worked
example of how to put GenAI inside a process — not how to make a single
chatbot.

Everything in here is synthetic. No real customers, tickets, or employees.

---

## Start here

```bash
uv sync                                                   # install deps
cp .env.example .env && $EDITOR .env                      # add TRITONAI_API_KEY
source .venv/bin/activate
uv run python scripts/orchestrator.py --port 8767         # run the demo
```



Then open `http://127.0.0.1:8767`, pick an example ticket from the dropdown,
click **Start Step Mode**, and walk forward one step at a time. Each panel
shows what data went in, what data came out, and which step runs next.

> Trouble shooting: If you can an "Address already in use" error, use the two steps below

```bash
kill $(lsof -ti :8767)
uv run python scripts/orchestrator.py --port 8767         # run the demo
```

---

## The shape of the workflow

The repo implements a customer-support process from the source PDF
(`customer-ticket-process.pdf`). The user-facing narrative is:

1. User submits a ticket.
2. IT team receives the ticket.
3. IT team classifies and prioritizes it.
4. IT team checks whether an FAQ resolution exists.
5. If FAQ matches → IT team drafts an FAQ response, sends it.
6. If FAQ does not match → IT team escalates to an IT specialist.
7. Specialist investigates and creates a solution.
8. IT team drafts a specialist-based customer response, a **supervisor**
   reviews the draft, then it is sent.
9. Customer accepts → close. Customer rejects → reopen and re-escalate once,
   then close-unresolved on a second rejection.
10. If the specialist solution was accepted, the workflow drafts a
    candidate FAQ entry and a **supervisor** approves or skips before it
    lands in the knowledge base.

Under the hood that narrative is implemented as **thirteen step folders**:
three LLM-based **skills** under `skills/<name>/` and ten deterministic
**automations** under `automations/<name>/`. Two of the automations are
human-in-the-loop pause gates (`review-specialist-draft`,
`approve-faq-promotion`). The only branching is at step 4 (FAQ vs.
specialist). Loops are bounded — a ticket can be reopened at most once
before it is closed as unresolved.

### Skills vs. automations

The thirteen step folders are split into two folders by a strict rule:

| Folder | What lives there | Count | Has `SKILL.md`? | Calls an LLM? |
| --- | --- | --- | --- | --- |
| `skills/` | LLM-based **skills** | 3 | yes | yes |
| `automations/` | Deterministic steps (incl. 2 HITL pause gates) | 10 | no | no |

**Skills** in this repo follow Anthropic's definition: a folder with a
`SKILL.md` that an LLM agent (Claude Code, Codex) loads at runtime to decide
*when* the skill applies, and a script the agent invokes that performs the
work — including a real LLM call. Three steps qualify:

- `skills/check-faq-resolution/` — asks an LLM whether the FAQ knowledge
  base contains a direct resolution for the ticket.
- `skills/investigate-specialist-solution/` — asks an LLM to act as the
  assigned IT specialist and produce a root cause + diagnostic steps +
  customer-safe solution.
- `skills/draft-faq-candidate/` — after a specialist solution is accepted
  by the customer, asks an LLM to draft a candidate FAQ entry (issue
  pattern, symptoms, solution steps, required customer info) so the next
  similar ticket can match against it. A supervisor approves or skips
  through the `approve-faq-promotion` HITL gate before it lands in the
  knowledge base.

**Automations** are everything else: deterministic Python scripts that look
up rows, score keywords, fill templates, and write CSVs. They never call an
LLM. They live under `automations/` with just a `README.md` and a
`scripts/` folder — no `SKILL.md`, no `install.sh`, because there is
nothing for an LLM agent to decide. The orchestrator runs them as
subprocesses. Two of them (`review-specialist-draft`,
`approve-faq-promotion`) are HITL pause gates: the script records a
human's decision rather than computing one. A tenth automation
(`audit-ticket-process`) runs after a ticket closes to write the audit
trail.

That split is the most important lesson of the repo: **an "AI-assisted
workflow" is not the same thing as a workflow where every step is an LLM
call.** AI earns its keep on the genuine judgement calls; the rest of the
process stays rule-based, auditable, and fast.

---

## How a step is shaped

Both skills and automations follow the same external contract — same CLI
flags, same JSON envelope, same shared helpers — so the orchestrator can
drive any of them identically. The only differences are *where* they live
and whether they call an LLM.

A skill folder under `skills/` looks like:

```text
skills/check-faq-resolution/
├── SKILL.md                          # agent-loaded contract: when to invoke
├── README.md                         # human-readable docs
└── scripts/check_faq_resolution.py   # the executable (calls an LLM inside)
```

All skills are registered with Claude Code at once by running the
repo-root `install.sh`, which auto-discovers every folder under `skills/`
that has a `SKILL.md` and symlinks it into `.claude/skills/<name>/`:

```bash
bash install.sh
```

An automation folder under `automations/` is simpler — there is no agent
contract, so no `SKILL.md` and no install step:

```text
automations/receive-ticket/
├── README.md                         # what the automation does
└── scripts/receive_ticket.py         # the executable (deterministic)
```

Every script — skill or automation — takes the same standard flags:

| Flag | Meaning |
| --- | --- |
| `--ticket-id` | which ticket to act on |
| `--data-dir` / `--out-dir` | where to read source data and write working CSVs |
| `--workflow-run-id` / `--step-id` | identifiers passed by the orchestrator |
| `--mode {live,demo}` | live = use only fresh `data/working/` rows from this run; demo = also accept seeded `data/processed/` rows so a single step can be narrated against the synthetic history |
| `--idempotency-mode {skip,replace}` | re-running the same `(workflow_run_id, step_id)` either skips (default) or rewrites the existing row, so retries are safe |
| `--json` | emit a JSON envelope instead of a human-readable summary |

The standard argument list is built by `make_skill_parser()` in
`utils/ticketing_common.py`. Step-specific flags (e.g. `--feedback-text` on
`verify-feedback-close-or-reopen`, `--model` on the three LLM skills,
`--decision` on the two HITL gates) are added on top.

### The envelope

Every step (skill or automation) prints a JSON envelope with a stable shape:

```json
{
  "status": "ok",
  "skill_name": "check-faq-resolution",
  "workflow_run_id": "wf-…",
  "step_id": "check-faq-resolution-…",
  "ticket_id": "TKT-00042",
  "next_action": "draft-faq-response",
  "confidence": 0.91,
  "review_required": false,
  "artifact_refs": ["working/faq_decisions.csv"],
  "outputs": {…},
  "error": null
}
```

The orchestrator reads `next_action` to choose the next step. The IT team
reads `review_required` to decide where a human still has to look. The whole
audit trail is written to `data/working/ticket_action_log.csv`.

### Shared helpers

The plumbing every script would otherwise duplicate lives in one place,
`utils/ticketing_common.py`, and is imported by both skills and automations:

- `make_skill_parser()` — the standard CLI surface above.
- `make_envelope()` / `emit_envelope()` — build and print the JSON contract.
- `emit_error()` — emit a uniform error envelope and return the right exit
  code; every script's `except` blocks call this.
- `find_step_row()` — the idempotency check.
- `append_csv_row()` / `replace_step_row()` — schema-stable CSV writes
  guarded by a POSIX advisory lock so concurrent runs cannot interleave.
- `append_action_log()` — the audit trail.
- `latest_working_row()` — read the most recent upstream row for a ticket.
- `needs_human_review()` — the human-review threshold (`confidence < 0.60`
  or an explicit `extra` flag).

That last point matters: students adding a new step don't redesign error
handling, CSV schemas, or idempotency. They write the business logic and
call into the shared helpers.

---

## The orchestrator

The orchestrator is in `scripts/orchestrator.py`. It is intentionally
boring code, not a step:

1. Creates an isolated run folder under
   `/tmp/customer-ticket-process-web-demo/<workflow_run_id>/`.
2. Copies the baseline `data/raw/` and `data/dictionaries/` into that
   folder.
3. Runs one step at a time via `subprocess` with `--json`,
   `--workflow-run-id`, `--step-id`. It picks the right script from
   `STEP_SCRIPTS` (skills live under `skills/`, automations under
   `automations/`).
4. Reads each envelope's `next_action` to decide what to run next.
5. Pauses when a step needs outside input — the customer's reply on
   `verify-feedback-close-or-reopen`, or a supervisor's decision on the
   two HITL automations (`review-specialist-draft`,
   `approve-faq-promotion`). The web form supplies the payload before
   the orchestrator resumes.

Keeping the control flow in plain Python means the demo is testable and
predictable. The work — classification, FAQ matching, drafting, specialist
investigation — stays inside the scripts. See
[`docs/web-workflow-demo.md`](docs/web-workflow-demo.md) for the API
endpoints, branch visualisation, and likely stall points.

---

## Running the rest

```bash
# Run from the repo root. The three LLM skills need TRITONAI_API_KEY set;
# the ten automations are deterministic and run offline.

# Automations (deterministic)
uv run python automations/receive-ticket/scripts/receive_ticket.py --ticket-id TKT-00042
uv run python automations/classify-prioritize-ticket/scripts/classify_prioritize_ticket.py --ticket-id TKT-00042
uv run python automations/draft-faq-response/scripts/draft_faq_response.py --ticket-id TKT-00042
uv run python automations/escalate-to-specialist/scripts/escalate_to_specialist.py --ticket-id TKT-00042
uv run python automations/draft-specialist-response/scripts/draft_specialist_response.py --ticket-id TKT-00042
uv run python automations/review-specialist-draft/scripts/review_specialist_draft.py --ticket-id TKT-00042 --decision approve
uv run python automations/send-customer-response/scripts/send_customer_response.py --ticket-id TKT-00042
uv run python automations/verify-feedback-close-or-reopen/scripts/verify_feedback.py --ticket-id TKT-00042 --feedback-text "Thanks, that fixed it!"
uv run python automations/approve-faq-promotion/scripts/approve_faq_promotion.py --ticket-id TKT-00042 --decision skip
uv run python automations/audit-ticket-process/scripts/audit_ticket_process.py --ticket-id TKT-00042

# Skills (LLM-based)
uv run python skills/check-faq-resolution/scripts/check_faq_resolution.py --ticket-id TKT-00042
uv run python skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py --ticket-id TKT-00042
uv run python skills/draft-faq-candidate/scripts/draft_faq_candidate.py --ticket-id TKT-00042
```

Each step writes one row to a working CSV in `data/working/` (or appends to
`ticket_action_log.csv`), so you can `cat` the files between steps and see
the trail.

### RAG demo (Naive vs HyQ)

A separate browser demo compares two retrieval strategies — naive
sliding-window chunking vs HyQ (Hypothetical Queries) — over the 18
simulated proprietary documents in `rag/knowledge_base/`. Build the
indices once, then run the demo:

```bash
uv run python -m rag.build_index --mode naive
uv run python -m rag.build_index --mode hyq
uv run python scripts/rag_demo.py --port 8766
# → http://127.0.0.1:8766
```

The UI shows side-by-side hits, the matched HyQ question for each, and
the rendered chunk markdown. See [`rag/README.md`](rag/README.md) for the
design notes and teaching points.

### Scenario suite

The repo ships 20 curated example tickets in `data/examples/ticket_scenarios.py`,
covering FAQ resolution, specialist escalation, rejection-then-rework,
ambiguous feedback, and the loop-prevention edge case. Run them all:

```bash
uv run python automations/summarize-workflow-suite/scripts/summarize_workflow_suite.py
```

The HTML report at `/tmp/customer-ticket-process-suite-report.html` shows
expected versus actual branch, terminal feedback action, review flags, and
FAQ-backlog candidates from specialist cases.

---

## Data

```text
data/
├── raw/            # source-of-truth tables (people, FAQ, raw tickets)
├── processed/      # synthetic historical tables for each ticketing step
├── dictionaries/   # reference enumerations (categories, priorities, …)
└── working/        # written by skills + automations during a workflow run
```

Regenerate everything (deterministic for a given seed):

```bash
uv run python data/generate_human_ticket_data.py --n-tickets 250 --seed 49502 --out-dir data
uv run python data/validate_human_ticket_data.py --data-dir data
```

The validator runs structural checks and exits non-zero on any failure. See
[`data/README.md`](data/README.md) for the column-level schema and
[`docs/human-ticketing-dataset-plan.md`](docs/human-ticketing-dataset-plan.md)
for the original generation design.

---

## Testing

```bash
uv run pytest                       # full suite (316 tests)
uv run pytest tests/skills          # the three LLM skills + end-to-end
uv run pytest tests/automations     # the ten deterministic steps
uv run pytest tests/utils           # shared infra in utils/ticketing_common.py
uv run pytest -k faq_resolution     # one pattern
```

Test layout mirrors the source layout:

- `tests/utils/test_ticketing_common.py` — the shared CLI parser, envelope,
  error helper, CSV writers, idempotency, action log.
- `tests/skills/test_check_faq_resolution.py` — the FAQ skill (mocked LLM
  via `FAQ_RESOLUTION_MOCK_JSON`).
- `tests/skills/test_investigate_specialist_solution.py` — the specialist
  skill (mocked LLM via `SPECIALIST_INVESTIGATION_MOCK_JSON`).
- `tests/skills/test_draft_faq_candidate.py` — the FAQ-candidate skill
  (mocked LLM via `FAQ_CANDIDATE_MOCK_JSON`).
- `tests/skills/test_ticketing_workflow_e2e.py` — full workflow paths
  (FAQ branch, specialist branch, reopen-then-close-unresolved). Patches
  all LLM calls so the tests run offline.
- `tests/skills/test_workflow_orchestrator.py` — orchestrator + subprocess
  invocation paths.
- `tests/automations/test_<name>.py` — one test file per deterministic
  step, with at least a happy-path and an edge-case test.

When you add a new step, add tests in the same change. See
[`CLAUDE.md`](CLAUDE.md) for the standing rules Claude Code follows on this
project — `uv` for packages, numpy + polars only, tests required.

---

## Building your own workflow

The intended adaptation pattern is:

1. Draw the business process. Identify lanes, decisions, loops, and handoffs.
2. Pick the smallest set of steps that covers the process — one per decision
   or transformation, not one per LLM call.
3. Decide where each step reads from and writes to. Put fast-changing
   working data in a separate folder from source-of-truth data.
4. Define the envelope contract (or reuse this one). Make sure every step
   names its `next_action` so the orchestrator never has to guess.
5. Build deterministic automations first. Only promote a step to a skill
   (LLM-based) where the judgment is genuinely hard and a wrong call is
   easy to verify.
6. Write a test for every function, scenarios for every branch, and a
   summary report so you can see the workflow's behaviour over a portfolio
   of cases — not just one happy path.

The same shape works for loan approvals, claims triage, HR onboarding,
admissions, and procurement.

---

## Further reading

- [`docs/web-workflow-demo.md`](docs/web-workflow-demo.md) — orchestrator
  internals, API endpoints, branching, likely stall points.
- [`docs/mba-demo-llm-guide.md`](docs/mba-demo-llm-guide.md) — guide for
  asking Claude Code or Codex to explain this repo back to you.
- [`docs/it-ticketing-skills-plan.md`](docs/it-ticketing-skills-plan.md) —
  original design notes for the skill set.
- [`docs/human-ticketing-dataset-plan.md`](docs/human-ticketing-dataset-plan.md) —
  design notes for the synthetic dataset.
- [`CLAUDE.md`](CLAUDE.md) — project-wide rules that Claude Code reads on
  every session (uv workflow, testing requirements, secret-scan hook, …).
