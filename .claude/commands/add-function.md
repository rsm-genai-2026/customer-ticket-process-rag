---
description: Add a new function WITH tests (one-shot — the function and its tests ship together)
---

I want to add a new function. Ask me these questions in order (one at a time, wait for each answer before the next):

1. **Where should the function live?** (e.g., which file under `skills/` or `src/`, or should I create a new one)
2. **What is the function's name and what does it do?** (short description)
3. **What inputs does it take, and what does it return?** (types + meaning of each)
4. **Any edge cases or error conditions I should handle?** (e.g., empty input, invalid values)

Once I answer all four:

1. Implement the function in the file I specified.
2. In `tests/` create or update a `test_<name>.py` file with:
   - A happy-path test for typical input.
   - Tests for each edge case I mentioned.
   - Tests for any obvious edge case I forgot (empty input, None, wrong type). Flag any you add so I can confirm.
3. Run `uv run pytest tests/<file>` and report the result.
4. If tests fail, fix the implementation or tests and re-run until green.
