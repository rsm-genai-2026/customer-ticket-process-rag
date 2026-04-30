#!/usr/bin/env bash
# Install the receive-ticket skill into this project's .claude/skills/ so Claude Code picks it up.
# Run from anywhere: `bash skills/receive-ticket/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/receive-ticket"

mkdir -p "$DEST_DIR"
# Relative symlinks so the repo stays portable across machines.
# .claude/skills/<name>/{SKILL.md,scripts} → ../../../skills/<name>/{SKILL.md,scripts}
ln -sfn ../../../skills/receive-ticket/SKILL.md "$DEST_DIR/SKILL.md"
ln -sfn ../../../skills/receive-ticket/scripts "$DEST_DIR/scripts"

echo "Linked receive-ticket → $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
