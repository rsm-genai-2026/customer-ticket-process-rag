---
name: investigate-specialist-solution
description: Act as the IT specialist to produce a root cause, diagnostic steps, and a customer-safe solution summary for an escalated ticket. Use only when an escalation_decisions.csv row exists for the ticket — i.e. after the escalate-to-specialist skill has run. The output is written to specialist_solutions.csv for the IT team to relay back to the customer.
---

# Investigate specialist solution

Step 7 of the AI-assisted ticketing workflow. The specialist path reviews the handoff, produces a root cause and a customer-safe solution. The script in `scripts/investigate_specialist_solution.py` uses category/system templates so the output is consistent and never invents customer facts.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to investigate. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where `specialist_solutions.csv` is appended. |

Required upstream:

- `data/working/escalation_decisions.csv` row for this ticket.

## How to use this skill

1. **Verify the escalation exists.** If `data/working/escalation_decisions.csv` has no row for the ticket, stop and route to `escalate-to-specialist` first.
2. **Run** the script:

   ```bash
   uv run python skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py --ticket-id TKT-00042
   ```

3. **Report** to the user:
   - The root cause (one line).
   - The diagnostic steps the specialist took.
   - The evidence reviewed.
   - The customer-safe `solution_summary` and the `customer_action_required`.
   - The confidence score and whether engineering follow-up is needed.
   - When the upstream escalation flagged missing information, note which information would raise confidence.
4. **Next valid action:** the IT team relays the solution to the customer via `draft-specialist-response`.

## Example

> User: "What did the specialist find for TKT-00042?"

Run the script, then report:
- Root cause: "Permission cache out of sync between SSO and the Customer Portal."
- Action: "Force a refresh of the user's SSO group membership and clear server-side session cache."
- Customer action: "Sign out completely, wait two minutes, then sign back in."
- Confidence: 0.78 (reduced because the customer hadn't supplied recent error timestamps).
- Next: draft-specialist-response.
