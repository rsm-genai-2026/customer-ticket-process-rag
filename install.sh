#!/usr/bin/env bash
# Install every skill in this repo into the project's .claude/skills/ folder
# so Claude Code (and other Anthropic-compatible agents) can load them.
#
# Auto-discovers any folder under skills/ that contains a SKILL.md file.
# For each one, creates a relative symlink at:
#
#   .claude/skills/<name>/SKILL.md   ->  ../../../skills/<name>/SKILL.md
#   .claude/skills/<name>/scripts    ->  ../../../skills/<name>/scripts
#
# Run from anywhere:
#
#   bash install.sh
#
# Re-run is safe — `ln -sfn` replaces existing symlinks in place.
# After install, restart Claude Code (or start a new session) to pick up
# the registered skills.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$REPO_ROOT/skills"
DEST_PARENT="$REPO_ROOT/.claude/skills"

if [ ! -d "$SKILLS_DIR" ]; then
  echo "error: $SKILLS_DIR does not exist." >&2
  exit 1
fi

mkdir -p "$DEST_PARENT"

installed=0
for skill_md in "$SKILLS_DIR"/*/SKILL.md; do
  [ -e "$skill_md" ] || continue   # nothing matched if the glob didn't expand
  skill_dir="$(dirname "$skill_md")"
  name="$(basename "$skill_dir")"
  dest="$DEST_PARENT/$name"
  mkdir -p "$dest"
  ln -sfn "../../../skills/$name/SKILL.md" "$dest/SKILL.md"
  ln -sfn "../../../skills/$name/scripts"  "$dest/scripts"
  echo "  linked $name"
  installed=$((installed + 1))
done

if [ "$installed" -eq 0 ]; then
  echo "warning: no skills found under $SKILLS_DIR (any folder with a SKILL.md)" >&2
  exit 1
fi

echo
echo "Installed $installed skill(s) into $DEST_PARENT"
echo "Restart Claude Code (or start a new session) to activate them."
