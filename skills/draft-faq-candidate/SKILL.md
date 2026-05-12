---
name: draft-faq-candidate
description: After a specialist solution has resolved a ticket and the customer has accepted, ask an LLM to draft a candidate FAQ entry (issue pattern, symptoms, solution steps, required customer info, category, system) from the ticket plus specialist solution. Writes one candidate row that a human supervisor reviews via the approve-faq-promotion automation. Only runs on the specialist branch after a positive customer feedback decision.
---

# Draft FAQ candidate (LLM-based)

**Step 10** of the workflow, only on the specialist branch when the
customer accepts the specialist's solution. One of the LLM-judgment
skills in this repo. Its purpose is to turn a one-off specialist
solution into a reusable knowledge-base entry that future tickets can
match against in `check-faq-resolution`.

The skill never adds to the FAQ knowledge base on its own. It only
*drafts* a candidate. The human-in-the-loop approval happens in the
downstream `approve-faq-promotion` automation.

## When to invoke

Use this skill when the user asks something like:

- "Should we promote this fix to the FAQ?"
- "Draft a knowledge-base entry from TKT-…'s solution."

Refuse politely if no row exists in `data/working/specialist_solutions.csv`
for the ticket, or if the latest `feedback_decisions.csv` row for the
ticket is not `close_ticket`. Either condition means there is no
accepted specialist solution to turn into a FAQ.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--ticket-id` | yes | The ticket whose solution should become a FAQ. |
| `--data-dir` | no (default `data`) | Source data dir. |
| `--out-dir` | no (default `data/working`) | Where `faq_candidates.csv` is appended. |
| `--model` | no | LLM model id. Defaults to `FAQ_CANDIDATE_MODEL` or the project default in `utils/connect.py`. |

Plus the standard skill-CLI flags (`--workflow-run-id`, `--step-id`,
`--mode`, `--idempotency-mode`, `--json`).

Required upstream:

- A row in `data/working/specialist_solutions.csv` for this ticket.
- A `close_ticket` decision in `data/working/feedback_decisions.csv` for
  this ticket — the FAQ candidate is only drafted from solutions that
  actually worked.
- An API key in `.env` / `~/.env` / shell: `TRITONAI_API_KEY` (classroom
  gateway) or `OPENAI_API_KEY` (standard OpenAI account).

## How to use this skill

1. **Confirm upstream.** If `specialist_solutions.csv` or the
   `close_ticket` feedback row is missing, stop and explain.
2. **Run the script:**

   ```bash
   uv run python skills/draft-faq-candidate/scripts/draft_faq_candidate.py \
       --ticket-id TKT-00042
   ```

3. **Report** back to the user:
   - The proposed category and affected system.
   - The one-line `issue_pattern`.
   - The proposed symptoms, solution steps, and required customer info.
   - The model's confidence and a one-paragraph rationale.
4. **Next valid action:** the supervisor reviews and approves (or skips)
   the candidate via the `approve-faq-promotion` automation.

## What the LLM receives

A compact JSON prompt with:

- Ticket fields: subject, description, affected system, symptom detail,
  steps already tried.
- Specialist solution fields: root cause, diagnostic steps, evidence
  reviewed, solution summary, customer action required.
- The customer's affirmative feedback text.
- The list of valid `category` values from `dictionaries/categories.csv`.
- The list of valid `system_name` values from `dictionaries/systems.csv`.

The model must return JSON with `category`, `system_name`,
`issue_pattern`, `symptoms`, `solution_steps`, `required_customer_info`,
`confidence`, and `reasoning`. Lists are returned as JSON arrays and
written to the CSV as pipe-joined strings to match the FAQ KB's
existing schema.

## Runtime modes

The same script runs in two settings:

- **Terminal / orchestrator / CI**: there is no agent in the loop. The script calls the LLM itself via `utils.connect.ask_json()` (against TritonAI) so the workflow can run end-to-end without an agent.
- **Inside an agent (Claude Code, Codex)**: the agent loads this `SKILL.md`, decides the skill applies, and runs the same script as a subprocess. The script still makes the LLM call itself — keeping the code path identical across both runtimes is intentional, so the same tests and the same web demo exercise the same logic.

For offline tests, set `FAQ_CANDIDATE_MOCK_JSON` to a JSON string and the
script will return that verbatim instead of calling the model.

## Example

> User: "Promote TKT-00042's fix to the FAQ."

Run the script, then report something like:

- Category: `login_access`. System: `Customer Portal`.
- Issue pattern: "sso_session_drift_after_password_change".
- Symptoms: "User signs in but is logged out within a minute" pipe
  "Token refresh fails silently".
- Solution: "Sign out completely" pipe "Wait two minutes" pipe "Sign
  back in".
- Required customer info: "Browser, OS, sign-in timestamp".
- Confidence: 0.82.
- Next: a supervisor approves or skips this via `approve-faq-promotion`.
