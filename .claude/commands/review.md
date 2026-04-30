---
description: Review my current changes (staged + unstaged) and flag issues
---

Review the code I've changed so far in this branch. Steps:

1. Run `git status` and `git diff HEAD` to see what has changed.
2. For each changed Python file, review it for:
   - Missing tests for new or modified functions (see CLAUDE.md — every function needs tests).
   - Bugs, unhandled edge cases, off-by-one errors.
   - Unused imports, dead code, commented-out blocks.
   - Unclear names or overly clever one-liners that a beginner shouldn't ship.
3. Do **not** edit the code. Report findings as a short bulleted list, grouped by file, ordered by severity.

If the diff is empty, just say "No changes to review."
