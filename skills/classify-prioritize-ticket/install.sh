#!/usr/bin/env bash
# Install the classify-prioritize-ticket skill into this project's .claude/skills/.
# Run from anywhere: `bash skills/classify-prioritize-ticket/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/classify-prioritize-ticket"

mkdir -p "$DEST_DIR"
cp "$SRC_DIR/SKILL.md" "$DEST_DIR/SKILL.md"
cp -r "$SRC_DIR/scripts" "$DEST_DIR/scripts"

echo "Installed classify-prioritize-ticket → $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
