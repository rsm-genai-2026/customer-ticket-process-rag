---
name: draft-faq-response
description: Draft a customer-safe email response based on a matched FAQ entry. Use ONLY when check-faq-resolution has already produced a faq_decisions.csv row with faq_match_found=true and required_customer_info_available=true. If no FAQ match exists yet, route to check-faq-resolution first. If the FAQ check decided to escalate, route to escalate-to-specialist instead.
---

# Draft an FAQ-based response

Step 5 (FAQ branch) of the human ticketing workflow. This skill never invents a fix — it relies on the matched FAQ row's `solution_steps` and `required_customer_info`. The script in `scripts/draft_faq_response.py` refuses to draft if the upstream FAQ decision says escalate.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to draft a response for. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where `customer_response_drafts.csv` is appended. |

Required upstream:

- A row in `data/working/faq_decisions.csv` for this ticket with `faq_match_found=true` and `recommended_next_step=draft-faq-response`. The script will refuse and exit non-zero otherwise.

## How to use this skill

1. **Verify the FAQ branch is the right path.** Read the latest `faq_decisions.csv` row for the ticket. If `faq_match_found=false` or `recommended_next_step` is anything other than `draft-faq-response`, stop and route the user to the appropriate skill (`check-faq-resolution` or `escalate-to-specialist`).
2. **Run** the script:

   ```bash
   uv run python skills/draft-faq-response/scripts/draft_faq_response.py --ticket-id TKT-00042
   ```

3. **Report** to the user:
   - The FAQ id used and the issue pattern.
   - The drafted text (the same text appears as `sent_text` in the working CSV, lightly tightened).
   - The customer action and any follow-up request.
   - Any quality-check warnings (the script flags missing fields itself).
4. **Next valid action:** run `send-customer-response`. After the customer replies to the sent message, run `verify-feedback-close-or-reopen` with the `--feedback-text` you receive.

## Example

> User: "Draft the FAQ reply for TKT-00042."

If the FAQ decision is in good shape, run the script and read the printed draft. End with: "Next: run send-customer-response."
