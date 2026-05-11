# verify-feedback-close-or-reopen

**Step 10** of the ticket workflow. A deterministic automation that classifies
the customer's reply and picks one of four next actions: close, reopen and
re-escalate, request clarification, or close-as-unresolved.

## Why this is an automation, not a skill

Feedback classification here is a keyword / phrase match against
`POSITIVE_PHRASES` and `NEGATIVE_PHRASES` lists, with explicit override rules
for mixed signals ("thanks but still broken" → negative). It is intentionally
visible and rule-based so the loop-prevention behaviour is auditable.

A future iteration could swap this for a small LLM sentiment classifier; if so
it would move under `skills/` and gain a `SKILL.md`.

## Inputs

| Flag | Required | Default | What it is |
| --- | --- | --- | --- |
| `--ticket-id` | yes | — | The ticket the customer is replying about |
| `--feedback-text` | yes | — | The customer's verbatim reply |
| `--it-member-id` | no | `""` | IT team member verifying the feedback (logged) |
| `--data-dir` | no | `data` | Source data directory |
| `--out-dir` | no | `data/working` | Where `feedback_decisions.csv` is appended |

## Required upstream

- `data/working/customer_response_drafts.csv` — must have the drafted response.
- `data/working/sent_messages.csv` — must have a sent response. Without one
  there is nothing for the customer to be replying to.

## Decision rules

- Positive feedback → `close_ticket`.
- Negative feedback, no prior reopen → `reopen_and_escalate`.
- Negative feedback after a previous reopen → `close_unresolved_vendor_followup`
  (prevents infinite loops).
- Ambiguous feedback → `request_clarification`.

## Run it

```bash
uv run python automations/verify-feedback-close-or-reopen/scripts/verify_feedback.py \
    --ticket-id TKT-00042 \
    --feedback-text "Thanks, that fixed it!"
```

## What it produces

- One row appended to `data/working/feedback_decisions.csv` with
  `resolution_accepted`, `verified_rejection`, `reopened_flag`,
  `next_action`, and `verification_notes`.
- A JSON envelope (`--json`) routing to the correct next step.

## Tests

```bash
uv run pytest tests/automations/test_verify_feedback.py -v
```
