# audit-ticket-process

The diagnostic automation. Tells you exactly where a ticket is in the workflow
and the single next valid action. Useful at any point — before triage, between
steps, or after closure. Read-only of working data; writes only to the action
log.

## Why this is an automation, not a skill

It is a state-machine derivation over the working CSVs: load the rows, infer
the latest step from timestamps (with a logical-order tie-break), and look up
the next valid action. No model judgment.

## Inputs

| Flag | Required | Default | What it is |
| --- | --- | --- | --- |
| `--ticket-id` | yes | — | The ticket to audit |
| `--data-dir` | no | `data` | Source data directory |
| `--out-dir` | no | `data/working` | Where the action log is appended |
| `--workflow-run-id` | no | — | When present, scopes the audit to that run |
| `--mode {live,demo}` | no | `live` | `live` only inspects `data/working/`; `demo` also includes seeded `data/processed/` rows for tutorial narration |

## Run it

```bash
uv run python automations/audit-ticket-process/scripts/audit_ticket_process.py --ticket-id TKT-00042
```

## What it produces

A text report on stdout with the current state, chronological timeline of
events, workflow flags, and the single recommended next action. JSON envelope
when `--json` is passed.

## Tests

```bash
uv run pytest tests/automations/test_audit_ticket_process.py -v
```
