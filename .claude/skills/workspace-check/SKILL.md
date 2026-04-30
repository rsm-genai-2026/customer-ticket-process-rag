---
name: workspace-check
description: Verify that the student has opened the `milestone02` folder as the root project in their IDE (VS Code) or Claude Desktop, not a parent/sibling directory. Use when file paths look wrong, when CLAUDE.md rules don't seem to apply, when Claude Code starts in the wrong cwd, when `.env` is "missing" but actually lives one level down, or when the user says "VS Code opened the wrong folder" / "Claude can't find my project". Runs a small check script and explains how to fix the setup in VS Code or Claude Desktop.
---

# workspace-check

Confirms the current working directory and key project files line up with the `milestone02` project root. If the student opened a parent folder (e.g. `rsm-teaching/`), Claude Code's cwd is wrong, relative paths break, and `CLAUDE.md` / `.env` won't be picked up.

## How to use this skill

1. **Run the check** from wherever Claude Code currently is:

   ```bash
   bash .claude/skills/workspace-check/scripts/workspace_check.sh
   ```

2. **Read the verdict.** The script prints one of:
   - `WORKSPACE OK` — cwd is the milestone02 folder and all marker files are here. Nothing to do.
   - `WRONG CWD` — explain to the student how to re-open the correct folder (instructions below).
   - `MARKER FILES MISSING` — the folder looks right by name but key files (`CLAUDE.md`, `pyproject.toml`, `.claude/settings.json`) aren't here. Either the student is in a partial clone or they renamed files.

3. **Explain the fix** based on what the student uses:
   - **VS Code:** `File → Open Folder…` → navigate to the `milestone02` directory and choose it. The VS Code status bar should show `milestone02` as the workspace name, not its parent.
   - **VS Code (terminal, already inside a parent repo):** `code <path-to>/milestone02` re-opens with the correct root.
   - **Claude Desktop:** `Settings → Projects → Add Project` → point it at the absolute path of the `milestone02` folder. In a new chat, pick that project from the project selector before typing.
   - **Claude Code CLI:** always launch with `cd <path-to>/milestone02 && claude` — the cwd at launch becomes the project root.

4. **Do not touch files** in this skill. It is read-only diagnostics. Never run `cd` inside a tool call expecting it to persist; the user needs to re-launch their IDE / Claude session.

## Why this matters in this project

- `.claude/settings.json` — hooks, permissions, the usage-log — only applies when Claude Code runs *inside* the milestone02 folder.
- `CLAUDE.md` — project rules (uv only, no pandas, tests required, etc.) — only auto-loads when the session root matches.
- `.env` lookups (`TRITONAI_API_KEY`) via `python-dotenv` walk up from cwd; starting from a sibling folder will find nothing.
- Relative paths in examples (`data/examples/...`, `skills/...`) break when the root is wrong.

## Related

- `setup-doctor` — for environment/package problems *after* you've confirmed the workspace is correct.
- `git-workflow` — for pull/push/conflict issues inside this project.
