#!/usr/bin/env bash
# Idempotently initialise the baseline as a git repo with one commit.
# run.sh calls this if needed, so users don't have to.
set -euo pipefail
BASELINE="$(cd "$(dirname "$0")/baseline" && pwd)"
if [ -d "$BASELINE/.git" ]; then
  exit 0
fi
cd "$BASELINE"
git init -q -b main
git add .
# Pinned author + date for reproducible commit SHA across machines.
GIT_COMMITTER_DATE="2026-04-24T00:00:00Z" \
GIT_AUTHOR_DATE="2026-04-24T00:00:00Z" \
git -c user.email="bench@duh" -c user.name="Benchmark Scaffold" \
  commit -q -m "Empty rate-limiter scaffold (benchmark 2 baseline)"
