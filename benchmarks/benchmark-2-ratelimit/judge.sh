#!/usr/bin/env bash
# judge.sh <agent-id> <judge-id>
#
# Invoke D.U.H. with the judge model to score the candidate's diff.
# Writes results/judgments/<judge-id>/<agent-id>.json.
set -euo pipefail

AGENT_ID="${1:?usage: judge.sh <agent-id> <judge-id>}"
JUDGE_ID="${2:?usage: judge.sh <agent-id> <judge-id>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$HERE/results/$AGENT_ID"
OUT_DIR="$HERE/results/judgments/$JUDGE_ID"
OUT_FILE="$OUT_DIR/$AGENT_ID.json"

if [ ! -f "$TARGET_DIR/diff.patch" ]; then
  echo "no diff.patch at $TARGET_DIR — run run.sh $AGENT_ID first" >&2
  exit 2
fi

case "$JUDGE_ID" in
  j-opus)   MODEL="claude-opus-4-7" ;;
  j-gpt54)  MODEL="gpt-5.4" ;;
  j-g31)    MODEL="gemini/gemini-3.1-pro-preview" ;;
  *) echo "unknown judge id: $JUDGE_ID" >&2; exit 2 ;;
esac

mkdir -p "$OUT_DIR"

# Compose the judge prompt: rubric + task + diff + files + meta + log tail.
# Size-cap the log/diff so we don't blow out the context window on big runs.
PROMPT_FILE="$(mktemp)"
trap "rm -f $PROMPT_FILE" EXIT
{
  cat "$HERE/JUDGE.md"
  echo
  echo "## Target agent id"
  echo
  echo "$AGENT_ID"
  echo
  echo "## TASK.md (what the candidate was asked to do)"
  echo
  cat "$HERE/TASK.md"
  echo
  echo "## files.txt"
  echo
  echo '```'
  cat "$TARGET_DIR/files.txt"
  echo '```'
  echo
  echo "## meta.json"
  echo
  echo '```json'
  cat "$TARGET_DIR/meta.json"
  echo '```'
  echo
  echo "## diff.patch (truncated to first ~200KB)"
  echo
  echo '```diff'
  head -c 200000 "$TARGET_DIR/diff.patch"
  echo '```'
  echo
  echo "## session.log (last ~80KB)"
  echo
  echo '```'
  tail -c 80000 "$TARGET_DIR/session.log" 2>/dev/null || true
  echo '```'
  if [ -f "$TARGET_DIR/adversarial.json" ]; then
    echo
    echo "## adversarial.json (hidden suite — drives dimension 4)"
    echo
    echo '```json'
    cat "$TARGET_DIR/adversarial.json"
    echo '```'
  fi
} > "$PROMPT_FILE"

# Run the judge. JSON-only output.
/Users/nomind/.local/bin/duh \
  --dangerously-skip-permissions \
  --model "$MODEL" \
  --output-format text \
  -p "$(cat "$PROMPT_FILE")" \
  > "$OUT_FILE.raw" 2>&1 || true

# Extract the first JSON object from the output (judges sometimes emit
# a trailing newline or a ```json fence despite our instructions).
python3 - "$OUT_FILE.raw" "$OUT_FILE" "$AGENT_ID" "$JUDGE_ID" <<'PY'
import json, re, sys, pathlib
raw_path, out_path, agent_id, judge_id = sys.argv[1:5]
text = pathlib.Path(raw_path).read_text(errors="replace")
# Strip common markdown fences.
text = re.sub(r"```(?:json)?", "", text)
# Grab the first {...} block by brace counting.
start = text.find("{")
if start == -1:
    payload = {"target": agent_id, "judge": judge_id, "error": "no JSON found",
               "raw_head": text[:400]}
    pathlib.Path(out_path).write_text(json.dumps(payload, indent=2))
    sys.exit(0)
depth, end = 0, -1
in_str, esc = False, False
for i in range(start, len(text)):
    c = text[i]
    if in_str:
        if esc: esc = False
        elif c == "\\": esc = True
        elif c == '"': in_str = False
        continue
    if c == '"':
        in_str = True; continue
    if c == "{": depth += 1
    elif c == "}":
        depth -= 1
        if depth == 0:
            end = i; break
if end == -1:
    payload = {"target": agent_id, "judge": judge_id,
               "error": "unterminated JSON",
               "raw_head": text[start:start+400]}
else:
    try:
        payload = json.loads(text[start:end+1])
    except Exception as e:
        payload = {"target": agent_id, "judge": judge_id,
                   "error": f"parse failed: {e}",
                   "raw_head": text[start:start+400]}
payload.setdefault("target", agent_id)
payload["judge"] = judge_id
pathlib.Path(out_path).write_text(json.dumps(payload, indent=2))
PY

rm -f "$OUT_FILE.raw"
echo "[$JUDGE_ID ← $AGENT_ID] -> $OUT_FILE"
