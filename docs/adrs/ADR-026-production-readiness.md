# ADR-026: Production Readiness — Logging, Health, CI, PyPI

**Status**: Implemented  
**Date**: 2026-04-07

## Decision

D.U.H. includes production infrastructure: structured JSONL logging, provider health checks, GitHub Actions CI, and PyPI packaging.

## Features

- **Structured logging** (`--log-json`): JSONL to `~/.config/duh/logs/duh.jsonl` with 10MB rotation
- **Health checks**: Provider connectivity with latency, MCP server status, `/health` REPL command
- **CI**: GitHub Actions on push/PR, Python 3.12, `--ignore=tests/integration`
- **PyPI**: `pip install duh-cli` / `pip install 'duh-cli[all]'`
- **Exponential backoff**: `with_backoff()` wrapper for Anthropic and OpenAI streaming
- **Fallback model**: `--fallback-model` auto-switches on overload/rate-limit
- **Session auto-save**: Engine persists messages after every turn

## Files

- `duh/adapters/structured_logging.py`
- `duh/kernel/health_check.py`
- `duh/kernel/backoff.py`
- `.github/workflows/ci.yml` / `publish.yml`
- `pyproject.toml`
