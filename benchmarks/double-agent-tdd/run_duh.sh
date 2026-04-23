#!/usr/bin/env bash
# run_duh.sh — the three D.U.H. agents in parallel lanes.
# Run this AFTER the first-party lanes finish (or concurrently if
# rate limits permit). If any lane fails, fix D.U.H. and re-run this
# script — first-party results are preserved.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HERE/results"

"$HERE/run.sh" duh-opus         > "$HERE/results/lane-duh-opus.log"         2>&1 &
P1=$!
"$HERE/run.sh" duh-gpt54        > "$HERE/results/lane-duh-gpt54.log"        2>&1 &
P2=$!
"$HERE/run.sh" duh-gemini-3.1   > "$HERE/results/lane-duh-gemini-3.1.log"   2>&1 &
P3=$!

wait "$P1" && echo "duh-opus        DONE"  || echo "duh-opus        FAILED"
wait "$P2" && echo "duh-gpt54       DONE"  || echo "duh-gpt54       FAILED"
wait "$P3" && echo "duh-gemini-3.1  DONE"  || echo "duh-gemini-3.1  FAILED"

echo "D.U.H. stage complete."
