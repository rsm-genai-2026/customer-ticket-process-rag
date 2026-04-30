---
description: Explain a file, function, or concept at a beginner-friendly level (no edits)
---

Explain the thing named in `$ARGUMENTS`. This can be a file path, a function name, or a general programming concept that showed up in our conversation.

Guidelines:

- Assume I am new to Python and to this codebase. Avoid jargon; when a technical term is unavoidable, define it the first time you use it.
- If `$ARGUMENTS` is a path or function name, read the code first, then explain what it does step by step. Show the relevant lines with `file:line` references so I can click through.
- If I seem confused about a concept from earlier in the conversation, explain it with a tiny concrete example (3-5 lines of code max).
- Do **not** edit any files. This command is read-only.
- Keep the explanation to under ~200 words unless I ask for more detail.
