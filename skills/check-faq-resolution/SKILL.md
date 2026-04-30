---
name: check-faq-resolution
description: Decide whether a ticket can be resolved using an existing FAQ entry. Use after triage when the IT team needs to know whether to draft an FAQ-based response or escalate to a specialist. Reads the working triage decision (or falls back to the historical processed/ticket_triage.csv for synthetic examples) and scores active FAQ entries against the ticket.
---

# Check FAQ resolution

Step 3 of the human ticketing workflow. The IT team has just triaged the ticket; now they search the FAQ knowledge base. The script in `scripts/check_faq_resolution.py` filters to active FAQs, scores them by category, system, and text overlap, and decides whether the top hit is strong enough to use.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to check. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where `faq_decisions.csv` is appended. |

Required upstream data:

- A triage decision in `data/working/triage_decisions.csv` for this ticket. If absent, the script falls back to the historical row in `data/processed/ticket_triage.csv` so existing synthetic tickets still work for demos.

## How to use this skill

1. **Confirm triage exists.** Either via `data/working/triage_decisions.csv` (preferred — produced by the `classify-prioritize-ticket` skill) or by a historical row in `data/processed/ticket_triage.csv`. If neither exists, stop and route the user to `classify-prioritize-ticket` first.
2. **Run** the script:

   ```bash
   uv run python skills/check-faq-resolution/scripts/check_faq_resolution.py --ticket-id TKT-00042
   ```

3. **Report** to the user:
   - Whether an FAQ match was found (`faq_match_found`).
   - The matched FAQ id, issue pattern, and match confidence (when found).
   - The candidate FAQ ids that were considered.
   - Whether the customer has supplied the required information.
   - The recommended next step:
     - **Match found, info present:** route to `draft-faq-response`.
     - **No match (or weak match, or missing required info):** route to `escalate-to-specialist`.

## Example

> User: "Can we resolve TKT-00042 with the FAQ KB?"

Run the script, then say something like:
- Match: yes — `FAQ-018` (`browser_redirect_loop_on_sso`), confidence 0.84.
- Required customer info available: true.
- Next: draft an FAQ-based response with `draft-faq-response`.
