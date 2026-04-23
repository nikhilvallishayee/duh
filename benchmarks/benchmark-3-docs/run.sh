#!/usr/bin/env bash
# run.sh <agent-id>
# Benchmark 3: docs over D.U.H. source tree at pinned baseline.
# Creates a worktree of the D.U.H. repo, adds an empty docs-new/,
# invokes the CLI, captures diff scoped to docs-new/.
set -euo pipefail

AGENT_ID="${1:?usage: run.sh <agent-id>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DUH_REPO="/Users/nomind/Code/duh"
BASELINE_COMMIT="645d91ad10ad83b5778bcd14f2c53b8e3366497c"
WORKTREE="$HERE/worktrees/$AGENT_ID"
OUT="$HERE/results/$AGENT_ID"
TASK_FILE="$HERE/TASK.md"

mkdir -p "$OUT"

case "$AGENT_ID" in
  claude-code-opus)   CLI="claude"; MODEL="claude-opus-4-7" ;;
  duh-opus)           CLI="duh";    MODEL="claude-opus-4-7" ;;
  codex-gpt54)        CLI="codex";  MODEL="gpt-5.4" ;;
  duh-gpt54)          CLI="duh";    MODEL="gpt-5.4" ;;
  gemini-cli-3.1)     CLI="gemini"; MODEL="gemini-3.1-pro-preview" ;;
  duh-gemini-3.1)     CLI="duh";    MODEL="gemini/gemini-3.1-pro-preview" ;;
  *) echo "unknown agent id: $AGENT_ID" >&2; exit 2 ;;
esac

if [ -d "$WORKTREE" ]; then
  git -C "$DUH_REPO" worktree remove --force "$WORKTREE" 2>/dev/null || rm -rf "$WORKTREE"
fi
git -C "$DUH_REPO" worktree add --detach "$WORKTREE" "$BASELINE_COMMIT" >/dev/null
mkdir -p "$WORKTREE/docs-new"
# Placeholder so the directory is visible to agents doing initial `ls`.
echo "# docs-new — agent writes documentation here" > "$WORKTREE/docs-new/.gitkeep"
echo "[$AGENT_ID] worktree ready at $WORKTREE"

PROMPT="$(cat "$TASK_FILE")"
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date +%s)"
LOG="$OUT/session.log"
: > "$LOG"

set +e
case "$CLI" in
  claude)
    ( cd "$WORKTREE"
      claude --print --model="$MODEL" --dangerously-skip-permissions \
             --allowedTools=Write,Edit,Read,Bash,Glob,Grep,Task \
             --add-dir="$WORKTREE" --max-budget-usd=20 "$PROMPT"
    ) >"$LOG" 2>&1 ;;
  codex)
    ( cd "$WORKTREE"
      codex exec --model "$MODEL" --skip-git-repo-check --full-auto "$PROMPT"
    ) >"$LOG" 2>&1 ;;
  gemini)
    ( cd "$WORKTREE"
      gemini -p "$PROMPT" --yolo --model "$MODEL"
    ) >"$LOG" 2>&1 ;;
  duh)
    ( cd "$WORKTREE"
      /Users/nomind/.local/bin/duh --dangerously-skip-permissions \
        --model "$MODEL" --max-cost 20 -p "$PROMPT"
    ) >"$LOG" 2>&1 ;;
esac
EXIT_CODE=$?
set -e

END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))

(
  cd "$WORKTREE"
  git add -N docs-new/ >/dev/null 2>&1 || true
  # Diff scoped to docs-new/ only — we want to see what the agent produced,
  # not any out-of-scope edits (which would be a protocol violation).
  git diff HEAD -- docs-new/ > "$OUT/diff.patch"
  git status --porcelain > "$OUT/files.txt"
  # Flag any writes outside docs-new/ (protocol check).
  outside=$(git status --porcelain | awk '{print $2}' | grep -v '^docs-new/' | head -10 || true)
  if [ -n "$outside" ]; then
    echo "$outside" > "$OUT/protocol_violations.txt"
  fi
)

cat > "$OUT/meta.json" <<EOF
{
  "agent_id": "$AGENT_ID",
  "cli": "$CLI",
  "model": "$MODEL",
  "baseline_commit": "$BASELINE_COMMIT",
  "benchmark": "benchmark-3-docs",
  "start_ts": "$(date -u -r $START_EPOCH +%Y-%m-%dT%H:%M:%SZ)",
  "end_ts": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "elapsed_s": $ELAPSED,
  "exit_code": $EXIT_CODE,
  "worktree": "$WORKTREE",
  "log_bytes": $(wc -c < "$LOG" | tr -d ' '),
  "diff_bytes": $(wc -c < "$OUT/diff.patch" | tr -d ' '),
  "files_changed": $(wc -l < "$OUT/files.txt" | tr -d ' ')
}
EOF

echo "[$AGENT_ID] done  exit=$EXIT_CODE  elapsed=${ELAPSED}s  diff=$(wc -c < "$OUT/diff.patch" | tr -d ' ')B"
