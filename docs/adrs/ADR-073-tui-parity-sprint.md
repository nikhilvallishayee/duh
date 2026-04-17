# ADR-073: TUI Parity Sprint

**Status**: Proposed
**Date**: 2026-04-18
**Supersedes**: None
**Context**: Comparative TUI analysis against Claude Code (Ink/React), OpenCode (Go/Bubble Tea), and Codex (Rust/Ratatui)

## Context

D.U.H. ships two parallel user interfaces that consume the same `engine.run()` event stream:

1. A Rich-backed REPL (`duh/cli/repl.py`, `duh/cli/repl_renderers.py`) — line-mode, readline-driven
2. A Textual TUI (`duh/ui/app.py`) — full-screen reactive widget tree

A comparative analysis of Claude Code, OpenCode, and Codex against D.U.H. surfaced a set of measurable gaps that make the TUI feel incomplete next to mature agent CLIs.

### Critical gaps (blocking users today)

- **TUI command parity**: 18+ slash commands available in the REPL (`/plan`, `/snapshot`, `/jobs`, `/template`, `/pr`, `/undo`, `/health`, `/audit`, `/connect`, `/models`, `/brief`, `/status`, `/changes`, `/git`, `/tasks`, `/search`, `/compact-stats`, `/attach`) are not wired into the TUI. A subset (`/memory`, `/cost`, `/context`, `/clear`, `/model`, `/sessions`) is reimplemented inline inside `app.py`, which duplicates logic and drifts from `SlashDispatcher`.
- **Approval modal has no timeout**: `TUIApprover.check()` awaits `push_screen_wait()` indefinitely. A walk-away user hangs the worker.
- **Multi-line input unsupported**: Both the REPL and TUI use single-line `Input`. Shift+Enter does not insert a newline. Code-heavy prompts are painful.

### Rendering quality gaps (friction, not blockers)

- **Output style inconsistency**: Tool-result truncation differs between REPL (120 chars success / debug-only / 500 chars) and TUI (120 / 0 / 1000 depending on style). Thinking blocks are debug-only in REPL and always collapsible in TUI.
- **No syntax highlighting in TUI**: Textual's native `Markdown` widget renders fenced code as plain text. RichRenderer uses `Rich.Syntax` embedded in `Rich.Markdown` and gets language-aware highlighting.
- **RichRenderer cursor rewind fragility**: `sys.stdout.write(f"\033[{lines}A\033[J")` silently prints literal escape codes in non-TTY (pipes, log files, restricted terminals).
- **Post-turn cost updates only**: Competitors update token/cost on every delta. D.U.H. updates once per turn.

### Polish gaps (parity with Codex / OpenCode)

- No command palette (Codex: `?`, OpenCode: `Ctrl+K`)
- No theme switcher (OpenCode has 8 built-in themes)
- Static tool-result spinner in TUI (REPL has animated Braille spinner)
- No transcript virtualization (Codex lazy-renders `display_lines()` per visible cell; Claude Code uses `VirtualMessageList`)
- No frame-rate cap (Codex caps at 120 FPS with a backpressure-aware commit-tick scheduler)

## Decision

Run a three-wave TUI sprint. Each wave closes one class of gaps. Waves are independently mergeable.

### Wave 1 — Parity (blocking)

**Goal**: TUI has every slash command the REPL has, approval can't hang, multi-line input works.

1. **Unify slash dispatch in the TUI.** Remove the inline command handlers in `duh/ui/app.py`. Construct a `SlashContext` with the TUI's engine/deps/executor/task_manager/model and delegate to `SlashDispatcher.dispatch()`. TUI-specific handlers (`/style`, `/mode`) become methods on a `TUIExtraDispatcher` that composes with the shared dispatcher.
2. **Port the 18 missing commands** so they work from the TUI. `/plan` and `/snapshot` need new async-compatible TUI screens (modal for plan approval, status panel for ghost filesystem state). `/jobs`, `/health`, `/audit`, `/compact-stats`, `/status`, `/changes`, `/git`, `/tasks`, `/search`, `/template`, `/pr`, `/undo`, `/attach`, `/connect`, `/models`, `/brief` can render their output into the existing message log.
3. **Approval timeout.** `TUIApprover.check()` races `push_screen_wait()` against an `asyncio.wait_for(..., timeout=60)`. On timeout: auto-deny, surface a `Permission auto-denied after 60s` message, cache `deny` for the session so the next request doesn't re-prompt. Make the timeout configurable via `AppConfig.approval_timeout_seconds`.
4. **Multi-line input.** Replace the Textual `Input` widget with a `TextArea` configured for chat (soft-wrap, fixed 6-line max height). Binding: `Enter` submits, `Shift+Enter` inserts newline, `Ctrl+J` inserts newline (fallback for terminals that don't distinguish `Shift+Enter`).

### Wave 2 — Rendering quality

**Goal**: REPL and TUI render the same event identically within a chosen output style.

5. **Single `OutputTruncationPolicy`** consulted by both `_RichRenderer` and `ToolCallWidget`. Parameters: `style: OutputStyle`, `is_error: bool`, returns `max_chars` and `max_lines`. Error/success paths use the same policy. Remove the per-site constants.
6. **Syntax highlighting in the TUI.** Replace the vanilla `Markdown` widget with a subclass that detects fenced code blocks and renders them via `rich.syntax.Syntax` converted to Textual markup. Fallback to the existing plain rendering if language inference fails.
7. **Cursor rewind safety.** `_RichRenderer.flush_response()` checks `sys.stdout.isatty()` before emitting CSI sequences. In non-TTY mode, skip the rewind and print a `---` separator.
8. **Live token counter.** Engine emits `usage_delta` events during streaming (we already compute this for cache tracking — just surface it). Both renderers update the status line reactively.

### Wave 3 — Polish

**Goal**: Parity with Codex on performance and discoverability, OpenCode on theming.

9. **Command palette.** `Ctrl+K` opens a Textual modal with fuzzy-filtered list of all slash commands and their descriptions. Selecting a command inserts `/<name> ` into the input. Backported to the REPL via readline-menu where feasible.
10. **Theme system.** Add `ThemeSelector` modal (`/theme` slash command). Ship 5 built-in themes: `duh-dark` (default), `duh-light`, `catppuccin`, `tokyonight`, `gruvbox`. Themes are Textual CSS files in `duh/ui/themes/`.
11. **Animated TUI spinner.** `ToolCallWidget` runs a `Timer` that updates the spinner glyph every 80ms until `set_result()` is called.
12. **Transcript virtualization.** Port the Codex pattern: `MessageWidget` computes `desired_height(width)` on insert and caches it; widget bodies render lazily on scroll.
13. **Frame-rate cap.** Textual's driver already rate-limits to 60 FPS by default; tune streaming batch size (coalesce `text_delta` events that arrive within 8ms) to avoid burning render budget on sub-frame updates.

### Migration & compatibility

- `SlashDispatcher` gains an `async` variant (current version is sync). REPL keeps its sync entry point; TUI gets `async_dispatch()`. Internally both reuse the same handler table, with async handlers `await`ed in the TUI path.
- `SlashContext` grows fields needed by the ported commands (e.g. `task_manager`, `template_state`, `plan_mode`). These are already present in `SessionBuild` — expose them through `SessionBuilderOptions.with_plan_mode=True`.
- `AppConfig.approval_timeout_seconds: float = 60.0` (nullable to disable).
- `OutputTruncationPolicy` lives in `duh/ui/styles.py` next to `OutputStyle`.

## Consequences

### Positive

- TUI becomes a first-class peer to the REPL. Users can pick their interface without losing features.
- Rendering policy consolidation eliminates a class of "works in REPL, doesn't in TUI" bugs.
- Approval timeout removes the single worst failure mode of the TUI (worker hang).
- Multi-line input unblocks code-heavy workflows.
- Wave 2 parity with Rich removes cognitive friction when switching modes.
- Wave 3 brings the TUI up to Codex-grade polish for users who live in it.

### Negative

- `SlashDispatcher` async refactor touches every existing handler. Done carefully it's mechanical, but the blast radius is large.
- Multi-line `TextArea` changes existing muscle memory (Enter behaviour is unchanged but the input now takes multiple lines of screen space).
- Theme system adds CSS file shipping + loader code. Minor but non-zero.
- Virtualization (Wave 3) is a known source of scroll bugs if done wrong.

### Neutral

- ~180 hours total across three waves. Wave 1 alone (~40h) closes all the blocking gaps.
- All work is mergeable independently — Wave 1 can ship without Waves 2–3 starting.

## References

- Comparative TUI analysis (internal): Claude Code TS / OpenCode / Codex / D.U.H. tear-downs
- Codex streaming pipeline: `codex-rs/tui/src/streaming/` (newline-gated collector, queued commit ticks)
- OpenCode theme registry: `opencode/internal/tui/theme/`
- Claude Code Ink integration: `tengu-legacy/src/screens/REPL.tsx`
- ADR-011 (REPL architecture), ADR-062 (output styles), ADR-067 (progress indicators + recent files)
