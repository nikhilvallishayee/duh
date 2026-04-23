#!/usr/bin/env bash
# judge_all.sh — three parallel judge lanes (one per judge model).
#
# Each judge is on a different provider, so they can run concurrently
# without rate-limit contention. Within a judge lane we serialise the 6
# targets.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

AGENTS=(claude-code-opus duh-opus codex-gpt54 duh-gpt54 gemini-cli-3.1 duh-gemini-3.1)

lane() {
  local judge="$1"
  for a in "${AGENTS[@]}"; do
    "$HERE/judge.sh" "$a" "$judge" || echo "[$judge ← $a] FAILED"
  done
}

mkdir -p "$HERE/results/judgments"

echo "Launching three judge lanes in parallel..."
lane j-opus  > "$HERE/results/judgments/lane-opus.log"  2>&1 &
P1=$!
lane j-gpt54 > "$HERE/results/judgments/lane-gpt54.log" 2>&1 &
P2=$!
lane j-g31   > "$HERE/results/judgments/lane-g31.log"   2>&1 &
P3=$!

wait "$P1"; echo "j-opus lane done"
wait "$P2"; echo "j-gpt54 lane done"
wait "$P3"; echo "j-g31 lane done"

echo "All 18 judgments complete."
