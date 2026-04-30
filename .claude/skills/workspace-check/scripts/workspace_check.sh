#!/usr/bin/env bash
# workspace-check — verify the current cwd is the milestone02 project root.
#
# Prints a short report and a verdict: WORKSPACE OK / WRONG CWD / MARKER FILES MISSING.
# Exit code: 0 on WORKSPACE OK, 1 otherwise.

set -uo pipefail

cwd="$(pwd)"
cwd_name="$(basename "$cwd")"

green="\033[32m"; yellow="\033[33m"; red="\033[31m"; reset="\033[0m"
is_tty=0; [ -t 1 ] && is_tty=1
say_ok()   { if [ $is_tty -eq 1 ]; then printf "${green}OK${reset}: %s\n"   "$*"; else echo "OK: $*";   fi; }
say_warn() { if [ $is_tty -eq 1 ]; then printf "${yellow}WARN${reset}: %s\n" "$*"; else echo "WARN: $*"; fi; }
say_bad()  { if [ $is_tty -eq 1 ]; then printf "${red}FAIL${reset}: %s\n"   "$*"; else echo "FAIL: $*";  fi; }

echo "cwd: $cwd"
echo ""

# --- 1. cwd basename ---
name_ok=0
if [ "$cwd_name" = "milestone02" ]; then
  say_ok "cwd folder is named 'milestone02'"
  name_ok=1
else
  say_bad "cwd is '$cwd_name', not 'milestone02'"
fi

# --- 2. marker files ---
markers=(
  "pyproject.toml"
  "CLAUDE.md"
  ".claude/settings.json"
  "utils/connect.py"
  "data/examples"
  "skills/example"
)
missing=()
for m in "${markers[@]}"; do
  if [ -e "$m" ]; then
    say_ok "found $m"
  else
    say_bad "missing $m"
    missing+=("$m")
  fi
done

# --- 3. .env (non-fatal; might just not be populated yet) ---
if [ -f .env ]; then
  say_ok ".env present at project root"
elif [ -f .env.example ]; then
  say_warn ".env not yet created (but .env.example is here — cp .env.example .env)"
fi

# --- 4. look for the project folder elsewhere if we're in the wrong place ---
echo ""
if [ $name_ok -eq 0 ] || [ ${#missing[@]} -gt 0 ]; then
  # Is there a milestone02 folder one or two levels below us? If so, point at it.
  candidate=""
  for path in \
    "$cwd/milestone02" \
    "$cwd/assignments/genai-for-business/milestone02" \
    "$cwd/genai-for-business/milestone02" \
    ; do
    if [ -f "$path/pyproject.toml" ] && [ -f "$path/CLAUDE.md" ]; then
      candidate="$path"
      break
    fi
  done
  if [ -n "$candidate" ]; then
    echo "Suggested project root: $candidate"
  fi
fi

echo ""
if [ $name_ok -eq 1 ] && [ ${#missing[@]} -eq 0 ]; then
  echo "WORKSPACE OK — this is the milestone02 project root."
  exit 0
fi

if [ $name_ok -eq 0 ]; then
  echo "WRONG CWD — your IDE (or Claude Code session) is not rooted in milestone02."
  echo ""
  echo "Fix (pick whichever you use):"
  echo "  VS Code           : File → Open Folder → pick the milestone02 directory."
  echo "  VS Code terminal  : code <path-to>/milestone02"
  echo "  Claude Desktop    : Settings → Projects → Add Project → pick milestone02."
  echo "  Claude Code CLI   : cd <path-to>/milestone02 && claude"
  exit 1
fi

echo "MARKER FILES MISSING — the folder name is right, but these files aren't here:"
printf '  %s\n' "${missing[@]}"
echo ""
echo "This usually means the folder was partially cloned, moved, or renamed."
echo "Re-clone the assignment repo or restore the missing files from git."
exit 1
