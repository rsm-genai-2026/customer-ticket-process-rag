#!/usr/bin/env bash
# setup-doctor — diagnose a milestone02 Python environment.
#
# Prints one line per check: OK / WARN / FAIL with a suggested fix.
# Exits 0 on pure-OK, 1 if any FAIL was emitted. WARN does not fail.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Walk up to find the project root (the directory containing pyproject.toml).
find_project_root() {
  local d="$1"
  while [ "$d" != "/" ] && [ -n "$d" ]; do
    if [ -f "$d/pyproject.toml" ]; then
      echo "$d"
      return 0
    fi
    d="$(dirname "$d")"
  done
  return 1
}

PROJECT_ROOT="$(find_project_root "$SCRIPT_DIR" || find_project_root "$PWD" || echo "")"
if [ -z "$PROJECT_ROOT" ]; then
  echo "FAIL: could not locate pyproject.toml walking up from $PWD"
  echo "  → fix: cd into the milestone02 folder and re-run"
  exit 1
fi

cd "$PROJECT_ROOT"

failures=0
green="\033[32m"; yellow="\033[33m"; red="\033[31m"; reset="\033[0m"
is_tty=0
[ -t 1 ] && is_tty=1

say_ok()   { if [ $is_tty -eq 1 ]; then printf "${green}OK${reset}: %s\n"   "$*"; else echo "OK: $*";   fi; }
say_warn() { if [ $is_tty -eq 1 ]; then printf "${yellow}WARN${reset}: %s\n" "$*"; else echo "WARN: $*"; fi; }
say_fail() { if [ $is_tty -eq 1 ]; then printf "${red}FAIL${reset}: %s\n"   "$*"; else echo "FAIL: $*"; fi; failures=$((failures+1)); }

echo "project root: $PROJECT_ROOT"
echo ""

# --- uv ---
if command -v uv >/dev/null 2>&1; then
  uv_ver=$(uv --version 2>/dev/null | head -1)
  say_ok "uv installed — $uv_ver"
else
  say_fail "uv not on PATH"
  echo "  → fix: install uv — https://docs.astral.sh/uv/getting-started/installation/"
  echo ""
  echo "Cannot continue without uv. Aborting further checks."
  exit 1
fi

# --- pyproject + lock ---
if [ -f pyproject.toml ]; then
  say_ok "pyproject.toml present"
else
  say_fail "pyproject.toml missing"
  echo "  → fix: open the milestone02 folder as your project root (see workspace-check skill)"
fi
if [ -f uv.lock ]; then
  say_ok "uv.lock present"
else
  say_warn "uv.lock missing — next 'uv sync' will create it"
fi

# --- Python version (prefer .venv python, fall back to uv python find) ---
venv_py=""
if   [ -x .venv/bin/python ];       then venv_py=".venv/bin/python"
elif [ -x .venv/Scripts/python.exe ]; then venv_py=".venv/Scripts/python.exe"
fi

if [ -n "$venv_py" ]; then
  ver="$($venv_py -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
  case "$ver" in
    3.12|3.13|3.14|3.15)  say_ok ".venv Python version = $ver" ;;
    *)                    say_fail ".venv Python version = $ver (need >= 3.12)"
                          echo "  → fix: rm -rf .venv && uv python install 3.12 && uv sync" ;;
  esac
else
  say_warn ".venv not created yet"
  echo "  → fix: uv sync"
fi

# --- critical imports ---
if [ -n "$venv_py" ]; then
  import_check=$("$venv_py" - <<'PY' 2>&1
import importlib, sys
mods = ["numpy", "polars", "plotnine", "openai", "pyarrow", "pydantic", "dotenv", "requests"]
missing = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        missing.append(f"{m} ({e.__class__.__name__})")
if missing:
    print("MISSING:" + ",".join(missing))
else:
    print("ALL_OK")
PY
)
  if [[ "$import_check" == ALL_OK ]]; then
    say_ok "all critical packages importable (numpy, polars, plotnine, openai, pyarrow, pydantic, dotenv, requests)"
  elif [[ "$import_check" == MISSING:* ]]; then
    say_fail "missing packages: ${import_check#MISSING:}"
    echo "  → fix: uv sync"
  else
    say_warn "could not run import check: $import_check"
  fi
fi

# --- .env & TRITONAI_API_KEY ---
if [ -f .env ]; then
  say_ok ".env file present"
  key_line=$(grep -E '^[[:space:]]*TRITONAI_API_KEY[[:space:]]*=' .env | head -1 || true)
  if [ -z "$key_line" ]; then
    say_fail "TRITONAI_API_KEY not set in .env"
    echo "  → fix: add a line  TRITONAI_API_KEY=<your key>  (see .env.example and the course handout)"
  else
    # Strip any quotes and check non-empty
    val="${key_line#*=}"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    val="$(echo "$val" | tr -d '[:space:]')"
    if [ -z "$val" ] || [ "$val" = "your-api-key-here" ] || [ "$val" = "changeme" ]; then
      say_fail "TRITONAI_API_KEY is set but looks empty or placeholder"
      echo "  → fix: paste the real key from the course handout into .env"
    else
      say_ok "TRITONAI_API_KEY populated (length=${#val})"
    fi
  fi
else
  say_fail ".env file missing"
  echo "  → fix: cp .env.example .env   (then paste TRITONAI_API_KEY from the course handout)"
fi

# --- pytest collection (fast sanity check) ---
if [ -n "$venv_py" ] && [ -d tests ]; then
  # --collect-only is fast and doesn't execute tests
  if collect_out=$("$venv_py" -m pytest --collect-only -q 2>&1); then
    n_line=$(echo "$collect_out" | grep -E 'tests? collected' | head -1)
    [ -z "$n_line" ] && n_line="collection complete"
    say_ok "pytest collects cleanly — $n_line"
  else
    say_warn "pytest --collect-only returned non-zero — tests may import-fail"
    echo "$collect_out" | tail -10 | sed 's/^/    /'
    echo "  → fix: uv sync --dev   (and re-run the doctor)"
  fi
fi

echo ""
if [ "$failures" -eq 0 ]; then
  echo "all good — environment looks healthy."
  exit 0
else
  echo "$failures check(s) failed. Address the fixes above, then re-run:"
  echo "    bash .claude/skills/setup-doctor/scripts/doctor.sh"
  exit 1
fi
