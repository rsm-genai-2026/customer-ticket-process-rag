---
name: audit-ticket-process
description: Inspect a ticket's full history across raw, processed, and working tables and report exactly where it is in the workflow plus the next valid action. Use when the user asks "what's the status of TKT-…", "where are we on this ticket", "what should I do next", or when the workflow needs a sanity check before another skill runs.
---

# Audit a ticket process

The diagnostic skill: tells you what happened, what didn't, and what the next valid action is. Useful at any point — before triage, between steps, or after closure. The script in `scripts/audit_ticket_process.py` reads working data first, falls back to the synthetic historical processed tables, and prints a concise report.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to audit. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where the action log is appended. |

## How to use this skill

1. **Run** the script:

   ```bash
   uv run python skills/audit-ticket-process/scripts/audit_ticket_process.py --ticket-id TKT-00042
   ```

2. **Report** to the user:
   - The current state (e.g. `triaged_no_faq_check_yet`, `faq_match_drafted_awaiting_feedback`, `closed`).
   - The chronological timeline of events (timestamps + skill_name).
   - The next valid action(s) — usually one, occasionally a small set if multiple paths are valid.
3. If the user asked because they were unsure which skill to run next, recommend that skill explicitly.

## Example

> User: "Where are we on TKT-00042?"

Run the script. The output names the state, lists the events that have occurred, and recommends the next skill — for example:

```text
TKT-00042 (Pacific Marine Solutions, premium): triaged_faq_match_response_drafted_awaiting_customer_reply

Timeline:
  2026-04-30T10:14:33 receive-ticket            (action_log)
  2026-04-30T10:42:11 classify-prioritize-ticket triage_decisions
  2026-04-30T10:55:02 check-faq-resolution      faq_decisions      [match=true, faq_id=FAQ-018]
  2026-04-30T11:08:50 draft-faq-response        customer_response_drafts

Next valid action: wait for customer feedback, then run verify-feedback-close-or-reopen --feedback-text "..."
```

The audit always ends with one explicit recommendation.
