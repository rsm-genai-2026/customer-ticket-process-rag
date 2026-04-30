---
name: draft-specialist-response
description: Draft a customer-facing email response that translates a specialist solution into plain language. Use only after investigate-specialist-solution has produced a row in specialist_solutions.csv. If the ticket reopened, the draft acknowledges the earlier attempt briefly. Refuses to draft when the upstream specialist solution is missing.
---

# Draft a specialist-based response

Step 8 of the human ticketing workflow. The IT team takes the specialist's solution and writes a plain-language message to the customer. The script in `scripts/draft_specialist_response.py` is deterministic — it never invents customer facts and refuses to run without a specialist solution.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to draft a response for. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where `customer_response_drafts.csv` is appended. |

Required upstream:

- `data/working/specialist_solutions.csv` — must have a row for this ticket.
- `data/working/escalation_decisions.csv` — used to detect a re-escalation (post-rejection) so the draft can acknowledge the earlier attempt.

## How to use this skill

1. **Verify the specialist solution exists.** If `data/working/specialist_solutions.csv` has no row for the ticket, stop and route to `investigate-specialist-solution` first.
2. **Run** the script:

   ```bash
   uv run python skills/draft-specialist-response/scripts/draft_specialist_response.py --ticket-id TKT-00042
   ```

3. **Report** to the user:
   - The drafted text (`sent_text`).
   - The customer action and follow-up request.
   - Whether this is a post-rejection draft (acknowledges the earlier attempt).
   - Any quality-check warnings.
4. **Next valid action:** run `send-customer-response`. After the customer replies to the sent message, run `verify-feedback-close-or-reopen` with the customer's reply via `--feedback-text`.

## Example

> User: "Draft the specialist response for TKT-00042."

If the specialist solution is in place, run the script. End with: "Next: run send-customer-response."
