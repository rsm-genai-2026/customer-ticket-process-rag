#!/usr/bin/env bash
# PreToolUse (git commit): auto-stage the usage log so it ships with every commit.
# Silent on failure — we never want to block a commit just because logging failed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$PROJECT_DIR")"
LOG_DIR="$PROJECT_DIR/usage-log"

if [ -d "$LOG_DIR" ]; then
  (cd "$PROJECT_ROOT" && git add "$LOG_DIR" 2>/dev/null) || true
fi
