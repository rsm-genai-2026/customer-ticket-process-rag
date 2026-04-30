---
name: setup-doctor
description: Diagnose and help fix a broken milestone02 Python environment. Use when the user reports any of - "uv sync fails", "ModuleNotFoundError", "ImportError", "command not found: uv", "numpy/polars/plotnine/openai/pyarrow not found", ".venv is missing or broken", "TRITONAI_API_KEY is empty", "pytest finds nothing", or generally says "my setup is broken" / "nothing works". Runs a diagnostic script and proposes the minimal fix.
---

# setup-doctor

A focused diagnostic skill for when something in the project environment is broken. The student does not need to debug it — just run `scripts/doctor.sh` and interpret the output.

## How to use this skill

1. **Run the diagnostic** from the project root:

   ```bash
   bash .claude/skills/setup-doctor/scripts/doctor.sh
   ```

2. **Read the output top-to-bottom.** Each check prints one of:
   - `OK: …` — nothing to do.
   - `WARN: …` — something unusual but not fatal. Mention it; don't necessarily fix.
   - `FAIL: …  → fix: <command>` — broken. This is what to fix.

3. **Propose the fix.** For each `FAIL`, tell the user the one command that resolves it and **do not run it yourself** — ask them to run it so they see what changed. Exceptions: if the only fix is `uv sync`, you may run it.

4. **Re-run the doctor** after fixes to confirm:  `bash .claude/skills/setup-doctor/scripts/doctor.sh`.

## What the script checks

| Check | Failure hint |
| --- | --- |
| `uv` on PATH | Install uv: https://docs.astral.sh/uv/getting-started/installation/ |
| Python ≥ 3.12 | `uv python install 3.12` then `uv sync` |
| `pyproject.toml` + `uv.lock` present | Confirm the student opened the correct folder (see `workspace-check` skill) |
| `.venv/` exists | `uv sync` |
| Critical imports (`numpy`, `polars`, `plotnine`, `openai`, `pyarrow`, `pydantic`, `dotenv`) | `uv sync` (will pull anything missing from `pyproject.toml`) |
| `.env` file present | `cp .env.example .env` and add `TRITONAI_API_KEY` |
| `TRITONAI_API_KEY` non-empty in `.env` | Paste the key from the course handout |
| pytest runs (collection only) | `uv sync --dev` |

## What this skill does NOT do

- It does not edit `.env` or write secrets (students paste those themselves).
- It does not upgrade uv, Python, or any dependency beyond what `uv sync` would do.
- It does not diagnose IDE / workspace-folder problems — use the `workspace-check` skill for that.
