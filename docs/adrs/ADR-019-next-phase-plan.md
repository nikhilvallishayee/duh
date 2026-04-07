# ADR-019: Next Phase Plan

**Status**: Complete  
**Date**: 2026-04-07

## Context

D.U.H. v0.1.0 core is complete (27 commits, 863 tests, 18 ADRs). Next phase focuses on proving the harness works in real-world scenarios with ecosystem tools, SDK compatibility, and iterative design improvement.

## Phase 2 Priorities — COMPLETE

### P1: Refactor & Anti-Pattern Cleanup ✓
- Split cli/main.py (488L→31L) into parser.py, doctor.py, runner.py
- Fixed 2 hidden bugs: Config.load() and hook_registry.execute()
- Removed dead code, added debug logging to silent exception handlers
- Domain model clean: no duplication, clear intention

### P2: SDK Compatibility ✓
- `--output-format stream-json --input-format stream-json` implemented
- NDJSON bidirectional protocol: control_request/response, user messages
- NDJSON safety: U+2028/U+2029 escaping
- Claude Agent SDK e2e verified with Ollama backend

### P3: Interactive REPL ✓
- `duh` (no -p) enters interactive mode with readline
- 7 slash commands: /help, /model, /cost, /status, /clear, /compact, /exit
- Streaming text + tool indicators + error display

### P4: Additional Provider Adapters ✓
- OpenAI adapter: GPT-4o, o1, any OpenAI-compatible API via base_url
- Auto-detection: ANTHROPIC_API_KEY → OPENAI_API_KEY → Ollama fallback
- Streaming with tool call accumulation, message/tool format conversion

### P5: Production Hardening ✓
- SIGTERM signal handler for graceful shutdown
- KeyboardInterrupt cleanup in all async entry points
- --fallback-model CLI flag
- --permission-mode for SDK compatibility

## Integration Tests — COMPLETE

### T1: Playwright MCP Test ✓
- MCPExecutor.from_config parses Playwright server config correctly
- Config parsing, multi-server support, connection scaffolding tested
- 5 tests

### T2: Pre/Post Hook Test with Insights ✓
- Shell hooks fire on PreToolUse/PostToolUse events
- Hooks receive JSON on stdin, stdout captured in result
- Error isolation: failing hooks don't block other hooks
- Matcher filtering works end-to-end
- 13 tests

### T3: RuFlow Integration Test ✓
- D.U.H. invocable as subprocess in print mode and stream-json mode
- Multiple sequential invocations succeed (orchestration pattern)
- SDK shim validated as executable
- 5 tests

### T4: Claude Agent SDK Test ✓
- Claude Agent SDK (v0.1.56) launches D.U.H. via bin/duh-sdk-shim
- Initialize handshake, user message, AssistantMessage + ResultMessage parsed
- Verified end-to-end with Ollama (qwen2.5-coder:1.5b)

## Additional Deliverables

### Skill Format Parity ✓
- Loads from .claude/skills/ (Claude Code compat) alongside .duh/skills/
- Supports directory layout: skill-name/SKILL.md
- All Claude Code frontmatter fields: user-invocable, context, agent, effort, paths
- .duh/skills/ always wins by name (project owner intent)
- 33 skills loaded in universal-pattern-space (31 Claude + 2 D.U.H.)

## Metrics

- Tests: 863 → 954 (91 new tests)
- Commits: 10 commits in this phase
- New files: 11 (adapters/openai.py, cli/ndjson.py, cli/sdk_runner.py, cli/repl.py, etc.)
- Providers: 2 → 3 (+ OpenAI)

## Decision

Phase 2 complete. All P1-P5 priorities and T1-T4 integration tests delivered.
