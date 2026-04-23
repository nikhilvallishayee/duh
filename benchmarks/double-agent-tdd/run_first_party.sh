#!/usr/bin/env bash
# run_first_party.sh — the three non-D.U.H. agents in parallel lanes.
# These land once; D.U.H. runs are separate so bugs in D.U.H. can be
# fixed and rerun without wasting first-party runs.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HERE/results"

"$HERE/run.sh" claude-code-opus  > "$HERE/results/lane-claude-code-opus.log"   2>&1 &
P1=$!
"$HERE/run.sh" codex-gpt54       > "$HERE/results/lane-codex-gpt54.log"        2>&1 &
P2=$!
"$HERE/run.sh" gemini-cli-3.1    > "$HERE/results/lane-gemini-cli-3.1.log"     2>&1 &
P3=$!

wait "$P1" && echo "claude-code-opus  DONE"  || echo "claude-code-opus  FAILED"
wait "$P2" && echo "codex-gpt54       DONE"  || echo "codex-gpt54       FAILED"
wait "$P3" && echo "gemini-cli-3.1    DONE"  || echo "gemini-cli-3.1    FAILED"

echo "first-party stage complete."
