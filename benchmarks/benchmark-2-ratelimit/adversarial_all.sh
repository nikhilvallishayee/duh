#!/usr/bin/env bash
# Run the hidden adversarial suite against each completed worktree.
# Writes results/<agent>/adversarial.json for every agent with a worktree.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV=/Users/nomind/Code/duh/.venv/bin/python

for wt in "$HERE"/worktrees/*/; do
  agent=$(basename "$wt")
  echo "=== adversarial $agent ==="
  cp "$HERE/adversarial/test_adversarial.py" "$wt/tests/test_zz_adversarial.py"
  (
    cd "$wt"
    # Install the agent's code editable so `import ratelimit` resolves.
    "$VENV" -m pip install -q -e . 2>&1 | tail -3 || true
    "$VENV" -m pytest tests/test_zz_adversarial.py --no-header -q --tb=line 2>&1 || true
  ) > "$HERE/results/$agent/adversarial.log" 2>&1
  if [ -f "$wt/adversarial.json" ]; then
    cp "$wt/adversarial.json" "$HERE/results/$agent/adversarial.json"
    echo "  → $(cat "$HERE/results/$agent/adversarial.json")"
  else
    echo '{"collected":0,"passed":0,"failed":0,"skipped":0,"pass_rate":0.0,"error":"no adversarial.json"}' \
      > "$HERE/results/$agent/adversarial.json"
    echo "  → no adversarial.json (agent code likely uncallable)"
  fi
  # Clean up copied test and build artifacts.
  rm -f "$wt/tests/test_zz_adversarial.py" "$wt/adversarial.json"
done
echo "adversarial runs complete"
