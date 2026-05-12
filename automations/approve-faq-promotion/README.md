# approve-faq-promotion

Human-in-the-loop gate that sits after `draft-faq-candidate` and before
`audit-ticket-process` on the specialist-branch close path. A supervisor
reviews the LLM-drafted FAQ candidate, optionally edits the fields, and
either **approves** (the row is appended to the run's
`data/raw/faq_knowledge_base.csv`) or **skips** (the candidate is
discarded but the decision is recorded). Either way, the workflow
continues to `audit-ticket-process`.

This is an automation, not a skill — there is no LLM judgment here. The
LLM already drafted the candidate upstream. This step records a human
decision and, on approve, mutates the per-run copy of the FAQ KB so the
new entry would be visible to future `check-faq-resolution` runs in the
same workflow run. The repo's source `data/raw/` is never modified —
the orchestrator copies baseline data into `/tmp` per run.

## CLI

Two modes share one script:

**Mode 1 — "awaiting decision".** Called with no `--decision` flag.
Reads the latest `faq_candidates.csv` row for the ticket and emits an
envelope with `status=awaiting_input` and the candidate fields in
`outputs`. Writes nothing.

**Mode 2 — "decision applied".** Called with `--decision approve` or
`--decision skip`, plus an optional `--candidate-json` containing the
supervisor's edited fields. On approve, appends a new row to
`data/raw/faq_knowledge_base.csv` (a stable `FAQ-<ticket_id>-<ts>` id
is generated) and writes a row to
`data/working/faq_promotion_decisions.csv`. On skip, writes the
decision row only. Either way emits `next_action=audit-ticket-process`.

## Outputs

- `data/working/faq_promotion_decisions.csv` — one row per decision:
  ticket_id, created_at, candidate_ticket_id, decision (approve|skip),
  new_faq_id, edited (true/false), reviewer_notes, workflow_run_id, step_id.
- On approve: a new row in `data/raw/faq_knowledge_base.csv` with
  `active_flag=true` and `owner` set to "workflow_promotion".

## Upstream / downstream

- Upstream: `draft-faq-candidate` (which writes to `faq_candidates.csv`
  and emits `next_action=approve-faq-promotion`).
- Downstream: `audit-ticket-process` (in both approve and skip cases).
