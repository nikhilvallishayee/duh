#!/usr/bin/env bash
# run.sh <agent-id>
#
# Single-run orchestrator. Creates a fresh git worktree of D.U.H. at the
# pinned baseline commit, invokes the agent's CLI non-interactively with
# TASK.md as the prompt, captures diff + session.log + meta.json under
# results/<agent-id>/.
#
# Requires env: ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY.
# No OAuth. Every CLI is forced into API-key mode so costs are comparable.
set -euo pipefail

AGENT_ID="${1:?usage: run.sh <agent-id>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DUH_REPO="/Users/nomind/Code/duh"
BASELINE_COMMIT="645d91ad10ad83b5778bcd14f2c53b8e3366497c"
WORKTREE="$HERE/worktrees/$AGENT_ID"
OUT="$HERE/results/$AGENT_ID"
TASK_FILE="$HERE/TASK.md"

mkdir -p "$OUT"

# ---- Pick CLI + model + invocation per agent id -----------------------
case "$AGENT_ID" in
  claude-code-opus)
    CLI="claude"
    MODEL="claude-opus-4-7"
    ;;
  duh-opus)
    CLI="duh"
    MODEL="claude-opus-4-7"
    ;;
  codex-gpt54)
    CLI="codex"
    MODEL="gpt-5.4"
    ;;
  duh-gpt54)
    CLI="duh"
    MODEL="gpt-5.4"
    ;;
  gemini-cli-3.1)
    CLI="gemini"
    MODEL="gemini-3.1-pro-preview"
    ;;
  duh-gemini-3.1)
    CLI="duh"
    MODEL="gemini/gemini-3.1-pro-preview"
    ;;
  duh-gpt-oss-120b)
    CLI="duh"
    MODEL="groq/openai/gpt-oss-120b"
    ;;
  duh-qwen3-32b)
    CLI="duh"
    MODEL="groq/qwen/qwen3-32b"
    ;;
  duh-llama4-scout)
    CLI="duh"
    MODEL="groq/meta-llama/llama-4-scout-17b-16e-instruct"
    ;;
  duh-deepseek-v4-pro)
    CLI="duh"
    MODEL="openrouter/deepseek/deepseek-v4-pro"
    ;;
  duh-llama4-maverick)
    CLI="duh"
    MODEL="openrouter/meta-llama/llama-4-maverick"
    ;;
  duh-qwen3-max)
    CLI="duh"
    MODEL="openrouter/qwen/qwen3-max-thinking"
    ;;
  duh-mistral-large)
    CLI="duh"
    MODEL="openrouter/mistralai/mistral-large-2512"
    ;;
  *)
    echo "unknown agent id: $AGENT_ID" >&2
    exit 2
    ;;
esac

# ---- Fresh worktree at baseline ---------------------------------------
if [ -d "$WORKTREE" ]; then
  echo "[$AGENT_ID] removing existing worktree"
  git -C "$DUH_REPO" worktree remove --force "$WORKTREE" 2>/dev/null || rm -rf "$WORKTREE"
fi
git -C "$DUH_REPO" worktree add --detach "$WORKTREE" "$BASELINE_COMMIT" >/dev/null
echo "[$AGENT_ID] worktree ready at $WORKTREE"

# ---- Compose the prompt -----------------------------------------------
PROMPT="$(cat "$TASK_FILE")"

# ---- Run the CLI non-interactively ------------------------------------
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date +%s)"
LOG="$OUT/session.log"
: > "$LOG"

set +e
case "$CLI" in
  claude)
    (
      cd "$WORKTREE"
      # --print: non-interactive.  --dangerously-skip-permissions allows
      # edits without prompting.  --allowedTools explicitly grants the
      # agentic toolset.  ANTHROPIC_API_KEY is honoured by --print when
      # set.  --add-dir confines the sandbox explicitly to the worktree
      # so Bash(cd /Users/...) and similar can't escape — the TASK used
      # to mention a hard-coded path and the agent would oblige.
      claude --print \
             --model="$MODEL" \
             --dangerously-skip-permissions \
             --allowedTools=Write,Edit,Read,Bash,Glob,Grep,Task \
             --add-dir="$WORKTREE" \
             --max-budget-usd=15 \
             "$PROMPT"
    ) >"$LOG" 2>&1
    ;;
  codex)
    (
      cd "$WORKTREE"
      # codex exec is the non-interactive subcommand. --skip-git-repo-check
      # and --full-auto let it run without prompting for confirmations.
      codex exec \
            --model "$MODEL" \
            --skip-git-repo-check \
            --full-auto \
            "$PROMPT"
    ) >"$LOG" 2>&1
    ;;
  gemini)
    (
      cd "$WORKTREE"
      # -p non-interactive, --yolo auto-accept, --model explicit.
      gemini -p "$PROMPT" --yolo --model "$MODEL"
    ) >"$LOG" 2>&1
    ;;
  duh)
    (
      cd "$WORKTREE"
      /Users/nomind/.local/bin/duh \
        --dangerously-skip-permissions \
        --model "$MODEL" \
        --max-cost 15 \
        -p "$PROMPT"
    ) >"$LOG" 2>&1
    ;;
esac
EXIT_CODE=$?
set -e

END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))

# ---- Capture diff + files list ----------------------------------------
(
  cd "$WORKTREE"
  # Untracked files show up in `git status` but not `git diff`; include them.
  git add -N . >/dev/null 2>&1 || true
  git diff HEAD > "$OUT/diff.patch"
  git status --porcelain > "$OUT/files.txt"
)

# ---- Meta --------------------------------------------------------------
cat > "$OUT/meta.json" <<EOF
{
  "agent_id": "$AGENT_ID",
  "cli": "$CLI",
  "model": "$MODEL",
  "baseline_commit": "$BASELINE_COMMIT",
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
