# summarize-workflow-suite

A meta-automation that runs the 20 curated example scenarios from
`scripts/ticket_scenarios.py` through the workflow orchestrator and writes an
HTML report. Useful as a regression check and as a teaching artifact ("show me
how the workflow performs across a portfolio of cases").

## Why this is an automation, not a skill

The scenarios themselves may call LLM-based skills (specifically
`check-faq-resolution` and `investigate-specialist-solution`), but this
wrapper only sequences runs and tallies branch outcomes. No model judgment
inside the wrapper.

## Inputs

| Flag | Required | Default | What it is |
| --- | --- | --- | --- |
| `--work-root` | no | `/tmp/...` | Isolated temp folder for scenario runs |
| `--report` | no | `/tmp/...` | HTML file to write |
| `--limit` | no | — | First N scenarios to run (handy for quick checks) |
| `--scenario-id` | no | — | Run one named scenario instead of the whole suite |
| `--json` | no | — | Emit a JSON summary instead of text |

## Run it

```bash
# Full suite
uv run python automations/summarize-workflow-suite/scripts/summarize_workflow_suite.py

# Quick smoke test (first 5)
uv run python automations/summarize-workflow-suite/scripts/summarize_workflow_suite.py --limit 5
```

## What it produces

An HTML report at `/tmp/customer-ticket-process-suite-report.html` (by default)
covering, per scenario:

- expected versus actual branch (FAQ vs specialist)
- terminal feedback action (close / reopen / close-unresolved / clarification)
- skills run
- review flags
- FAQ-backlog candidate notes for specialist cases

## Tests

```bash
uv run pytest tests/automations/test_summarize_workflow_suite.py -v
```
