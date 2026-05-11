# draft-faq-response

**Step 5 (FAQ branch)** of the ticket workflow. A deterministic automation that
turns a matched FAQ entry into a customer-facing draft email by filling a
template with the FAQ's `solution_steps` and `required_customer_info`.

## Why this is an automation, not a skill

The skill that *decides* whether the FAQ applies is
`skills/check-faq-resolution/` — that's the LLM call. Once that decision is
made, formatting the resulting message is mechanical: a string template. No
model needed.

## Inputs

| Flag | Required | Default | What it is |
| --- | --- | --- | --- |
| `--ticket-id` | yes | — | The ticket to draft a response for |
| `--data-dir` | no | `data` | Source data directory |
| `--out-dir` | no | `data/working` | Where `customer_response_drafts.csv` is appended |

## Required upstream

A row in `data/working/faq_decisions.csv` for this ticket with
`faq_match_found=true` and `recommended_next_step=draft-faq-response`. The
script refuses and exits non-zero otherwise.

## Run it

```bash
uv run python automations/draft-faq-response/scripts/draft_faq_response.py --ticket-id TKT-00042
```

## What it produces

- One row appended to `data/working/customer_response_drafts.csv` with the
  draft text, the customer action requested, follow-up request, and quality
  check notes.
- A JSON envelope (`--json`) with `next_action="send-customer-response"`.

## Tests

```bash
uv run pytest tests/automations/test_draft_faq_response.py -v
```
