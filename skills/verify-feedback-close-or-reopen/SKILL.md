---
name: verify-feedback-close-or-reopen
description: Verify a customer's reply to a sent response and decide whether to close the ticket, reopen and re-escalate, request clarification, or close as unresolved. Use after send-customer-response has recorded delivery and the customer replies. Requires --feedback-text. Prevents infinite reopen loops by closing-as-unresolved when the customer rejects after a prior reopen cycle.
---

# Verify customer feedback and close or reopen

Step 9 of the human ticketing workflow. The IT team has a customer reply in hand and needs a decision: close, reopen, request clarification, or close-unresolved. The script in `scripts/verify_feedback.py` classifies the feedback, verifies that a response was actually sent, looks at any prior `feedback_decisions.csv` for this ticket, and produces a clear next action.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket the customer is replying about. |
| `--feedback-text` | yes | The customer's verbatim reply (quote it!). |
| `--it-member-id` | no | The IT team member verifying the feedback (for the action log). |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where `feedback_decisions.csv` is appended. |

Required upstream:

- `data/working/customer_response_drafts.csv` — must have the drafted response.
- `data/working/sent_messages.csv` — must have a sent response for this ticket. Without a sent response there is nothing for the customer to reply to.

## How to use this skill

1. **Get the customer's reply text** from the user. Quote it verbatim — do not paraphrase.
2. **Run** the script:

   ```bash
   uv run python skills/verify-feedback-close-or-reopen/scripts/verify_feedback.py \
       --ticket-id TKT-00042 \
       --feedback-text "Thanks, that fixed it!"
   ```

3. **Report** to the user:
   - `resolution_accepted`: true / false.
   - `next_action`: `close_ticket`, `reopen_and_escalate`, `request_clarification`, or `close_unresolved_vendor_followup`.
   - The `verification_notes` (one-line rationale).
   - If `next_action=reopen_and_escalate`, recommend running `escalate-to-specialist` next.
   - If `next_action=request_clarification`, do not escalate; wait for a clearer customer accept/reject signal.
   - If `next_action=close_ticket` or `close_unresolved_vendor_followup`, the ticket is done; recommend `audit-ticket-process` to confirm the closed state.

## Example

> User: "Customer replied: 'Tried it, still broken.'"

Run with `--feedback-text "Tried it, still broken."`. The script classifies as negative, sees no prior reopen, and recommends `next_action=reopen_and_escalate`. End with: "Next: escalate-to-specialist."
