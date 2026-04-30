#!/usr/bin/env bash
# git-workflow — print the state Claude needs to diagnose push/pull/conflict issues.
# Read-only: never modifies the working tree or the index.

set -uo pipefail

if ! command -v git >/dev/null 2>&1; then
  echo "FAIL: git not installed"
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "FAIL: not inside a git repository (cwd: $(pwd))"
  exit 1
fi

sep() { printf -- "\n--- %s ---\n" "$*"; }

sep "branch / upstream"
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
echo "current branch : $branch"
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
if [ -n "$upstream" ]; then
  echo "upstream       : $upstream"
else
  echo "upstream       : (none — this branch has no remote tracking branch)"
  echo "                 to set one:  git push -u origin $branch"
fi

sep "ahead / behind"
if [ -n "$upstream" ]; then
  # git rev-list returns "ahead<TAB>behind"
  counts=$(git rev-list --left-right --count "$upstream"...HEAD 2>/dev/null || echo "? ?")
  behind=$(echo "$counts" | awk '{print $1}')
  ahead=$(echo  "$counts" | awk '{print $2}')
  echo "local is       : ahead $ahead, behind $behind  (vs $upstream)"
  if [ "$ahead" != "0" ] && [ "$behind" != "0" ]; then
    echo "hint           : branches have diverged — pull --rebase before pushing"
  elif [ "$ahead" != "0" ] && [ "$behind" = "0" ]; then
    echo "hint           : ready to push — 'git push' will work"
  elif [ "$ahead" = "0" ] && [ "$behind" != "0" ]; then
    echo "hint           : remote has new commits — 'git pull --rebase' to catch up"
  else
    echo "hint           : up to date"
  fi
else
  echo "(skipped — no upstream set)"
fi

sep "staged changes (would be in next commit)"
staged=$(git diff --cached --name-status 2>/dev/null || true)
if [ -z "$staged" ]; then
  echo "(none)"
else
  echo "$staged"
fi

sep "unstaged changes (modified but not staged)"
unstaged=$(git diff --name-status 2>/dev/null || true)
if [ -z "$unstaged" ]; then
  echo "(none)"
else
  echo "$unstaged"
fi

sep "untracked files"
untracked=$(git ls-files --others --exclude-standard 2>/dev/null || true)
if [ -z "$untracked" ]; then
  echo "(none)"
else
  echo "$untracked"
fi

sep "unmerged paths (conflicts)"
unmerged=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
if [ -z "$unmerged" ]; then
  echo "(none — no conflicts currently)"
else
  echo "$unmerged"
  echo ""
  echo "Each of the above files contains conflict markers (<<<<<<<, =======, >>>>>>>)."
  echo "Resolve manually, then:  git add <file>"
fi

sep "in-progress operation"
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null || echo ".git")
op="none"
if   [ -d "$GIT_DIR/rebase-merge" ] || [ -d "$GIT_DIR/rebase-apply" ]; then op="rebase"
elif [ -f "$GIT_DIR/MERGE_HEAD" ];                                         then op="merge"
elif [ -f "$GIT_DIR/CHERRY_PICK_HEAD" ];                                    then op="cherry-pick"
elif [ -f "$GIT_DIR/BISECT_LOG" ];                                          then op="bisect"
fi
echo "in progress    : $op"
case "$op" in
  rebase)       echo "continue with  : git rebase --continue        (abort with: git rebase --abort)" ;;
  merge)        echo "continue with  : git commit                   (abort with: git merge --abort)" ;;
  cherry-pick)  echo "continue with  : git cherry-pick --continue   (abort with: git cherry-pick --abort)" ;;
  bisect)       echo "finish with    : git bisect reset" ;;
esac

sep "recent history (last 5 commits)"
git --no-pager log --oneline --decorate -n 5 2>/dev/null || echo "(no commits yet)"

echo ""
