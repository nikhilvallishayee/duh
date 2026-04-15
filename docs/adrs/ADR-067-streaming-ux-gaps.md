# ADR-067: Streaming UX and Rendering — Competitive Gaps

**Status:** Proposed — 2026-04-15
**Date:** 2026-04-15
**Related:** ADR-011 (TUI architecture), ADR-062 (output styles), ADR-065 (competitive positioning)

## Context

Agent CLI tools are converging on a set of UX expectations that go well beyond "stream text to a terminal." Users now expect rich, interactive rendering: thinking indicators, syntax-highlighted diffs, progress feedback, file-tree awareness, and polished markdown. D.U.H. has a solid three-tier rendering architecture (ADR-011) and a Textual-based TUI, but several competitive gaps remain.

This ADR surveys how the major agent CLIs handle streaming output and user experience, identifies where D.U.H. falls short, and proposes a prioritized gap-closure plan.

## Competitive Landscape

### Claude Code

Claude Code uses an Ink-based (React for terminals) TUI that sets the current high-water mark for agent CLI UX:

- **Thinking blocks**: Displayed inline with a collapsible "Thinking..." indicator. Users see a visual signal that the model is reasoning, with the option to expand and read the thinking content. Always visible in default mode, not just debug.
- **Markdown rendering**: Full GitHub-flavored markdown with syntax-highlighted code blocks, tables, lists, blockquotes. Rendered inline in the streaming output.
- **Tool panels**: Collapsible panels for each tool call showing tool name, a description of what it does, input summary, and result. Panels auto-collapse after completion so they do not clutter the conversation.
- **Diff rendering**: Edit and Write tool results show syntax-highlighted unified diffs inline, making it immediately clear what changed. File paths, added lines, and removed lines are color-coded.
- **Progress indicators**: Long-running tool executions (Bash commands, file operations) show spinners and elapsed-time counters. The user always knows the agent is working.
- **File tracking**: A status area shows files that have been read, written, or edited during the session. Not a full tree sidebar, but contextual file awareness.
- **Image display**: Inline image rendering in supported terminals (iTerm2, Kitty, Sixel).
- **Permission prompts**: Styled modal-like prompts with the tool name, input, and a clear yes/no/always choice.

### GitHub Copilot CLI

Copilot CLI takes a more minimal approach but handles the essentials well:

- **Streaming with spinners**: Text streams character-by-character with animated spinners during model inference and tool execution.
- **Collapsible diffs**: Code changes are presented as collapsible diff blocks. The user can expand to see the full unified diff or collapse to a one-line summary.
- **Thinking**: No explicit thinking block display (Copilot's underlying models do not expose chain-of-thought in the same way).
- **Progress**: Simple spinner animation during tool execution. No elapsed-time display.
- **Markdown**: Basic markdown rendering in terminal output. Tables and complex formatting are handled but not as polished as Claude Code.

### Codex CLI

OpenAI's Codex CLI focuses on sandbox transparency:

- **Terminal streaming**: Standard character-by-character streaming output.
- **Sandbox status indicators**: Visual indicators showing when the agent is executing code in a sandboxed environment. Status includes "thinking," "running code," and "reviewing output."
- **Diff rendering**: Changes are shown as diffs with file-level summaries (files added, modified, deleted). Full unified diffs are available on demand.
- **Progress**: Status line updates during sandbox execution showing the current phase.
- **Thinking**: No dedicated thinking block display; status indicators serve a similar purpose ("thinking..." status).
- **Markdown**: Basic terminal markdown rendering.

### Gemini CLI

Google's Gemini CLI is relatively new but includes some thoughtful UX choices:

- **Streaming with thinking indicators**: Text streams with a visible "Thinking..." status during model reasoning. The thinking content itself is not shown, but the indicator provides feedback.
- **Markdown**: Rendered in terminal with code block highlighting.
- **Tool execution**: Tool calls shown with status updates. No collapsible panels.
- **Progress**: Spinner during model calls and tool execution.
- **Directory awareness**: Sessions are directory-scoped and auto-switch when the user changes directories. The current project context is always visible.

### OpenCode

OpenCode takes the most ambitious TUI approach in the open-source space:

- **Dual-pane TUI**: A full Bubble Tea (Go) terminal application with separate chat pane and editor/diff pane side by side. Users can see conversation and code changes simultaneously.
- **Editor integration**: The diff pane shows file changes with syntax highlighting, similar to a code editor.
- **Markdown**: Full rendering in the chat pane with syntax highlighting.
- **Thinking**: Visible as a status indicator.
- **Progress**: Status bar with model, token count, and execution status.
- **Session management**: UI for browsing and switching between sessions.

## Summary Matrix

| Capability | Claude Code | Copilot CLI | Codex CLI | Gemini CLI | OpenCode | **D.U.H. (current)** |
|---|---|---|---|---|---|---|
| Thinking block display | Collapsible, always visible | N/A | Status only | Indicator only | Indicator | Debug only, collapsed |
| Diff rendering (Edit/Write) | Syntax-highlighted inline | Collapsible diffs | On-demand diffs | None | Side pane | None |
| Progress indicators | Spinner + elapsed time | Spinner | Phase status | Spinner | Status bar | Spinner (TUI), basic (Rich) |
| File tree / touched files | Status area | None | File summary | Directory context | Editor pane | Sidebar (placeholder) |
| Markdown quality | Full GFM + syntax | Basic | Basic | Code blocks | Full in chat pane | Rich (Tier 1), Textual Markdown (Tier 2) |
| Table rendering | Full GFM tables | Basic | Basic | Basic | Full | Rich tables (Tier 1), Textual Markdown (Tier 2) |
| Code block highlighting | Per-language syntax | Monochrome | Monochrome | Per-language | Per-language | Per-language (Rich Syntax) |
| Image display | Inline (iTerm2/Kitty) | None | None | None | None | None |
| Dual pane | No | No | No | No | Yes | No (sidebar only) |
| Permission styling | Modal-like prompt | Inline | Sandboxed (no prompt) | Auto | Auto | Styled prompt (Rich), auto (TUI) |

## Gap Analysis: D.U.H.

### Gap 1: Thinking Block Display (Priority: P0)

**Current state**: Thinking deltas are only rendered when `--debug` is set. In both the Rich renderer and the TUI, thinking tokens are silently discarded in default mode. The TUI's `ThinkingWidget` exists and works, but `_run_query` skips it unless `self._debug` is True.

**Why it matters**: Thinking indicators are table-stakes UX for models with extended thinking. Users need feedback that the model is reasoning, especially during long pauses. Every major competitor provides at least an indicator.

**Proposed fix**: Display a non-expanded "Thinking..." indicator in default mode (collapsed, no content shown). In verbose mode (ADR-062), expand the thinking block to show full content. In concise mode, show only a brief duration indicator.

**Effort**: Small. The `ThinkingWidget` already exists. The change is removing the `if self._debug` guard and adding style-aware collapse behavior.

### Gap 2: Diff Rendering for Edit/Write Tools (Priority: P0)

**Current state**: The Edit tool (`duh/tools/edit.py`) computes a unified diff internally via `_make_diff()` and includes it in the tool result string. But the renderers display tool results as plain text (first line only in success case, first 300 chars in error case). The diff is computed but never rendered as a diff.

**Why it matters**: File changes are the primary output of a coding agent. Showing diffs with syntax highlighting (green for additions, red for deletions) is essential for the user to understand what the agent did. Claude Code and Copilot both do this well.

**Proposed fix**:
- In the Rich renderer: Detect tool results that contain unified diff content (look for `---`/`+++`/`@@` markers). Render with `rich.syntax.Syntax` using the `diff` lexer.
- In the TUI: Add a `DiffWidget` that parses unified diff output and renders with color-coded lines. Mount it inside the `ToolCallWidget` when the tool is Edit or Write.
- In both cases: Show the file path prominently and a stat summary (e.g., "+12 -3 lines").

**Effort**: Medium. Diff detection heuristic is straightforward. Rich has built-in diff syntax support. Textual needs a new widget but it is a straightforward Static with styled content.

### Gap 3: Progress Indicators for Long Tool Executions (Priority: P1)

**Current state**: The Rich renderer writes a single-frame spinner (`\r  ⠋ running {name}...`) when a tool starts, then clears it on result. The TUI shows `"⠋ running..."` as static text. Neither animates. Neither shows elapsed time.

**Why it matters**: Bash commands can run for seconds or minutes. The user needs confidence that the agent has not hung. An animated spinner with elapsed time is the minimum; a progress bar for known-length operations is better.

**Proposed fix**:
- Rich renderer: Use `rich.progress.Progress` or `rich.status.Status` for an animated spinner on stderr during tool execution. Display elapsed seconds.
- TUI: Use Textual's `Timer` to animate the spinner frames in `ToolCallWidget`. Show elapsed time next to the spinner.
- For Bash specifically: If the command is long-running (>2 seconds), show a "still running..." heartbeat.

**Effort**: Small-medium. Rich has built-in progress support. Textual timer integration is well-documented.

### Gap 4: File Tree Sidebar Showing Touched Files (Priority: P2)

**Current state**: The TUI sidebar exists (`#sidebar` in `app.py`) but is empty except for the logo. It is hidden by default (CSS `display: none`). The `FileTracker` in `duh/kernel/file_tracker.py` already tracks which files were read, written, and edited, and can produce `diff_summary()`.

**Why it matters**: As sessions grow, users lose track of which files the agent has touched. A sidebar listing files (grouped by read/written/edited) provides situational awareness. OpenCode solves this with a full editor pane; Claude Code shows a file status area.

**Proposed fix**:
- Wire the `FileTracker`'s tracked-files data into the TUI sidebar.
- Group files by operation: read (dim), written (green), edited (yellow).
- Update the list reactively as new tool results arrive.
- Show file path relative to cwd, with change count if edited multiple times.

**Effort**: Medium. The data source (FileTracker) and the container (sidebar) both exist. The work is wiring them together and building a simple `FileListWidget`.

### Gap 5: Markdown Rendering Quality (Priority: P2)

**Current state**: Rich renderer uses `rich.markdown.Markdown` which handles code blocks (with syntax highlighting via `rich.syntax.Syntax`), headers, lists, bold/italic, and blockquotes. Textual's `Markdown` widget handles similar elements. Both are functional but have known limitations:
- GFM tables render adequately in Rich but can misalign with wide content.
- Nested lists sometimes lose indentation in Textual's Markdown widget.
- No special handling for task lists (`- [ ]` / `- [x]`).
- Link rendering shows raw URLs rather than styled hyperlinks (terminal support varies).

**Why it matters**: Markdown is the primary output format for agent responses. Small rendering issues compound over a long session and reduce readability.

**Proposed fix**:
- For Rich: Table rendering is already good. Focus on task-list rendering (detect `- [ ]` patterns) and hyperlink support via Rich's `[link]` markup on OSC-8-capable terminals.
- For Textual: Contribute upstream or patch locally for nested list indentation. Add task-list support via a custom `MarkdownBlock`.
- Both: Add a `/render` debug command that re-renders the last assistant message to diagnose markdown issues.

**Effort**: Medium-large. Upstream Textual Markdown improvements may be needed. Rich-side fixes are smaller.

### Gap 6: Image Display Support (Priority: P3)

**Current state**: No image display. If the model references an image or a tool produces image output, it is either omitted or shown as a file path.

**Why it matters**: Image display is a nice-to-have, not a blocker. Only Claude Code supports it, and only on specific terminals. However, as agent CLIs mature, screenshot analysis, diagram generation, and chart rendering will become more common.

**Proposed fix**:
- Detect terminal capabilities: iTerm2 (inline images via escape sequences), Kitty (graphics protocol), Sixel (broad support). Fall back to showing the file path with a "view with..." hint.
- In the TUI: Textual does not natively support inline images, but it can open the system image viewer or use a placeholder with file path. Long-term: contribute to Textual's image support or use `term-image` library.
- In the Rich renderer: Use `rich-pixels` or direct escape sequences for iTerm2/Kitty.

**Effort**: Large. Terminal image protocols are fragile and terminal-specific. Acceptable as a P3 stretch goal.

## Decision

Close the gaps in priority order:

| Phase | Gaps | Target |
|---|---|---|
| **Phase 1** | Gap 1 (thinking display), Gap 2 (diff rendering) | Next sprint |
| **Phase 2** | Gap 3 (progress indicators), Gap 4 (file sidebar) | Following sprint |
| **Phase 3** | Gap 5 (markdown quality) | Ongoing incremental |
| **Phase 4** | Gap 6 (image display) | Stretch / post-v1 |

### Design Principles

1. **Renderer-agnostic events**: The kernel already yields typed events. New rendering features must not require kernel changes. The renderers interpret events differently based on tier and output style.
2. **Progressive enhancement**: Bare renderer stays simple. Rich renderer adds styled output. TUI adds widgets. Each tier degrades gracefully.
3. **Output style aware**: All new rendering features must respect the `OutputStyle` enum (ADR-062). Concise mode hides more; verbose mode shows more; default mode is the balanced middle.
4. **No new dependencies for Tier 0/1**: Rich is already optional. Do not add required dependencies for basic rendering improvements. Image display (P3) may add an optional dependency.

## Consequences

### Positive

- Closes the two most visible competitive gaps (thinking display, diff rendering) with small-to-medium effort
- Leverages existing infrastructure: `ThinkingWidget`, `_make_diff()`, `FileTracker`, sidebar container
- Each gap can be closed independently without architectural changes
- Aligns with ADR-062 output styles so the work compounds

### Negative

- Phase 3 (markdown quality) may require upstream contributions to Textual, which is outside our control
- Phase 4 (image display) has high effort-to-value ratio and fragile terminal compatibility
- More rendering code means more rendering tests; each renderer variant multiplied by each output style is a matrix to cover

## Implementation Notes

- Thinking display: Remove the `if self._debug` guard in `duh/ui/app.py` line 508-511 and `duh/adapters/renderers.py` lines 47-48, 207-209. Add style-conditional collapse.
- Diff rendering: Detect diff markers in `render_tool_result()`. Use `rich.syntax.Syntax(diff_text, "diff")` for Rich tier. Create `DiffWidget` for TUI tier.
- Progress: Replace static spinner text in `ToolCallWidget.compose()` with a Textual `Timer`-driven animation. Use `rich.status.Status` in `RichRenderer.render_tool_use()`.
- File sidebar: Add `FileListWidget` to `duh/ui/widgets.py`. Wire `FileTracker` events into `DuhApp._run_query()`.
- All changes scoped to `duh/adapters/renderers.py`, `duh/ui/widgets.py`, `duh/ui/app.py`, and `duh/ui/theme.py`. Kernel untouched.
