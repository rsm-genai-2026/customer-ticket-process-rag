#!/usr/bin/env bash
# Install the check-faq-resolution skill into this project's .claude/skills/.
# Run from anywhere: `bash skills/check-faq-resolution/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/check-faq-resolution"

mkdir -p "$DEST_DIR"
ln -sfn ../../../skills/check-faq-resolution/SKILL.md "$DEST_DIR/SKILL.md"
ln -sfn ../../../skills/check-faq-resolution/scripts "$DEST_DIR/scripts"

echo "Linked check-faq-resolution → $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
