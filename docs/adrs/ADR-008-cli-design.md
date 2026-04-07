# ADR-008: CLI Design

**Status**: Accepted  
**Date**: 2026-04-07

## Context

Claude Code's CLI (`main.tsx`) is a 500+ line React/Ink application with Commander.js argument parsing, OAuth flows, MDM settings, GrowthBook feature flags, keychain prefetching, and dozens of startup optimizations. The entry point alone imports 80+ modules. The CLI supports interactive REPL mode, print mode (`-p`), API mode (`--json`), coordinator mode, assistant mode, and remote mode.

D.U.H. needs a CLI that provides the core workflows without the complexity.

### Legacy Behavior (Claude Code)

Key patterns from `tengu-legacy/src/main.tsx`:

1. **Provider auto-detection**: Checks `ANTHROPIC_API_KEY` environment variable, falls back to OAuth, then to Bedrock/Vertex credentials. No explicit `--provider` flag needed.

2. **Flag conventions**:
   - `-p "prompt"` — print mode (non-interactive, outputs to stdout)
   - `--model <name>` — model override
   - `--output-format json` — JSON output (stream-json for streaming)
   - `--dangerously-skip-permissions` — auto-approve all tools
   - `--continue` — resume last session
   - `--resume <id>` — resume specific session
   - `--max-turns <n>` — agentic loop limit
   - `--system-prompt <text>` — system prompt override

3. **Output conventions**:
   - Text output to stdout (for piping)
   - Human messages, tool traces, errors to stderr
   - `--output-format json` for machine consumption
   - Exit code 0 on success, 1 on error

4. **Error UX**: Errors are human-readable with actionable hints. "Credit balance is too low" → "Go to console.anthropic.com → Plans & Billing to add credits." Not raw stack traces.

5. **Doctor subcommand**: `claude doctor` runs diagnostics (Python version, API key, config, tools).

## Decision

Implement a minimal CLI using `argparse` (stdlib, no deps) with the same flag conventions where they make sense.

### Modes

| Mode | Invocation | Output |
|------|-----------|--------|
| Print | `duh -p "prompt"` | Streaming text to stdout |
| Help | `duh` (no args) | Usage help |
| Doctor | `duh doctor` | Diagnostic checks |
| JSON | `duh -p "prompt" --output-format json` | JSON array of events |

Interactive REPL mode is future work — print mode covers the scripting/automation use case first.

### Flag Conventions

Matching Claude Code where possible:

| Flag | Short | Description | CC equivalent |
|------|-------|-------------|---------------|
| `--prompt` | `-p` | Print mode prompt | `-p` |
| `--model` | | Model override | `--model` |
| `--provider` | | Provider (anthropic/ollama) | Auto-detect |
| `--output-format` | | text or json | `--output-format` |
| `--max-turns` | | Agentic loop limit | `--max-turns` |
| `--system-prompt` | | System prompt override | `--system-prompt` |
| `--dangerously-skip-permissions` | | Auto-approve all | Same |
| `--debug` | `-d` | Full event tracing | `--verbose` |
| `--version` | | Show version | `--version` |

### Provider Auto-Detection

```
1. --provider flag (explicit)
2. ANTHROPIC_API_KEY set → anthropic
3. Ollama reachable at localhost:11434 → ollama
4. Error with actionable message
```

### Output Format

- **text** (default): Streaming text to stdout. Tool calls and errors to stderr with ANSI color.
- **json**: Array of all events as JSON to stdout after completion.

### Error UX

Every error includes what went wrong AND what to do about it:

```python
_ERROR_HINTS = {
    "credit balance is too low": "Go to console.anthropic.com → Plans & Billing...",
    "invalid x-api-key": "Check ANTHROPIC_API_KEY is set correctly.",
    "rate_limit": "Wait a moment and try again.",
    "overloaded": "API is overloaded. Try again or use --model claude-haiku-4-5...",
    "prompt is too long": "Conversation too long. Try a shorter prompt...",
    "Could not resolve authentication": "No API key found. Set ANTHROPIC_API_KEY...",
}
```

### Debug Mode

`--debug` / `-d` enables:
- Full event tracing to stderr (every event type and payload summary)
- Thinking block output (dimmed italic)
- Tool result content (truncated)
- Provider and model identification at startup

### Doctor

`duh doctor` checks:
- Python version (>= 3.12)
- ANTHROPIC_API_KEY presence
- Config directory
- anthropic SDK installed
- Available tools

### Future Enhancements

- **REPL mode**: Interactive chat loop (no `-p`)
- **`--continue` / `--resume`**: Session resumption
- **`--output-format stream-json`**: NDJSON streaming (one event per line)
- **Subcommands**: `duh init`, `duh config`, `duh mcp`
- **Shell completion**: bash/zsh/fish via argparse hooks

## Consequences

- Zero external CLI dependencies (stdlib argparse)
- Matches Claude Code flag conventions where they make sense
- Errors guide users to solutions, not stack traces
- Provider auto-detection works out of the box (set key → it works)
- Debug mode gives full visibility without cluttering normal output
- Doctor subcommand provides instant diagnostics
