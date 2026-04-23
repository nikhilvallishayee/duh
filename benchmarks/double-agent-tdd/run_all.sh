#!/usr/bin/env bash
# run_all.sh — preflight → first-party → D.U.H.
# Stages are separate so D.U.H. bugs can be fixed and rerun
# without losing first-party results.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

"$HERE/preflight.sh"
echo
echo "========== stage 1: first-party CLIs =========="
"$HERE/run_first_party.sh"
echo
echo "========== stage 2: D.U.H. =========="
"$HERE/run_duh.sh"
echo
echo "All six runs complete. Launch judgments with ./judge_all.sh"
