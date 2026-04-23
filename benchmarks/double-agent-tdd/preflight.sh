#!/usr/bin/env bash
# preflight.sh — verify everything needed for a full run.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DUH_REPO="/Users/nomind/Code/duh"
BASELINE="645d91ad10ad83b5778bcd14f2c53b8e3366497c"

fail=0

check() {
  local name="$1" cond="$2"
  if eval "$cond" >/dev/null 2>&1; then
    echo "  OK    $name"
  else
    echo "  FAIL  $name"
    fail=1
  fi
}

echo "CLIs:"
check "claude"   "command -v claude"
check "codex"    "command -v codex"
check "gemini"   "command -v gemini"
check "duh"      "test -x /Users/nomind/.local/bin/duh"

echo "API keys:"
check "ANTHROPIC_API_KEY" 'test -n "${ANTHROPIC_API_KEY:-}"'
check "OPENAI_API_KEY"    'test -n "${OPENAI_API_KEY:-}"'
check "GEMINI_API_KEY"    'test -n "${GEMINI_API_KEY:-}"'

echo "D.U.H. repo:"
check "repo exists"     "test -d $DUH_REPO/.git"
check "baseline commit" "git -C $DUH_REPO cat-file -e $BASELINE^{commit}"

echo "Harness files:"
for f in TASK.md JUDGE.md run.sh judge.sh aggregate.py; do
  check "$f" "test -f $HERE/$f"
done

if [ "$fail" -ne 0 ]; then
  echo
  echo "PREFLIGHT FAILED"
  exit 1
fi
echo
echo "PREFLIGHT OK — ready to run."
