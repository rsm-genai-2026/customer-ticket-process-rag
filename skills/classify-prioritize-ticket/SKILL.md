---
name: classify-prioritize-ticket
description: Classify a customer-support ticket into a category and assign a priority, recommended specialist group, and SLA target timestamps. Use when the user is performing IT-team triage on a freshly received ticket — typically right after the receive-ticket step. Do NOT use for tickets that are already triaged unless the user explicitly asks to re-triage.
---

# Classify and prioritize a ticket

Step 2 of the AI-assisted ticketing workflow. The skill picks a category (login_access, software_bug, …), a priority (low / medium / high / urgent), and the right specialist group, then computes SLA targets from the priority. The math lives in `scripts/classify_prioritize_ticket.py` and is fully deterministic.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to triage. |
| `--data-dir` | no (default `data`) | Source directory. |
| `--out-dir` | no (default `data/working`) | Where `triage_decisions.csv` and the action log are appended. |

The script reads:

- `data/raw/submitted_tickets.csv`
- `data/raw/customers.csv`
- `data/dictionaries/categories.csv`
- `data/dictionaries/priority_rules.csv`

## How to use this skill

1. **Confirm the ticket exists.** If the user has not run `receive-ticket` yet, point that out and offer to run it first. (You may proceed straight to triage if the ticket id is valid — receive-ticket is for human readability, not a hard prerequisite.)
2. **Run** the script:

   ```bash
   uv run python skills/classify-prioritize-ticket/scripts/classify_prioritize_ticket.py --ticket-id TKT-00042
   ```

3. **Report** to the user:
   - The assigned category, classification evidence (keywords + system match), and confidence.
   - The assigned priority and one-line rationale.
   - The recommended specialist group.
   - The SLA target timestamps for first response and resolution.
4. **Next valid action:** check the FAQ knowledge base via the `check-faq-resolution` skill.

## Example

> User: "Triage TKT-00042 for me."

Run the script, then report something like:
- Category: `login_access` (matched: ["login", "portal"], system: Customer Portal). Confidence 0.83.
- Priority: `high` — premium tier with high urgency on a customer-portal access issue.
- Specialist group: `identity_security`.
- First-response target: 2026-04-30T18:00:00+00:00 (2h). Resolution target: 2026-05-01T16:00:00+00:00 (24h).
- Next: run `check-faq-resolution`.
