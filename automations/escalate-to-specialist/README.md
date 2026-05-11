# escalate-to-specialist

**Step 6** of the ticket workflow. A deterministic automation that selects an
IT specialist for an escalated ticket and writes a structured handoff package.

## Why this is an automation, not a skill

Specialist selection is a deterministic preference order — same group + same
affected system → same group only → same system across any group → any
specialist, breaking ties by seniority then by id. The handoff text is a
template populated from the ticket. No model needed.

## Inputs

| Flag | Required | Default | What it is |
| --- | --- | --- | --- |
| `--ticket-id` | yes | — | The ticket to escalate |
| `--data-dir` | no | `data` | Source data directory |
| `--out-dir` | no | `data/working` | Where `escalation_decisions.csv` is appended |

## Required upstream

One of:

- `data/working/faq_decisions.csv` row recommending escalation (no FAQ match,
  or matched FAQ without the required customer info), **or**
- `data/working/feedback_decisions.csv` row with `reopened_flag=true` (a
  re-escalation after the customer rejected the first response).

Without one of those signals the automation refuses to run.

## Run it

```bash
uv run python automations/escalate-to-specialist/scripts/escalate_to_specialist.py --ticket-id TKT-00042
```

## What it produces

- One row appended to `data/working/escalation_decisions.csv` with the chosen
  specialist, the escalation reason, the handoff summary, the specific question
  for the specialist, and a `missing_information_flag`.
- A JSON envelope (`--json`) with
  `next_action="investigate-specialist-solution"`.

## Tests

```bash
uv run pytest tests/automations/test_escalate_to_specialist.py -v
```
