#!/usr/bin/env bash
# Install the audit-ticket-process skill into this project's .claude/skills/.
# Run from anywhere: `bash skills/audit-ticket-process/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/audit-ticket-process"

mkdir -p "$DEST_DIR"
cp "$SRC_DIR/SKILL.md" "$DEST_DIR/SKILL.md"
cp -r "$SRC_DIR/scripts" "$DEST_DIR/scripts"

echo "Installed audit-ticket-process → $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
