#!/usr/bin/env bash
# Install the send-customer-response skill into this project's .claude/skills/.
# Run from anywhere: `bash skills/send-customer-response/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/send-customer-response"

mkdir -p "$DEST_DIR"
ln -sfn ../../../skills/send-customer-response/SKILL.md "$DEST_DIR/SKILL.md"
ln -sfn ../../../skills/send-customer-response/scripts "$DEST_DIR/scripts"

echo "Linked send-customer-response → $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
