#!/usr/bin/env bash
# Install the investigate-specialist-solution skill into this project's .claude/skills/.
# Run from anywhere: `bash skills/investigate-specialist-solution/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/investigate-specialist-solution"

mkdir -p "$DEST_DIR"
ln -sfn ../../../skills/investigate-specialist-solution/SKILL.md "$DEST_DIR/SKILL.md"
ln -sfn ../../../skills/investigate-specialist-solution/scripts "$DEST_DIR/scripts"

echo "Linked investigate-specialist-solution → $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
