---
name: escalate-to-specialist
description: Route a ticket to an IT specialist with a complete handoff package. Use when (a) the FAQ check declared no match, (b) the FAQ check matched but the customer has not provided required information, or (c) the ticket was reopened after a rejected resolution and needs re-investigation. The script selects the right specialist and writes the escalation row.
---

# Escalate to specialist

Step 6 of the human ticketing workflow. The IT team can no longer self-serve and needs an IT specialist. The script in `scripts/escalate_to_specialist.py` selects a specialist by group and system support, builds a handoff summary, and writes one row to `data/working/escalation_decisions.csv`.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to escalate. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where escalation_decisions.csv is appended. |

Required upstream:

- `data/working/triage_decisions.csv` — for `recommended_specialist_group`. In live mode, this must come from the current workflow run.
- `data/working/faq_decisions.csv` (preferred for first-time escalations) — to confirm the FAQ check refused the ticket.
- `data/working/feedback_decisions.csv` — when this is a re-escalation after a rejected resolution (`reopened_flag=true`).

Either FAQ-decision-says-escalate **or** feedback-says-reopen must be present. The script will refuse otherwise.

## How to use this skill

1. **Determine the trigger** — say which of the three reasons is in play. If unsure, run `audit-ticket-process` first.
2. **Run** the script:

   ```bash
   uv run python skills/escalate-to-specialist/scripts/escalate_to_specialist.py --ticket-id TKT-00042
   ```

3. **Report** to the user:
   - The chosen specialist id, name, group, and seniority.
   - Whether they support the affected system.
   - The escalation reason.
   - The handoff summary (symptom, impact, steps already tried).
   - The specific question put to the specialist.
4. **Next valid action:** the IT specialist investigates → run `investigate-specialist-solution` next.

## Example

> User: "Escalate TKT-00042 to a specialist."

The script picks SP-005 from `identity_security` who supports the Customer Portal, writes the handoff, and prints something like:
- Reason: no FAQ match.
- Specialist: SP-005 (senior, identity_security, supports Customer Portal).
- Specific question: "Can you confirm whether the user's session/MFA enrollment is current and whether SSO group membership has propagated?"
- Next: investigate-specialist-solution.
