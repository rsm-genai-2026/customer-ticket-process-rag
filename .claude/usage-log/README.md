# Usage log

This directory contains JSONL logs of how Claude Code was used in this project. **Every commit auto-stages these files** (via `.claude/hooks/auto-stage-log.sh`) so the instructor can review them on GitHub.

## What gets logged

One line per event, in `session-YYYYMMDD-<session-id>.jsonl`:

- `{"event": "prompt", "text": "..."}` — a prompt you submitted to Claude (truncated to 2000 chars)
- `{"event": "tool",   "tool": "Bash",  "summary": "..."}` — Claude about to run a tool, with a short summary:
  - Bash → first 500 chars of the command
  - Read / Write / Edit → file path
  - Grep / Glob → pattern
  - WebFetch → url, WebSearch → query
  - Agent → description

## What does NOT get logged

- File contents that Claude reads or writes
- Command output (stdout/stderr)
- Claude's text responses and internal reasoning

This keeps the log small and avoids accidentally committing secrets that might appear inside files.

## For instructors

Each session lives in its own file so you can diff sessions individually and aggregate across students without merge churn.
