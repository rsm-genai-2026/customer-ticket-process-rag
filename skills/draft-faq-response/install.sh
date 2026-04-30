#!/usr/bin/env bash
# Install the draft-faq-response skill into this project's .claude/skills/.
# Run from anywhere: `bash skills/draft-faq-response/install.sh`
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
DEST_DIR="$REPO_ROOT/.claude/skills/draft-faq-response"

mkdir -p "$DEST_DIR"
ln -sfn ../../../skills/draft-faq-response/SKILL.md "$DEST_DIR/SKILL.md"
ln -sfn ../../../skills/draft-faq-response/scripts "$DEST_DIR/scripts"

echo "Linked draft-faq-response → $DEST_DIR"
echo "Restart Claude Code (or start a new session) to activate the skill."
