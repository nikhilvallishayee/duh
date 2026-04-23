#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV=/Users/nomind/Code/duh/.venv/bin/python
for wt in "$HERE"/worktrees/*/; do
  agent=$(basename "$wt")
  echo "=== consistency $agent ==="
  "$VENV" "$HERE/consistency/check.py" "$wt" "$HERE/results/$agent/consistency.json"
  "$VENV" -c "import json; d=json.load(open('$HERE/results/$agent/consistency.json')); print(f'  pass_rate={d.get(\"pass_rate\", 0):.0%}  sym={d.get(\"symbol_existence\",{}).get(\"rate\",0):.0%}  sig={d.get(\"signature_match\",{}).get(\"rate\",0):.0%}  cov={d.get(\"coverage\",{}).get(\"rate\",0):.0%}')"
done
echo "consistency runs complete"
