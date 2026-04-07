# ADR-019: Next Phase Plan

**Status**: Draft  
**Date**: 2026-04-07

## Context

D.U.H. v0.1.0 is feature-complete for the core harness (26 commits, 863 tests, 18 ADRs). The next phase focuses on ecosystem integration, SDK compatibility, and production hardening.

## Phase 2 Priorities

### P1: SDK Compatibility (Claude Agent SDK can use D.U.H.)
- Support `--output-format stream-json --input-format stream-json` for SDK consumers
- Match the NDJSON protocol that the Claude Agent SDK expects
- Test: `from anthropic import Claude; agent = Claude(cli_path="duh")`

### P2: Interactive REPL
- Build `duh` (no -p) interactive mode with readline
- Slash commands: /help, /model, /cost, /status, /clear, /compact, /exit
- Streaming text + tool indicators + status bar

### P3: Playwright MCP Integration
- Configure Playwright MCP server in settings
- `duh -p "open example.com and take a screenshot" --dangerously-skip-permissions`
- MCP executor connects to Playwright server at startup

### P4: Additional Providers
- OpenAI adapter (GPT-4o, o1 via openai SDK)
- litellm adapter (100+ models via one adapter)
- HuggingFace/transformers adapter (local inference)

### P5: Pre/Post Hooks with Insights
- Pre-tool hooks can modify tool input (linting, validation)
- Post-tool hooks can analyze output (metrics, learning)
- Insights injection: after N turns, inject learned patterns

### P6: CLI Refactor
- Split cli/main.py (494L) into cli/parser.py + cli/runner.py + cli/doctor.py
- Each under 200L, single responsibility

### P7: Production Hardening
- Retry with exponential backoff at the loop level
- Model fallback (sonnet → haiku on overload)
- Graceful shutdown (cleanup MCP, save session)
- Signal handling (SIGINT, SIGTERM)

## Architecture Notes

All new features follow the existing pattern:
1. Define a port (Protocol) if needed
2. Write the adapter
3. Wire into CLI via Deps injection
4. Add tests (target: 100% branch coverage)
5. Write ADR documenting the decision

## Decision

Execute in priority order. Each priority is a standalone commit with tests. No speculative features — build only what's needed now.
