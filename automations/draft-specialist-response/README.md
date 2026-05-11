# draft-specialist-response

**Step 8** of the ticket workflow. A deterministic automation that turns a
specialist solution into a plain-language customer email. When the ticket was
reopened after a rejected first attempt, the draft includes a short
acknowledgement of that earlier attempt.

## Why this is an automation, not a skill

The skill that *generates* the specialist solution is
`skills/investigate-specialist-solution/` — that's the LLM call. Re-wording the
solution into a customer-safe message is mechanical templating.

## Inputs

| Flag | Required | Default | What it is |
| --- | --- | --- | --- |
| `--ticket-id` | yes | — | The ticket to draft a response for |
| `--data-dir` | no | `data` | Source data directory |
| `--out-dir` | no | `data/working` | Where `customer_response_drafts.csv` is appended |

## Required upstream

- `data/working/specialist_solutions.csv` — must have a row for this ticket.
- `data/working/escalation_decisions.csv` — used to detect a re-escalation so
  the draft can acknowledge the earlier attempt.

## Run it

```bash
uv run python automations/draft-specialist-response/scripts/draft_specialist_response.py --ticket-id TKT-00042
```

## What it produces

- One row appended to `data/working/customer_response_drafts.csv` with the
  draft, the customer action, follow-up request, and quality-check notes.
- A JSON envelope (`--json`) with `next_action="send-customer-response"`.

## Tests

```bash
uv run pytest tests/automations/test_draft_specialist_response.py -v
```
