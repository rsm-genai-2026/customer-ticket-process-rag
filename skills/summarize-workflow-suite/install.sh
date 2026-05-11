#!/usr/bin/env bash
# Install the summarize-workflow-suite skill into this project's .claude/skills/.
# Run from anywhere: `bash skills/summarize-workflow-suite/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/summarize-workflow-suite"

mkdir -p "$DEST_DIR"
ln -sfn ../../../skills/summarize-workflow-suite/SKILL.md "$DEST_DIR/SKILL.md"
ln -sfn ../../../skills/summarize-workflow-suite/scripts "$DEST_DIR/scripts"

echo "Linked summarize-workflow-suite -> $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
