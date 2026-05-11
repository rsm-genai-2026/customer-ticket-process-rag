---
name: summarize-workflow-suite
description: Run the curated web-demo ticket scenarios through the orchestrator and produce an HTML report that summarizes branch choices, terminal outcomes, review needs, and FAQ-backlog candidates. Use when students or reviewers ask whether the ticket workflow works across many examples.
---

# Summarize the workflow suite

This is a teaching and regression-report skill. It runs the curated examples
from `scripts/ticket_scenarios.py` through the same `TicketWorkflowOrchestrator`
used by the web demo, then writes an HTML report for human review.

## What the script needs

| Input | Required | What it is |
| --- | --- | --- |
| `--work-root` | no | Isolated temp folder where scenario runs are written. |
| `--report` | no | HTML file to write. |
| `--limit` | no | First N scenarios to run. Useful for quick checks. |
| `--scenario-id` | no | Run one named scenario instead of the full suite. |
| `--json` | no | Emit a JSON summary instead of text. |

## How to use this skill

Run from the repo root:

```bash
uv run python skills/summarize-workflow-suite/scripts/summarize_workflow_suite.py
```

For a quick smoke test:

```bash
uv run python skills/summarize-workflow-suite/scripts/summarize_workflow_suite.py --limit 5
```

Report back:

- how many scenarios passed
- which scenarios failed and why
- which examples used the FAQ branch vs specialist branch
- which specialist cases look like candidates for new FAQ entries after human approval

The skill does not modify `data/raw/` or `data/working/`; it uses isolated
workflow folders under `/tmp` by default.
