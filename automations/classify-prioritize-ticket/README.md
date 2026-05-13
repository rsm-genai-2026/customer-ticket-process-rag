# classify-prioritize-ticket

**Step 2** of the ticket workflow. A deterministic automation that picks a
category, assigns a priority, names the recommended specialist group, and
computes SLA target timestamps. No LLM call.

## Why this is an automation, not a skill

Category selection is a keyword + system-match score against the categories
dictionary. Priority is a small additive rule over customer tier, reported
urgency, and category sensitivity. Both fit on a page and produce deterministic,
auditable rationales — no model needed.

## Inputs

| Flag | Required | Default | What it is |
| --- | --- | --- | --- |
| `--ticket-id` | yes | — | The ticket to triage |
| `--data-dir` | no | `data` | Source data directory |
| `--out-dir` | no | `data/working` | Where `triage_decisions.csv` and the action log are appended |

Plus the standard skill-CLI flags from `utils.ticketing_common.make_skill_parser`.

## Reads

- `data/raw/submitted_tickets.csv`
- `data/raw/customers.csv`
- `data/dictionaries/categories.csv`
- `data/dictionaries/priority_rules.csv`

## Run it

```bash
uv run python automations/classify-prioritize-ticket/scripts/classify_prioritize_ticket.py --ticket-id TKT-00042
```

## What it produces

- One row appended to `data/working/triage_decisions.csv` with the chosen
  category, priority, specialist group, classification evidence, SLA target
  timestamps, and confidence score.
- One row appended to `data/working/ticket_action_log.csv`.
- A JSON envelope (`--json`) with `next_action="check-faq-resolution"`.

## Tests

```bash
uv run pytest tests/automations/test_classify_prioritize_ticket.py -v
```
