# ADR-022: Token Counting, Cost Control & Auto-Compaction

**Status**: Implemented  
**Date**: 2026-04-07

## Decision

D.U.H. estimates tokens (~4 chars/token), tracks cost per provider, enforces budget limits (`--max-cost`), and auto-compacts at 80% of model context window.

## Features

- `count_tokens()` / `estimate_cost()` / `get_context_limit()`
- `--max-cost` flag with 80% warning and 100% hard stop
- Auto-compaction triggers before each turn when context exceeds threshold
- Smart deduplication removes redundant file reads before compaction
- `/cost` REPL command shows estimated spend and budget remaining

## Files

- `duh/kernel/tokens.py` — Estimation and pricing
- `duh/kernel/engine.py` — Budget enforcement and auto-compact
- `duh/adapters/simple_compactor.py` — Tail-window + summarize strategy
