#!/usr/bin/env bash
# Install the escalate-to-specialist skill into this project's .claude/skills/.
# Run from anywhere: `bash skills/escalate-to-specialist/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/escalate-to-specialist"

mkdir -p "$DEST_DIR"
ln -sfn ../../../skills/escalate-to-specialist/SKILL.md "$DEST_DIR/SKILL.md"
ln -sfn ../../../skills/escalate-to-specialist/scripts "$DEST_DIR/scripts"

echo "Linked escalate-to-specialist → $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
