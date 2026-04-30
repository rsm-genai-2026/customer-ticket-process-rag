#!/usr/bin/env bash
# Install the verify-feedback-close-or-reopen skill into this project's .claude/skills/.
# Run from anywhere: `bash skills/verify-feedback-close-or-reopen/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/verify-feedback-close-or-reopen"

mkdir -p "$DEST_DIR"
ln -sfn ../../../skills/verify-feedback-close-or-reopen/SKILL.md "$DEST_DIR/SKILL.md"
ln -sfn ../../../skills/verify-feedback-close-or-reopen/scripts "$DEST_DIR/scripts"

echo "Linked verify-feedback-close-or-reopen → $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
