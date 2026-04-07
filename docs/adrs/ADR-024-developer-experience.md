# ADR-024: Developer Experience — REPL, TUI, Undo, Templates

**Status**: Implemented  
**Date**: 2026-04-07

## Decision

D.U.H. provides a Rich-enhanced interactive REPL with 17 slash commands, file undo, prompt templates, brief mode, conversation search, readline history with tab completion, and context window dashboard.

## REPL Commands

/help, /model, /cost, /status, /context, /changes, /git, /tasks, /brief, /search, /template, /plan, /undo, /jobs, /pr, /health, /exit

## Key Features

- Rich markdown rendering with fallback to plain ANSI
- UndoStack (20 entries) for Write/Edit rollback
- Prompt templates from `.duh/templates/` with `$PROMPT` substitution
- `--brief` flag for concise responses
- Persistent readline history at `~/.config/duh/repl_history`
- Tab completion for /slash commands

## Files

- `duh/cli/repl.py` — REPL with Rich renderer
- `duh/kernel/undo.py` — Undo stack
- `duh/kernel/templates.py` — Template loading
- `duh/kernel/plan_mode.py` — Two-phase planning
