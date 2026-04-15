# ADR-062: Output Styles and Streaming Modes

**Status:** Proposed — 2026-04-16
**Date:** 2026-04-16
**Related:** ADR-008 (CLI design), ADR-011 (TUI architecture)

## Context

Leading agent CLIs support multiple output styles that control how responses are rendered:
- **Default** — Rich markdown with tool panels
- **Concise** — Shorter responses, less verbose tool output
- **Verbose** — Full tool output, thinking blocks visible
- **JSON** — Structured events for SDK/pipe mode
- **Stream-JSON** — NDJSON streaming events

D.U.H. has `--output-format text|json|stream-json` for SDK mode, and the TUI has a single fixed style. Missing: user-configurable output verbosity and style.

## Decision

### Output Styles

| Style | Tool Output | Thinking | Markdown | Use Case |
|-------|-----------|----------|----------|----------|
| `default` | First line + OK/Error | Hidden | Full render | Interactive REPL/TUI |
| `concise` | Hidden (just status) | Hidden | Minimal | Fast iteration |
| `verbose` | Full output (scrollable) | Shown | Full render | Debugging |
| `json` | Structured events | Included | Raw text | SDK integration |
| `stream-json` | NDJSON events | Included | Raw text | Pipe/streaming |

### Configuration

```bash
duh --output-style concise
duh --output-style verbose --tui
```

Or in DUH.md / CLAUDE.md:
```
output_style: concise
```

Or toggle at runtime:
```
/style verbose
/style concise
```

### Implementation

Add an `OutputStyle` enum and wire it into renderers:
- REPL: Rich renderer respects style
- TUI: Widget rendering respects style (tool panels collapse in concise mode)
- SDK: Already has json/stream-json, add concise text mode

## Consequences

### Positive
- User controls verbosity without losing information
- Concise mode for experienced users who just want results
- Verbose mode for debugging complex tool chains
- Follows industry best practice for output style systems

### Negative
- Another configuration knob to maintain
- Each renderer must handle all styles
- Style changes mid-session affect display consistency
