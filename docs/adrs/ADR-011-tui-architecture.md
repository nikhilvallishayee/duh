# ADR-011: TUI Architecture

**Status**: Accepted  
**Date**: 2026-04-06

## Context

Claude Code has a full Ink/Yoga-based terminal UI engine: a React-like component tree, a Yoga layout engine for flexbox in the terminal, a custom renderer that diffs virtual screens and blits only changed cells, scroll boxes, focus management, alt-screen support, bidirectional text, hyperlinks, and terminal progress reporting. This is ~50 files and ~8000+ LOC in `src/ink/`.

Key components:
- `renderer.ts` -- creates a virtual screen from the Yoga layout tree, diffs against previous frame, emits minimal terminal escape sequences
- `components/` -- React components: `Box`, `Text`, `ScrollBox`, `AlternateScreen`, `Link`, `Button`, etc.
- `terminal.ts` -- terminal capability detection (progress reporting, hyperlinks, cursor control)
- `output.ts` -- virtual screen buffer with character-level diffing
- `screen.ts` -- pooled screen representation for zero-alloc rendering
- `reconciler.ts` -- React reconciler bridge to the DOM element tree

This is a full terminal rendering engine. It is impressive. It is also not what D.U.H. needs at v0.1.

### The rendering tiers

Terminal UIs exist on a spectrum:

| Tier | Mechanism | Capabilities | LOC |
|------|-----------|--------------|-----|
| **Bare** | `print()` / `sys.stdout.write()` | Text, ANSI colors, streaming | ~50 |
| **Rich** | Rich library (styled panels, spinners, tables, markdown) | Styled output, live display, progress bars | ~200 |
| **Full TUI** | textual, prompt_toolkit, or custom Yoga port | Flexbox layout, widgets, mouse, alt-screen | ~2000+ |

Claude Code jumped straight to Tier 3 because it was built in TypeScript with React/Ink already available. D.U.H. should climb the tiers incrementally.

### Why the kernel is renderer-agnostic

The kernel (`loop.py`, `engine.py`) yields events --- `text_delta`, `tool_use`, `tool_result`, `thinking_delta`, `error`, `done`. It never calls `print()`. It never imports a rendering library. The CLI's `run_print_mode()` is the first renderer: it consumes events and writes to stdout/stderr.

This is the right architecture. The UI is a **port**, not baked into the kernel. A bare renderer, a Rich renderer, a full TUI renderer, and a JSON-only machine renderer are all just different consumers of the same event stream.

### What needs rendering

| Element | Bare (print) | Rich | Full TUI |
|---------|-------------|------|----------|
| Streaming text | `sys.stdout.write(delta)` | `Live` + `Markdown` | Widget |
| Tool call display | `> ToolName(args)` on stderr | Styled panel | Component |
| Tool result | `< output[:100]` on stderr | Collapsible panel | Component |
| Permission prompt | `[y/n]?` on stderr | Styled prompt | Modal dialog |
| Thinking | Dim italic on stderr | Dim panel | Collapsible |
| Progress/spinner | None | `Spinner` or `Progress` | Widget |
| Error | Red text on stderr | Red panel | Error overlay |

## Decision

### 1. Three rendering tiers, start with Bare + Rich

**Tier 0 (Bare)** is already implemented in `cli/main.py`. It works. It ships.

**Tier 1 (Rich)** adds styled output via the `rich` library (optional dependency). Streaming text gets markdown rendering. Tool calls get styled panels. Progress gets spinners. Permission prompts get styled yes/no.

**Tier 2 (Full TUI)** is future work. When the time comes, use `textual` (Python's answer to Ink) or a custom renderer. The kernel does not change.

### 2. Renderer protocol

```python
class Renderer(Protocol):
    """Consumes engine events and produces terminal output."""

    def render_text_delta(self, text: str) -> None: ...
    def render_tool_use(self, name: str, input: dict) -> None: ...
    def render_tool_result(self, output: str, is_error: bool) -> None: ...
    def render_thinking(self, text: str) -> None: ...
    def render_error(self, error: str) -> None: ...
    def render_permission_prompt(self, tool: str, input: dict) -> str: ...
    def finish(self) -> None: ...
```

The CLI selects a renderer based on capabilities:
- `--output-format json` -> JSON renderer (machine-readable)
- Rich installed + TTY -> Rich renderer
- Fallback -> Bare renderer

### 3. Streaming text display

Text deltas are written character-by-character as they arrive from the model. No buffering. The user sees the model "type" in real time.

For Bare: `sys.stdout.write(text); sys.stdout.flush()`
For Rich: `Live` context with incremental `Markdown` rendering.

### 4. Tool progress display

Tool calls show the tool name and a summary of input. Tool results show success/error status. Long-running tools (Bash) show a spinner.

### 5. Permission prompts

When `approve()` returns `"ask"`, the renderer shows a prompt. Bare uses `input()`. Rich uses a styled prompt with highlighted tool name and input summary.

## Architecture

```
Engine (yields events)
  |
  CLI (selects renderer)
  |
  +-- BareRenderer (print/write)
  |
  +-- RichRenderer (Rich library)
  |
  +-- JsonRenderer (JSON to stdout)
  |
  +-- [Future] TextualRenderer (full TUI)
```

## Consequences

- The kernel stays clean: no rendering code, no UI imports
- Bare mode works everywhere, even without Rich installed
- Rich mode provides a polished experience with zero kernel changes
- Full TUI can be added later as another renderer
- Machine-readable JSON output is just another renderer
- Testing renderers is easy: feed events, check output
