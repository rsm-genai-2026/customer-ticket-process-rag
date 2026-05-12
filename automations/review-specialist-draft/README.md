# review-specialist-draft

Human-in-the-loop gate that sits between `draft-specialist-response` and
`send-customer-response`. A supervisor reviews the LLM-drafted reply,
optionally edits the text, and either **approves** (workflow continues
to send) or **rejects** (workflow re-runs `investigate-specialist-solution`
once before forcing approval on the second pass).

This is an automation, not a skill — there is no LLM judgment here. The
LLM already drafted the reply upstream. This step records a human
decision and, on approve+edit, patches the `sent_text` column of the
existing draft row so the next step sends the edited version.

## CLI

Two modes share one script:

**Mode 1 — "awaiting decision".** Called with no `--decision` flag.
Reads the latest `customer_response_drafts.csv` row for the ticket and
emits an envelope with `status=awaiting_input` and the draft text in
`outputs.draft_text`. Writes nothing. The orchestrator surfaces this to
the supervisor.

**Mode 2 — "decision applied".** Called with `--decision approve` or
`--decision reject`, plus the supervisor's edited text and notes. On
approve, writes a row to `data/working/specialist_draft_reviews.csv`
and (if edited) updates the `sent_text` of the latest draft row, then
emits `next_action=send-customer-response`. On reject, writes the
review row and emits `next_action=investigate-specialist-solution`,
unless the latest review on this ticket was already a reject — in that
case it forces approval, so the loop is bounded to one retry.

## Outputs

- `data/working/specialist_draft_reviews.csv` — one row per decision:
  ticket_id, created_at, original_text, edited_text, decision (approve|reject),
  reviewer_notes, retry_count, workflow_run_id, step_id.

## Upstream / downstream

- Upstream: `draft-specialist-response` (which now emits
  `next_action=review-specialist-draft` instead of `send-customer-response`).
- Downstream on approve: `send-customer-response`.
- Downstream on reject (first time): `investigate-specialist-solution`.
- Downstream on reject (second time): forced approve →
  `send-customer-response` with a note.
