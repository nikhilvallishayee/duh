#!/usr/bin/env bash
# run.sh <agent-id>
# Single-run orchestrator for benchmark 2 (rate limiter).
# Clones a fresh worktree of the benchmark-2 baseline scaffold repo,
# invokes the agent's CLI non-interactively with TASK.md as the prompt,
# captures diff + session.log + meta.json under results/<agent-id>/.
set -euo pipefail

AGENT_ID="${1:?usage: run.sh <agent-id>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BASELINE_REPO="$HERE/baseline"
BASELINE_COMMIT="1b2bb9ad523de4880a75bce14f9c079b3fe182c3"
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

"$HERE/init_baseline.sh"
if [ -d "$WORKTREE" ]; then
  git -C "$BASELINE_REPO" worktree remove --force "$WORKTREE" 2>/dev/null || rm -rf "$WORKTREE"
fi
git -C "$BASELINE_REPO" worktree add --detach "$WORKTREE" "$BASELINE_COMMIT" >/dev/null
echo "[$AGENT_ID] worktree ready at $WORKTREE"

PROMPT="$(cat "$TASK_FILE")"
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date +%s)"
LOG="$OUT/session.log"
: > "$LOG"

set +e
case "$CLI" in
  claude)
    (
      cd "$WORKTREE"
      claude --print \
             --model="$MODEL" \
             --dangerously-skip-permissions \
             --allowedTools=Write,Edit,Read,Bash,Glob,Grep,Task \
             --add-dir="$WORKTREE" \
             --max-budget-usd=20 \
             "$PROMPT"
    ) >"$LOG" 2>&1
    ;;
  codex)
    (
      cd "$WORKTREE"
      codex exec --model "$MODEL" --skip-git-repo-check --full-auto "$PROMPT"
    ) >"$LOG" 2>&1
    ;;
  gemini)
    (
      cd "$WORKTREE"
      gemini -p "$PROMPT" --yolo --model "$MODEL"
    ) >"$LOG" 2>&1
    ;;
  duh)
    (
      cd "$WORKTREE"
      /Users/nomind/.local/bin/duh \
        --dangerously-skip-permissions \
        --model "$MODEL" \
        --max-cost 20 \
        -p "$PROMPT"
    ) >"$LOG" 2>&1
    ;;
esac
EXIT_CODE=$?
set -e

END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))

(
  cd "$WORKTREE"
  git add -N . >/dev/null 2>&1 || true
  git diff HEAD > "$OUT/diff.patch"
  git status --porcelain > "$OUT/files.txt"
)

cat > "$OUT/meta.json" <<EOF
{
  "agent_id": "$AGENT_ID",
  "cli": "$CLI",
  "model": "$MODEL",
  "baseline_commit": "$BASELINE_COMMIT",
  "benchmark": "benchmark-2-ratelimit",
  "start_ts": "$START_TS",
  "end_ts": "$END_TS",
  "elapsed_s": $ELAPSED,
  "exit_code": $EXIT_CODE,
  "worktree": "$WORKTREE",
  "log_bytes": $(wc -c < "$LOG" | tr -d ' '),
  "diff_bytes": $(wc -c < "$OUT/diff.patch" | tr -d ' '),
  "files_changed": $(wc -l < "$OUT/files.txt" | tr -d ' ')
}
EOF

echo "[$AGENT_ID] done  exit=$EXIT_CODE  elapsed=${ELAPSED}s  diff=$(wc -c < "$OUT/diff.patch" | tr -d ' ')B"
