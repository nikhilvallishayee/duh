#!/usr/bin/env bash
# Run the hidden adversarial suite against each completed worktree.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV=/Users/nomind/Code/duh/.venv/bin/python

"$VENV" -m pip install -q --disable-pip-version-check \
  redis fakeredis hypothesis pytest pytest-asyncio 2>&1 | tail -3 || true

for wt in "$HERE"/worktrees/*/; do
  agent=$(basename "$wt")
  out="$HERE/results/$agent"
  echo "=== adversarial $agent ==="
  cp "$HERE/adversarial/test_adversarial.py" "$wt/tests/test_zz_adversarial.py"
  (
    cd "$wt"
    PYTHONPATH="$wt" "$VENV" -m pytest tests/test_zz_adversarial.py \
      --no-header -q --tb=line -p no:cacheprovider 2>&1 || true
  ) > "$out/adversarial.log" 2>&1
  rm -f "$wt/tests/test_zz_adversarial.py"

  # Parse pytest summary line from the log. Expected formats:
  #   "5 passed in 0.10s"
  #   "1 failed, 4 passed in 0.15s"
  #   "3 failed, 2 passed, 1 skipped in 0.20s"
  log="$out/adversarial.log"
  passed=$(grep -oE '[0-9]+ passed' "$log" | tail -1 | grep -oE '^[0-9]+' || echo 0)
  failed=$(grep -oE '[0-9]+ failed' "$log" | tail -1 | grep -oE '^[0-9]+' || echo 0)
  skipped=$(grep -oE '[0-9]+ skipped' "$log" | tail -1 | grep -oE '^[0-9]+' || echo 0)
  errored=$(grep -oE '[0-9]+ error' "$log" | tail -1 | grep -oE '^[0-9]+' || echo 0)
  passed=${passed:-0}; failed=${failed:-0}; skipped=${skipped:-0}; errored=${errored:-0}
  attempted=$((passed + failed))
  if [ "$attempted" -gt 0 ]; then
    rate=$(python3 -c "print(f'{$passed / $attempted:.3f}')")
  else
    rate=0.000
  fi
  cat > "$out/adversarial.json" <<EOF
{
  "passed": $passed,
  "failed": $failed,
  "skipped": $skipped,
  "errored": $errored,
  "attempted": $attempted,
  "pass_rate": $rate
}
EOF
  printf "  → pass_rate=%-5s passed=%d failed=%d skipped=%d errored=%d\n" \
    "$(python3 -c "print(f'{$rate*100:.0f}%')")" "$passed" "$failed" "$skipped" "$errored"
done
echo "adversarial runs complete"
