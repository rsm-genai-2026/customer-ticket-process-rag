---
name: send-customer-response
description: Deliver the latest drafted customer response and record the send. Use after draft-faq-response or draft-specialist-response has produced a row in customer_response_drafts.csv. Refuses to send if no draft exists. Idempotent on (workflow_run_id, step_id) — re-running will not double-send.
---

# Send the customer response

The egress step of the workflow. Up to this point the IT team has only drafted text in `data/working/customer_response_drafts.csv`; nothing has reached the customer. This skill records that the response has actually been sent (in this prototype, "sending" means appending to `data/working/sent_messages.csv`; in a production system this is where you would call your email/ticketing API).

The script in `scripts/send_customer_response.py` is deterministic and never invents a fix. It reads the latest draft for the ticket, picks the recipient from the original ticket, "delivers" the message, and emits the orchestration envelope.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket whose response should be sent. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where `sent_messages.csv` is appended. |
| `--channel` | no (default `email`) | Channel to record on the send (`email`, `portal`, `phone`). |
| `--workflow-run-id` / `--step-id` | no | Orchestrator-supplied; auto-generated if blank. |
| `--mode {live,demo}` | no | live (default). demo is reserved; not used for routing decisions in this skill. |
| `--json` | no | Emit the JSON envelope instead of human-readable text. |

Required upstream:

- `data/working/customer_response_drafts.csv` — must contain a row for this ticket.

## How to use this skill

1. **Verify a draft exists.** If `data/working/customer_response_drafts.csv` has no row for the ticket, stop and route the user back to the appropriate drafting skill (`draft-faq-response` or `draft-specialist-response`).
2. **Run** the script:

   ```bash
   uv run python skills/send-customer-response/scripts/send_customer_response.py --ticket-id TKT-00042
   ```

3. **Report** to the user:
   - Confirmation that the response was recorded.
   - The recipient (from `submitted_by_email`), channel, and `delivery_id`.
   - The `next_action`: wait for customer feedback, then run `verify-feedback-close-or-reopen`.

## Example

> User: "Send the response we drafted for TKT-00042."

Run the script. It records a `delivery_id` like `DEL-TKT-00042-…`, writes one row to `sent_messages.csv`, and reports the next step.
