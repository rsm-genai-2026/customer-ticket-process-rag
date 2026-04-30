---
name: receive-ticket
description: Produce a concise IT-team intake summary for a customer-support ticket. Use when the user asks "what's in this ticket", "summarize ticket TKT-…", or is starting the human IT workflow on a newly submitted ticket and needs the receive/intake step before triage. The skill loads the ticket and customer context and writes one row to the action log.
---

# Receive and summarize a ticket

Use this skill at step 1 of the human ticketing workflow: the IT team has just received a ticket and needs a quick, human-readable intake summary before classifying or prioritizing it. The actual data work lives in `scripts/receive_ticket.py`. Your job is to know the ticket id, run the script, and report the printed summary.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to summarize, e.g. `TKT-00042`. |
| `--data-dir` | no (default `data`) | Directory containing `raw/`, `processed/`, `dictionaries/`. |
| `--out-dir` | no (default `data/working`) | Where the action log is appended. |

## How to use this skill

1. **Get the ticket id.** If the user did not provide one, ask once.
2. **Run** the script from the repo root:

   ```bash
   uv run python skills/receive-ticket/scripts/receive_ticket.py --ticket-id TKT-00042
   ```

3. **Report** back to the user:
   - The customer name, tier, and SLA plan.
   - The reported subject and one-line symptom summary.
   - The customer's reported urgency and business impact.
   - Whether the customer has already tried any troubleshooting steps.
   - The next valid action (always: classify and prioritize via the `classify-prioritize-ticket` skill).
4. **Do not invent** any field. If a value is empty in the data, say so explicitly.

## Example

> User: "Show me what's in TKT-00042 — I need to start working it."

Run the script with `--ticket-id TKT-00042`. The printed summary is your reply. End with a one-line nudge: "Next step: classify and prioritize this ticket."
