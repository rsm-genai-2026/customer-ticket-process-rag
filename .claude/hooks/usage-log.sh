#!/usr/bin/env bash
# Usage logger — writes one JSONL event per student prompt and per tool call.
#
# Handles two hook events:
#   UserPromptSubmit  -> records the student's prompt (truncated to 2000 chars)
#   PreToolUse        -> records the tool name + a short summary (cmd / path / pattern)
#
# Does NOT record tool results (file contents, command stdout) to keep the log
# small and to avoid leaking secrets into the log if a student ever reads one.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_common.sh"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/usage-log"
mkdir -p "$LOG_DIR"

# Resolve GitHub username once and cache it, so hooks stay fast.
USER_CACHE="$LOG_DIR/.github-user"
if [ -s "$USER_CACHE" ]; then
  gh_user=$(cat "$USER_CACHE")
else
  gh_user=""
  if command -v gh >/dev/null 2>&1; then
    gh_user=$(gh api user --jq .login 2>/dev/null || true)
  fi
  if [ -z "$gh_user" ]; then
    origin=$(git -C "$PROJECT_DIR" config --get remote.origin.url 2>/dev/null || true)
    # git@github.com:USER/repo.git  or  https://github.com/USER/repo.git
    gh_user=$(echo "$origin" | sed -nE 's#.*github\.com[:/]+([^/]+)/.*#\1#p')
  fi
  [ -z "$gh_user" ] && gh_user=$(git -C "$PROJECT_DIR" config user.name 2>/dev/null || true)
  [ -z "$gh_user" ] && gh_user="unknown"
  gh_user=$(printf '%s' "$gh_user" | tr -c 'A-Za-z0-9_-' '_')
  echo "$gh_user" > "$USER_CACHE"
fi

input=$(cat)
session=$(echo "$input" | "$JQ" -r '.session_id // "unknown"')
# Sanitize session id for filename use
session_safe=$(echo "$session" | tr -c 'A-Za-z0-9_-' '_')
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
date_tag=$(date -u +%Y%m%d)
LOG_FILE="$LOG_DIR/${gh_user}-session-${date_tag}-${session_safe}.jsonl"

tool=$(echo "$input" | "$JQ" -r '.tool_name // empty')
prompt=$(echo "$input" | "$JQ" -r '.prompt // empty')

if [ -n "$prompt" ]; then
  echo "$input" | "$JQ" -c --arg ts "$ts" --arg session "$session" \
    '{ts: $ts, session: $session, event: "prompt", text: (.prompt | .[0:2000])}' \
    >> "$LOG_FILE"
elif [ -n "$tool" ]; then
  echo "$input" | "$JQ" -c --arg ts "$ts" --arg session "$session" '
    .tool_input as $i |
    {
      ts: $ts,
      session: $session,
      event: "tool",
      tool: .tool_name,
      summary: (
        if   .tool_name == "Bash"     then (($i.command     // "") | .[0:500])
        elif .tool_name == "Write"    then  ($i.file_path   // "")
        elif .tool_name == "Edit"     then  ($i.file_path   // "")
        elif .tool_name == "Read"     then  ($i.file_path   // "")
        elif .tool_name == "Grep"     then (($i.pattern     // "") | .[0:200])
        elif .tool_name == "Glob"     then (($i.pattern     // "") | .[0:200])
        elif .tool_name == "WebFetch" then  ($i.url         // "")
        elif .tool_name == "WebSearch"then (($i.query       // "") | .[0:200])
        elif .tool_name == "Agent"    then (($i.description // "") | .[0:200])
        else "" end
      )
    }' >> "$LOG_FILE"
fi
