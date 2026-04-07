# ADR-019: Next Phase Plan

**Status**: Active  
**Date**: 2026-04-07

## Context

D.U.H. v0.1.0 core is complete (27 commits, 863 tests, 18 ADRs). Next phase focuses on proving the harness works in real-world scenarios with ecosystem tools, SDK compatibility, and iterative design improvement.

## Phase 2 Priorities

### P1: Refactor & Anti-Pattern Cleanup
- Split cli/main.py (494L) into cli/parser.py + cli/runner.py + cli/doctor.py
- Ensure domain model reveals intention, no duplication, fewest elements
- Run full anti-pattern audit, fix all findings
- Write missing tests for uncovered branches

### P2: SDK Compatibility
- Support `--output-format stream-json --input-format stream-json`
- Match the NDJSON protocol the Claude Agent SDK expects
- Test: Claude Agent SDK using D.U.H. as the CLI backend
- Test: PatternSpace app API integration

### P3: Interactive REPL
- `duh` (no -p) enters interactive mode with readline
- Slash commands: /help, /model, /cost, /status, /clear, /compact, /exit
- Streaming text + tool indicators + status bar

### P4: Additional Provider Adapters
- OpenAI adapter (GPT-4o, o1 via openai SDK)
- litellm adapter (100+ models via one adapter)
- HuggingFace/transformers adapter (local inference)

### P5: Production Hardening
- Retry with exponential backoff at the loop level
- Model fallback (sonnet → haiku on overload)
- Graceful shutdown (cleanup MCP, save session, signal handling)

## Integration Tests (prove the harness works)

These are TESTS, not features. They validate that D.U.H.'s existing MCP, hooks, and plugin systems work with real ecosystem tools:

### T1: Playwright MCP Test
- Configure Playwright MCP server in .duh/settings.json
- `duh -p "open example.com and screenshot" --dangerously-skip-permissions`
- Validates: MCP executor connects, discovers tools, executes browser actions

### T2: Pre/Post Hook Test with Insights
- Configure shell hooks that log tool usage to a file
- Run a multi-tool workflow
- Verify hooks fired, insights file written
- Validates: hook system works end-to-end with real shell commands

### T3: RuFlow Integration Test
- Run RuFlow (claude-flow) orchestration over D.U.H.
- Validates: D.U.H. can be orchestrated by external tools

### T4: Claude Agent SDK Test
- Use the Anthropic Python SDK's agent mode with `cli_path="duh"`
- Validates: stream-json protocol compatibility

## Design Iteration

After each priority, run the cycle:
1. Use D.U.H. to analyze its own codebase (dogfooding)
2. Identify friction points
3. Refactor based on insights
4. Update ADRs

## Decision

Execute P1-P5 in order. Run T1-T4 after P2 (SDK compat). Each priority is a standalone commit. No speculative features.
