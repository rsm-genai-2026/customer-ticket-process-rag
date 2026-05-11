# send-customer-response

**Step 9** of the ticket workflow. The egress step: records that the latest
drafted customer response has been delivered. In this prototype "delivery"
means appending a row to `sent_messages.csv`; in production this is where you
would call your real email or ticketing API.

## Why this is an automation, not a skill

It's I/O. No judgment, no language work — just produce a `delivery_id`, pick
the recipient from the original ticket, and record the send. Refuses to send
if no draft exists. Idempotent on `(workflow_run_id, step_id)` so a retry will
not double-send.

## Inputs

| Flag | Required | Default | What it is |
| --- | --- | --- | --- |
| `--ticket-id` | yes | — | The ticket whose response should be sent |
| `--channel` | no | `email` | One of `email`, `portal`, `phone` |
| `--data-dir` | no | `data` | Source data directory |
| `--out-dir` | no | `data/working` | Where `sent_messages.csv` is appended |

## Required upstream

`data/working/customer_response_drafts.csv` must contain a row for the ticket.
The script refuses otherwise.

## Run it

```bash
uv run python automations/send-customer-response/scripts/send_customer_response.py --ticket-id TKT-00042
```

## What it produces

- One row appended to `data/working/sent_messages.csv` with a fresh
  `delivery_id`, the recipient, channel, and `delivery_status=delivered`.
- A JSON envelope (`--json`) with
  `next_action="verify-feedback-close-or-reopen"`.

## Tests

```bash
uv run pytest tests/automations/test_send_customer_response.py -v
```
