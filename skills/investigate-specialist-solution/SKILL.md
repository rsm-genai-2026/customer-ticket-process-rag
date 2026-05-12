---
name: investigate-specialist-solution
description: Act as the IT specialist (via LLM) and produce a root cause, diagnostic steps, evidence reviewed, customer-safe solution summary, and customer action for an escalated ticket. Use only when an escalation_decisions.csv row exists — i.e. after the escalate-to-specialist automation has run. The skill calls an LLM to generate genuine diagnostic judgement; it never invents customer facts and refuses to run without the upstream escalation.
---

# Investigate specialist solution (LLM-based)

**Step 7** of the ticket workflow. One of the two real AI skills in this repo
(along with `check-faq-resolution`). It runs only on the specialist branch —
the FAQ check has already declared "no FAQ match" or "match but missing
required info," and `escalate-to-specialist` has chosen a specialist.

The skill asks an LLM to act as the assigned specialist and produce a
diagnosis. The model receives the ticket, triage decision, escalation context,
and specialist profile, and returns one structured JSON object.

## When to invoke

Use this skill when the user asks something like:

- "What did the specialist find for TKT-…?"
- "Investigate TKT-… — the FAQ couldn't help."
- "Have the specialist take a look at this ticket."

Refuse politely if `data/working/escalation_decisions.csv` has no row for the
ticket and route the user to `escalate-to-specialist` first.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket to investigate. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where `specialist_solutions.csv` is appended. |
| `--model` | no | LLM model id. Defaults to `SPECIALIST_INVESTIGATION_MODEL` or the project default in `utils/connect.py`. |

Plus the standard skill-CLI flags (`--workflow-run-id`, `--step-id`,
`--mode`, `--idempotency-mode`, `--json`).

Required upstream:

- A row in `data/working/escalation_decisions.csv` for this ticket.
- An API key in `.env` / `~/.env` / shell: `TRITONAI_API_KEY` (classroom
  gateway) or `OPENAI_API_KEY` (standard OpenAI account).

## How to use this skill

1. **Verify the escalation exists.** If `data/working/escalation_decisions.csv`
   has no row for the ticket, stop and route to `escalate-to-specialist` first.
2. **Run the script:**

   ```bash
   uv run python skills/investigate-specialist-solution/scripts/investigate_specialist_solution.py \
       --ticket-id TKT-00042
   ```

3. **Report** back to the user:
   - The root cause (one line).
   - The diagnostic steps the specialist took.
   - The evidence reviewed.
   - The customer-safe `solution_summary` and the `customer_action_required`.
   - The confidence score and whether engineering follow-up is needed.
   - When the escalation flagged missing information, note that the confidence
     was capped because of it.
4. **Next valid action:** the IT team relays the solution to the customer via
   the `draft-specialist-response` automation.

## What the LLM receives

A compact JSON payload with:

- Ticket fields: subject, description, affected system, symptom detail, steps
  already tried, expected outcome, business impact, customer-reported urgency.
- Triage fields: assigned category, priority, recommended specialist group.
- Escalation fields: escalation reason, the specific question asked of the
  specialist, the handoff summary, and the missing-information flag.
- Specialist fields: id, name, group, seniority, systems supported.

The model must return JSON with `root_cause`, `diagnostic_steps`,
`evidence_reviewed`, `solution_summary`, `customer_action_required`,
`confidence`, `requires_follow_up_flag`, and `reason`.

If the upstream `missing_information_flag` is true, the confidence is capped
at `0.60` regardless of what the model returned — so a ticket with missing
reproduction details always lands in human review.

## Runtime modes

The same script runs in two settings:

- **Terminal / orchestrator / CI**: there is no agent in the loop. The script calls the LLM itself via `utils.connect.ask_json()` (against TritonAI) so the workflow can run end-to-end without an agent.
- **Inside an agent (Claude Code, Codex)**: the agent loads this `SKILL.md`, decides the skill applies, and runs the same script as a subprocess. The script still makes the LLM call itself — keeping the code path identical across both runtimes is intentional, so the same tests and the same web demo exercise the same logic.

For offline tests, set `SPECIALIST_INVESTIGATION_MOCK_JSON` to a JSON string
and the script will return that verbatim instead of calling the model.

## Example

> User: "What did the specialist find for TKT-00042?"

Run the script, then report something like:

- Root cause: "Permission cache out of sync between SSO and the Customer Portal."
- Diagnostic steps: ["Reviewed SSO assertions", "Checked cached group
  membership", "Confirmed customer browser version"]
- Customer action: "Sign out completely, wait two minutes, then sign back in.
  Reply if the issue persists."
- Confidence: 0.78 (or capped to 0.60 if missing info was flagged).
- Next: `draft-specialist-response`.

## Alternatively, via MCP

If the optional MCP exhibit is wired into your client (see `mcp_exhibit/README.md`),
the same investigation is available without invoking the script directly:

> Call the `investigate_specialist_solution` MCP tool with `ticket_id="TKT-00042"`.

The tool wraps this exact script in `--mode demo` and returns the same JSON
envelope. The orchestrator path stays canonical; MCP is only there as an
alternative front door for an LLM client (e.g. Claude Desktop).
