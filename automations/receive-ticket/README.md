# receive-ticket

**Step 1** of the ticket workflow. A deterministic automation that loads a newly
submitted ticket plus the customer master record and prints a human-readable
intake summary. No LLM call — pure data lookup.

## Why this is an automation, not a skill

The work here is "look up two rows by primary key and format them." There is no
judgment to outsource to a model. The folder lives under `automations/` so the
naming reflects that.

## Inputs

| Flag | Required | Default | What it is |
| --- | --- | --- | --- |
| `--ticket-id` | yes | — | The ticket to summarize, e.g. `TKT-00042` |
| `--data-dir` | no | `data` | Directory containing `raw/`, `processed/`, `dictionaries/` |
| `--out-dir` | no | `data/working` | Where the action log is appended |

Plus the standard skill-CLI flags from `lib.ticketing_common.make_skill_parser`
(`--workflow-run-id`, `--step-id`, `--mode`, `--idempotency-mode`, `--json`).

## Run it

```bash
uv run python automations/receive-ticket/scripts/receive_ticket.py --ticket-id TKT-00042
```

## What it produces

- A text intake summary on stdout (customer, subject, urgency, business impact,
  steps already tried).
- One row appended to `data/working/ticket_action_log.csv`.
- A JSON envelope on stdout when `--json` is passed, with
  `next_action="classify-prioritize-ticket"`.

## Tests

```bash
uv run pytest tests/automations/test_receive_ticket.py -v
```
