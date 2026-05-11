# Claude guidance for this project

These rules apply whenever Claude Code is assisting in this repository. They are read automatically on every session — students do not need to repeat them in prompts.

> **Usage is logged.** Your prompts and the tools Claude runs are written to `.claude/usage-log/session-*.jsonl` and auto-staged on every `git commit`. The instructor reviews these logs. File contents and command output are **not** logged. See [`.claude/usage-log/README.md`](.claude/usage-log/README.md).

## Python environment

- This project uses [`uv`](https://docs.astral.sh/uv/) for environment and package management.
- Create the venv with `uv sync` (reads `pyproject.toml` + `uv.lock`).
- Run any Python command via `uv run …`. If you see a warning about `VIRTUAL_ENV` not matching the project environment, prefix the command with `source .venv/bin/activate &&` in the same shell invocation.
- Data stack: **numpy and polars only**. Do not introduce additional dataframe or plotting libraries unless the instructor asks first.

## Package management — use `uv add`, never `pip`

- Add a runtime dependency: `uv add <package>`
- Add a dev-only dependency: `uv add --dev <package>`
- Install a local package in editable mode: `uv add --editable --dev <path-to-package>`
- Remove: `uv remove <package>`

Never run `pip install`, `python -m pip`, or `uv pip install` — these bypass
`pyproject.toml` / `uv.lock` and the dependency disappears on the next `uv sync`.
(Enforced via `deny` rules in `.claude/settings.json`.)

## Tests — required for every function

Every function added or modified in this project must have one or more tests. This is a hard rule, not a suggestion.

### Where tests live

- `tests/` at the repo root. Mirror the source layout (e.g., `tests/skills/test_check_faq_resolution.py` tests code under `skills/check-faq-resolution/`).
- Test files must be named `test_*.py` and test functions `test_*` so `pytest` discovers them.

### What a test must cover

At minimum:

- A **happy-path** case showing the function does what it claims for typical input.
- One or more **edge cases**: empty input, boundary values, and any documented error condition. If the function validates input, test that invalid input fails the way it should.

If a function genuinely cannot be tested (e.g., a thin wrapper over a third-party SDK with no logic of its own), state that explicitly rather than silently omitting tests.

### How to run tests

```bash
uv run pytest                 # run everything
uv run pytest tests/<file>    # run one file
uv run pytest -k <pattern>    # run matching tests
uv run pytest -x              # stop on first failure
```

### Workflow expectation for Claude

When adding or modifying a function:

1. Write (or propose) the tests **in the same change** as the function.
2. Run the test suite before reporting the task complete.
3. If tests fail, fix the code or the test rather than declaring done.

A `.claude/hooks/check-stale-tests.sh` Stop hook will surface a reminder if Python files under `src/`, `skills/`, `tests/`, or `lib/` changed since the last pytest run. It does not block, but treat the reminder as a prompt to run `uv run pytest`.

## Calling an LLM

Use `utils.connect.ask()` for every LLM call. Do not instantiate `OpenAI(...)` clients inline — `ask()` wraps one with the right `base_url` for TritonAI.

```python
from utils.connect import ask

MODEL = "api-llama-4-scout"   # student changes this one line to swap models
reply = ask("Explain a p-value in one sentence.", model=MODEL)
```

- Single string prompt → wrapped in system + user messages automatically.
- Pre-built messages list → passed through (for multi-turn dialogs).
- Need structured JSON? Use `ask_json(prompt, schema=MyPydanticModel)`.
- Supported model ids route through `https://tritonai-api.ucsd.edu/v1`. The curated short-lists `CHEAP_MODELS` and `EXPENSIVE_MODELS` in `utils/connect.py` are the source of truth for which ids are blessed for classroom use — read them there rather than memorising names. Run `list_models()` for the full live list from the gateway.
- There is also a reserved id `"oauth-gpt"` (see `OAUTH_MODELS`) that routes through `utils.oauth_gpt` and uses the student's own ChatGPT/Codex OAuth subscription instead of TritonAI. First call opens a browser sign-in, then credentials are cached at `~/.oauth_gpt/openai_codex_oauth.json`. Useful when the TritonAI key is missing or the requested team model isn't authorized. Students switch by setting `MODEL = "oauth-gpt"` — no other code changes.
- The API key comes from `TRITONAI_API_KEY` in `.env` (copy `.env.example`).

## Formatting & linting

- Python files are auto-formatted with `ruff format` after Claude writes or edits them (via `.claude/hooks/ruff-format.sh`). You don't need to format by hand.
- On Stop, `.claude/hooks/ruff-check.sh` runs `ruff check` and surfaces any lint findings as a reminder. Fix with `uv run ruff check --fix`.
- `ruff` is invoked via `uvx`, so it works even before it's been added as a dev dependency. Once you run `uv add --dev ruff` the version is pinned in `pyproject.toml`.

## Secret-commit guard

Before any `git commit`, `.claude/hooks/secret-scan.sh` scans the staged files for:

- Sensitive filenames (`.env`, `*.pem`, `id_rsa`, `credentials.json`, …)
- Common secret patterns (AWS keys, OpenAI/Anthropic keys, GitHub tokens, Slack tokens, private-key headers)

If anything matches, Claude will be prompted to ask you before committing. If the match is a false positive, you can approve the commit — but **review first**.

## Slash commands

These project-local commands are available in Claude Code (type `/<name>`):

| Command | What it does |
| --- | --- |
| `/run-tests` | Run the full test suite and report results. |
| `/review` | Review your current diff, flag bugs/missing tests/style issues. Does not edit. |
| `/add-function` | Walk you through adding a new function **with** its tests in one shot. |
| `/explain <thing>` | Beginner-friendly explanation of a file, function, or concept. Read-only. |

## Git workflow

- `git add`, `git commit`, `git status`, `git diff` and other safe read/write git
  commands are pre-allowed.
- Destructive operations (`git push --force`, `git reset --hard`, `git rebase`, etc.) are not pre-allowed and will prompt.
- File deletion (`rm`, `rmdir`) and remote-shell commands (`ssh`, `scp`, `sftp`)
  always prompt for confirmation.

## Project structure reminder

```text
milestone02/
├── CLAUDE.md              # this file
├── README.md              # deliverables and deadlines
├── pyproject.toml         # uv project + dep list
├── .env.example           # copy to .env and fill TRITONAI_API_KEY
├── .claude/
│   ├── settings.json      # permissions + hooks (committed)
│   ├── commands/          # project-local slash commands
│   ├── hooks/             # project-scoped hook scripts (committed)
│   └── usage-log/         # instructor-reviewed usage logs
├── utils/                 # shared helpers — import via `from utils.connect import ask`
├── data/
│   └── examples/          # tutorial .qmd files using the approved data stack
├── skills/                # per-skill code
└── tests/                 # pytest test suite
```
