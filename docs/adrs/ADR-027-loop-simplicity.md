# ADR-027: Loop Simplicity Refactor

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-07

## Context

Forensic audit found 2 bugs, 4 fidelity gaps, and 3 simplicity violations in the agentic loop against Kent Beck's 4 rules of simple design.

## Changes

1. **Extract helpers** (Rule 3 — no duplication): `_extract_tool_use_blocks()`, `_get_content()`, `_get_stop_reason()`, `_is_partial()`, `_to_message()` extracted from inline logic.

2. **Result truncation** (bug fix): `_truncate_result()` at 80K chars prevents context explosion from unbounded tool output. Applied in the loop before results are sent to the model.

3. **Stop reason tracking** (fidelity): `_get_stop_reason()` now extracts stop_reason for ALL exit paths, not just end_turn.

4. **Environment context** (fidelity): System prompt now includes `<environment>` block with cwd, platform, shell, and Python version — matches Claude Code's context injection.

5. **Dead parameter removal** (Rule 4 — fewest elements): Removed unused `max_tokens` parameter from `query()`.

## Files

- `duh/kernel/loop.py` — 6 extracted helpers, truncation, stop_reason
- `duh/cli/runner.py` — Environment context injection
