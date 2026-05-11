---
name: check-faq-resolution
description: Decide whether a ticket can be resolved using an existing FAQ entry. Use after triage when the IT team needs to know whether to draft an FAQ-based response or escalate to a specialist. The skill passes the ticket, triage decision, and active FAQ table to an LLM and asks for a structured JSON decision.
---

# Check FAQ resolution

Step 3 of the AI-assisted ticketing workflow. The ticket has just been triaged; now the skill asks an LLM whether the FAQ knowledge base contains a direct resolution. The script in `scripts/check_faq_resolution.py` sends the ticket, triage decision, and active FAQ entries to the model and requires a JSON answer.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to check. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where `faq_decisions.csv` is appended. |
| `--model` | no | LLM model to use. Defaults to `FAQ_RESOLUTION_MODEL` or `gpt-4.1-mini`. |

Required upstream data:

- A triage decision in `data/working/triage_decisions.csv` for this ticket. In live mode this is required. Use `--mode demo` only when intentionally narrating seeded historical examples.
- An API key in `.env`, `~/.env`, or the shell: `TRITONAI_API_KEY` for the classroom gateway, or `OPENAI_API_KEY` for a standard OpenAI account.

## How to use this skill

1. **Confirm triage exists.** Use `data/working/triage_decisions.csv`, produced by the `classify-prioritize-ticket` skill. If absent, stop and route the user to `classify-prioritize-ticket` first.
2. **Run** the script:

   ```bash
   uv run python skills/check-faq-resolution/scripts/check_faq_resolution.py --ticket-id TKT-00042
   ```

3. **Report** to the user:
   - Whether an FAQ match was found (`faq_match_found`).
   - The matched FAQ id, issue pattern, and match confidence (when found).
   - The FAQ ids that were reviewed by the LLM.
   - Whether the customer has supplied the required information for the matched FAQ.
   - The recommended next step:
     - **Match found, info present:** route to `draft-faq-response`.
     - **No match (or weak match, or missing required info):** route to `escalate-to-specialist`.

## What the LLM receives

The model receives a compact JSON prompt with:

- Ticket fields such as subject, description, affected system, symptom detail, steps already tried, and business impact.
- Triage fields such as assigned category, priority, and specialist group.
- Every active FAQ entry with its id, category, system, symptoms, solution steps, and required customer information.

The model must return JSON with `faq_match_found`, `faq_id`, `confidence`, `required_customer_info_available`, `reason`, `ticket_evidence`, and `faq_evidence`.

For production hardening ideas, see `IMPROVEMENT_IDEAS.md`. The main skill stays intentionally direct so students can understand the workflow before adding retrieval, calibration, redaction, or fallback logic.

## Example

> User: "Can we resolve TKT-00042 with the FAQ KB?"

Run the script, then say something like:
- Match: yes — `FAQ-001`, confidence 0.91.
- Required customer info available: true.
- Next: draft an FAQ-based response with `draft-faq-response`.
