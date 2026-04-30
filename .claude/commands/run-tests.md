---
description: Run the project test suite and report results
---

Run `uv run pytest` at the repo root and report the result concisely:

- On success: say how many tests passed, nothing else.
- On failure: show the failing test names and the first assertion/error for each. Do **not** try to fix failures unless I ask you to — just report them.

If the venv is missing, create it with `uv venv` and install deps with `uv sync`, then run the tests.
