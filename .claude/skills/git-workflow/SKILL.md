---
name: git-workflow
description: Help the student with git push, pull, and merge-conflict resolution in this project. Use when the user reports any of - "failed to push", "rejected non-fast-forward", "branch is behind", "diverged branches", "CONFLICT", "merge conflict", "unmerged paths", "I can't pull", "I can't push", "help me merge", "there's a conflict" - or generally asks for help pulling, pushing, or resolving conflicts. Starts by printing git state, then walks through the correct fix step-by-step.
---

# git-workflow

Help the student push, pull, and resolve merge conflicts without destroying work. Always start from the current git state — never guess.

## Step 1: look at the state

Always run this first:

```bash
bash .claude/skills/git-workflow/scripts/git_state.sh
```

Read every section it prints:
- `branch / upstream` — where HEAD is and what remote branch it tracks.
- `ahead/behind` — how the local branch relates to the remote.
- `staged / unstaged / untracked` — what would be in the next commit.
- `unmerged paths` — files currently in a conflict state.
- `in-progress operation` — rebase / merge / cherry-pick / bisect in progress.

## Step 2: match the state to the right fix

Use the table below. Do **not** run destructive commands (`git reset --hard`, `git push --force`, `git clean -fd`, `git checkout .`) unless the student explicitly asks for them — those commands are in the `ask` list of `.claude/settings.json` precisely because they can wipe out hours of work.

### A) "I want to push my work"

1. If `staged` or `unstaged` changes exist → commit them first (`/review` or `/add-function` if adding code; otherwise `git add <files>` + `git commit -m "..."`). Never `git add -A` blindly — the secret-scan hook will fire on `.env`-style files, but reviewing manually is safer.
2. If `ahead > 0` and `behind == 0` → `git push`. Done.
3. If `behind > 0` → see case **C**.

### B) "I want to pull latest"

1. If `unstaged` or `staged` changes exist → commit (or `git stash`) first. Pulling on top of uncommitted edits risks conflicts in files the student doesn't realize they've touched.
2. `git pull --rebase` is the project default — keeps history linear and avoids merge commits. Tell the student this before running.
3. If it completes cleanly → done.
4. If it stops with CONFLICTs → see case **D**.

### C) "Rejected, non-fast-forward" / "branch is behind"

The remote has commits yours doesn't. Pull first, then push.

```bash
git pull --rebase origin <branch>
# resolve conflicts if any (case D)
git push
```

Never use `git push --force` to paper over this on a shared branch. It overwrites teammates' commits.

### D) Resolving a merge conflict

1. The script output's `unmerged paths:` section lists the conflicted files.
2. Open each one. Look for conflict markers:
   ```
   <<<<<<< HEAD
   my version of this block
   =======
   the incoming version
   >>>>>>> origin/main
   ```
3. For each marker block, decide what the file should look like — usually a combination of both sides, not a naive choice of one. Edit the file to that final content and delete the `<<<<<<<`, `=======`, `>>>>>>>` lines.
4. `git add <resolved-file>` to mark it resolved.
5. Repeat for every file in `unmerged paths`.
6. Continue whatever operation was in progress:
   - rebase: `git rebase --continue`
   - merge:  `git commit` (git pre-fills a merge message — keep it)
   - cherry-pick: `git cherry-pick --continue`
7. If the student wants to bail out entirely: `git rebase --abort` / `git merge --abort` / `git cherry-pick --abort`. These are safe — they restore the pre-operation state.

### E) "I committed to the wrong branch" / "I want to undo my last commit"

Do **not** run `git reset --hard`. Instead:
- Undo the commit, keep changes staged: `git reset --soft HEAD~1`.
- Undo the commit, keep changes unstaged: `git reset HEAD~1` (same as `--mixed`).
- Then check out the correct branch and commit there.

### F) "I have no idea what's going on"

Run the state script. Then read it to the student and ask:
- "Do you want to keep your local changes or discard them?"
- "Is anyone else pushing to this branch?"
- "Is this an assignment branch or `main`?"

Don't start typing commands until those answers are clear.

## Guard rails for this project

- The repo's `.claude/settings.json` already marks `git push`, `git reset`, `git rebase`, `git merge`, `git cherry-pick`, and `git clean` as **ask** — each will prompt the student before running. Use that as a pause point to explain what the command will do before they approve it.
- `git commit` runs a secret-scan pre-commit hook that will flag `.env`-style files. If the hook fires, do not try to push through it — open the flagged file and confirm there's no real secret.
