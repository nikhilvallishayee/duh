# ADR-023: Safety Hardening — Bash Security, Permissions, Output Limits

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-07

## Decision

Extend ADR-005's 3-layer safety with: 69 bash command patterns (26 dangerous + 10 moderate + 18 PS dangerous + 7 PS moderate + 8 additional), file permission validation before execution, 100KB output truncation per tool, and per-tool configurable timeouts.

## Additions

- `bash_security.py` — `classify_command()` with risk levels (safe/moderate/dangerous)
- PowerShell patterns for Windows cross-platform safety
- Upfront `os.access()` checks in Read/Write/Edit/MultiEdit
- `MAX_TOOL_OUTPUT = 100_000` enforced in NativeExecutor
- `TOOL_TIMEOUTS` dict with per-tool defaults (Bash: 300s, Read: 30s, etc.)

## Files

- `duh/tools/bash_security.py`
- `duh/tools/bash.py` — Cross-platform shell detection
- `duh/kernel/tool.py` — Timeout configuration
- `duh/adapters/native_executor.py` — Output truncation + timeout enforcement
