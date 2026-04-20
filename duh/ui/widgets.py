"""Custom Textual widgets for the D.U.H. TUI (ADR-011 Tier 2).

Widgets
-------
MessageWidget        — renders a single conversation turn (user or assistant)
HighlightedMarkdown  — Rich-backed Markdown Static with syntax highlighting
ToolCallWidget       — collapsible panel: tool name, input summary, output
ThinkingWidget       — dim/italic block for extended-thinking tokens
"""

from __future__ import annotations

import re
from typing import Any

from textual.app import ComposeResult
from textual.markup import escape as escape_markup
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Collapsible, Label, Static

from duh.ui.styles import OutputStyle, OutputTruncationPolicy

# Rich is a hard dependency of duh-cli (see pyproject.toml: "rich>=13.0,<16.0"),
# but we gate the import defensively so this module never hard-fails if a
# downstream consumer strips it out.  When Rich is unavailable we degrade
# `HighlightedMarkdown` to a plain Textual `Markdown` widget.
try:
    from rich.markdown import Markdown as RichMarkdown
    from rich.syntax import Syntax as RichSyntax  # noqa: F401  (re-exported for tests)

    _HAS_RICH = True
except ImportError:  # pragma: no cover — Rich is a hard dep in practice
    RichMarkdown = None  # type: ignore[assignment]
    RichSyntax = None  # type: ignore[assignment]
    _HAS_RICH = False


# ---------------------------------------------------------------------------
# HighlightedMarkdown — Rich-backed Markdown with syntax-highlighted code blocks
# ---------------------------------------------------------------------------
#
# Textual ships a `MarkdownFence` widget that renders fenced code blocks but
# *without* language-aware syntax highlighting — the rendered text uses the
# theme's default color for every token.  The REPL's RichRenderer gets
# highlighting for free because it pipes content through `rich.markdown.Markdown`
# with `code_theme="monokai"`, which internally drives each fence through
# `rich.syntax.Syntax` (Pygments-backed).
#
# This widget closes that gap for the TUI (ADR-073 Wave 2 #6).  It composes a
# single `Static` whose renderable is a `rich.markdown.Markdown` instance.
# Textual's `Static.update()` accepts arbitrary Rich renderables, so the full
# Rich Markdown output — headers, bold, lists, tables, AND syntax-highlighted
# code fences — is rendered natively inside the Textual widget tree.
#
# Streaming semantics: `update(content)` re-renders the entire markdown string.
# Rich's parser is fast enough that doing this on every `text_delta` is fine
# (measured <2ms for 4 KB messages).


# Rich markdown themes (Pygments themes for code blocks).
#
# "monokai" — dark, high-contrast; matches RichRenderer in `repl_renderers.py`.
# "default" — light, Pygments' default; used when the user picks a light UI.
#
# The theme is passed to `rich.markdown.Markdown(code_theme=...)`, which uses
# it only for fenced code blocks.  Everything else (headers, bold, lists) is
# styled by Rich's own markdown styles + the Textual CSS theme.
def _detect_code_theme() -> str:
    """Pick a Pygments theme based on terminal background (gap #15).

    Uses the ``COLORFGBG`` env var exported by xterm, rxvt, iTerm2, and
    many others: ``"fg;bg"`` where 0-6 = dark bg, 7-15 = light bg.
    Falls back to ``"monokai"`` when the var is missing or unparseable
    so dark-terminal users (the common case) keep the historical theme.
    """
    import os
    raw = os.environ.get("COLORFGBG", "")
    if raw:
        parts = raw.split(";")
        if len(parts) >= 2 and parts[-1].isdigit():
            bg = int(parts[-1])
            if 7 <= bg <= 15:
                return "default"  # light background → light Pygments theme
    return "monokai"


DEFAULT_CODE_THEME = _detect_code_theme()


class HighlightedMarkdown(Static):
    """Markdown renderer with language-aware syntax highlighting.

    Parameters
    ----------
    content:
        Markdown source.  May contain fenced code blocks with or without a
        language tag; unknown languages fall back to Pygments' ``TextLexer``
        (Rich handles this automatically — no exception is raised).
    code_theme:
        Pygments theme name used by ``rich.syntax.Syntax`` for code fences.
        Defaults to ``"monokai"``.

    Notes
    -----
    When Rich is unavailable at import time, the widget degrades to rendering
    the raw markdown text via Textual's default Static markup so that the TUI
    never crashes on a malformed environment.
    """

    DEFAULT_CSS = """
    HighlightedMarkdown {
        height: auto;
        background: transparent;
        color: $text;
    }
    """

    def __init__(
        self,
        content: str = "",
        *,
        code_theme: str = DEFAULT_CODE_THEME,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, markup=False)
        self._markdown_source = content
        self._code_theme = code_theme
        # Populate the initial renderable.  Do this *after* super().__init__()
        # so the widget is fully constructed.
        self._refresh_renderable()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_markdown(self, content: str) -> None:
        """Replace the markdown source and re-render.

        This is the streaming-friendly entry point: MessageWidget.append()
        calls it on every `text_delta`.  Rich's markdown parser is fast
        enough that re-parsing on every delta is acceptable.
        """
        self._markdown_source = content
        self._refresh_renderable()

    @property
    def markdown_source(self) -> str:
        """Return the raw markdown source (for tests / debugging)."""
        return self._markdown_source

    @property
    def code_theme(self) -> str:
        """Return the configured Pygments theme name."""
        return self._code_theme

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _refresh_renderable(self) -> None:
        """Build a Rich Markdown renderable from the current source and
        hand it to ``Static.update()``.

        Falls back to plain-text rendering when Rich is not importable.
        """
        if _HAS_RICH and RichMarkdown is not None:
            try:
                renderable = RichMarkdown(
                    self._markdown_source,
                    code_theme=self._code_theme,
                )
                self.update(renderable)
                return
            except Exception:
                # Defensive: if Rich raises on malformed markdown, fall back
                # to plain text so the TUI never crashes.
                pass
        # Plain-text fallback (Rich unavailable or raised).
        self.update(self._markdown_source)


# ---------------------------------------------------------------------------
# MessageWidget
# ---------------------------------------------------------------------------


class MessageWidget(Widget):
    """Renders a single conversation message.

    Parameters
    ----------
    role:
        ``"user"`` or ``"assistant"``.
    text:
        Initial text content (may be empty; call :meth:`append` to stream).

    Deferred markdown parsing (streaming optimization)
    -------------------------------------------------
    For *assistant* messages the widget composes a lightweight plain
    :class:`Static` during streaming.  Every :meth:`append` just writes
    the raw concatenated text to that Static — no markdown parse, no
    syntax-highlight pass.  When the upstream event loop finalises the
    turn and calls :meth:`finish`, the Static is swapped out for a
    :class:`HighlightedMarkdown` which runs the Rich markdown parse
    exactly ONCE on the full, now-complete source.

    Motivation: on a 20 KB response at ~125 coalesced flushes, the old
    path triggered 125 full-buffer markdown parses (O(n) each → O(n²)
    overall, ~2 s wasted CPU while streaming).  The new path performs a
    single parse at turn-end.

    User messages retain the original plain-Static behaviour unchanged —
    they were never markdown-rendered.
    """

    DEFAULT_CSS = """
    MessageWidget {
        height: auto;
        margin: 0 0 1 0;
    }
    """

    # Reactive text so the widget re-renders when streamed text arrives.
    _content: reactive[str] = reactive("", layout=True)

    def __init__(
        self,
        role: str,
        text: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        role_class = "message-user" if role == "user" else "message-assistant"
        merged = f"{role_class} {classes}".strip() if classes else role_class
        super().__init__(name=name, id=id, classes=merged)
        self._role = role
        self._content = text
        # Plain Static body used for:
        #   * user messages (always), and
        #   * assistant messages WHILE streaming (pre-finish).
        self._body: Static | None = None
        # HighlightedMarkdown body used for assistant messages AFTER
        # finish().  Populated by :meth:`finish` on the streaming path,
        # or (for legacy / test call-sites that never call append/finish)
        # by :meth:`on_mount` when the caller passes non-empty initial
        # text that is never updated — see _finalized handling there.
        self._md_body: HighlightedMarkdown | None = None
        # Streaming-state flag.  Starts True for assistant messages;
        # flips False on first finish() call.  User messages are not
        # "streaming" in the markdown sense (no parse ever runs), so
        # the flag is meaningless for them but kept True for symmetry.
        self._streaming: bool = True

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        label_text = "You" if self._role == "user" else "Assistant"
        yield Label(label_text, classes="message-role-label")
        # Both roles start with a plain Static.  For user messages this
        # is also the final body.  For assistant messages it is the
        # streaming body; finish() replaces it with HighlightedMarkdown.
        #
        # markup=False prevents Textual from interpreting stray [style]
        # tokens in raw model output as markup — critical for preserving
        # the exact bytes we received.
        yield Static(self._content, classes="message-body", markup=False)

    def on_mount(self) -> None:
        self._body = self.query_one(".message-body", Static)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, delta: str) -> None:
        """Append streaming text delta to the message body.

        During streaming (assistant role, pre-finish) this updates a
        plain Static with the raw concatenated text — no markdown parse.
        """
        if not delta:
            return
        self._content += delta
        if self._role == "user":
            if self._body is not None:
                self._body.update(self._content)
            return
        # Assistant path.
        if not self._streaming:
            # Defensive: append() after finish() is not expected.  If it
            # happens, treat it as "re-streaming" — update the markdown
            # body in place.  This is cheaper than swapping widgets.
            if self._md_body is not None:
                self._md_body.update_markdown(self._content)
            return
        if self._body is not None:
            # Plain-text update only — no RichMarkdown parse.
            self._body.update(self._content)

    def finish(self) -> None:
        """Called when streaming is complete — promote to full markdown render.

        Idempotent: calling finish() a second time is a no-op.
        Safe to call before any append() (empty-message edge case).
        """
        if self._role == "user":
            return
        if not self._streaming:
            # Already finalized — idempotent.
            return
        self._streaming = False
        # If we never mounted (e.g. unit test that never awaited
        # run_test), there is no live Static to swap.  Create the
        # markdown body in-memory so .markdown_source is queryable and
        # bail out; on_mount would be too late to run.
        if self._body is None or not self.is_mounted:
            self._md_body = HighlightedMarkdown(
                self._content, classes="message-body",
            )
            return
        # Live-widget path: swap the plain Static for a HighlightedMarkdown
        # that does the single, final markdown parse.  We do this by
        # mounting the new widget and removing the old one.  The order
        # (mount before remove) minimises flicker and prevents a height
        # collapse that could re-flow surrounding messages.
        new_body = HighlightedMarkdown(self._content, classes="message-body")
        try:
            self.mount(new_body, after=self._body)
            self._body.remove()
        except Exception:
            # Defensive: if mounting fails (e.g. widget already being
            # torn down), fall back to in-memory promotion so tests that
            # query markdown_source still see the final content.
            pass
        self._md_body = new_body
        self._body = None


# ---------------------------------------------------------------------------
# ToolCallWidget
# ---------------------------------------------------------------------------

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class ToolCallWidget(Widget):
    """Collapsible panel showing tool name, input summary, and output.

    Usage::

        w = ToolCallWidget(name="Bash", input={"command": "ls /"})
        await app.mount(w)
        # … later …
        w.set_result("total 64\\n...", is_error=False)

    Animated spinner (ADR-073 Wave 3 #11)
    -------------------------------------
    While the widget is running (``_tool_running`` is ``True`` and no
    result has arrived yet), a :class:`~textual.timer.Timer` started in
    :meth:`on_mount` cycles through :data:`_SPINNER_FRAMES` every 80 ms.
    The glyph is updated *inline* into the "running…" label so the TUI
    visibly breathes while tool calls execute.

    Race-free behaviour:
        * If :meth:`set_result` is called *before* :meth:`on_mount`
          (possible because Textual composition is async), ``_tool_running``
          flips to ``False`` first, and :meth:`on_mount` then sees that
          and does not start the timer.
        * If :meth:`set_result` is called *after* :meth:`on_mount`, the
          timer is explicitly stopped inside :meth:`set_result`.

    Output-style awareness:
        * :attr:`OutputStyle.CONCISE` — no animation (save render cycles).
        * :attr:`OutputStyle.DEFAULT` / :attr:`OutputStyle.VERBOSE` —
          full 80 ms animation.
    """

    DEFAULT_CSS = """
    ToolCallWidget {
        height: auto;
        margin: 0 0 0 2;
    }
    """

    # 80 ms per frame = 12.5 FPS — matches the REPL's Braille spinner and
    # Textual's default 60 FPS render budget with room to spare.
    _SPINNER_INTERVAL_S: float = 0.08

    def __init__(
        self,
        tool_name: str,
        input: dict[str, Any],
        *,
        output_style: str = "default",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        merged = f"tool-call-widget {classes}".strip() if classes else "tool-call-widget"
        super().__init__(name=name, id=id, classes=merged)
        self._tool_name = tool_name
        self._input = input
        self._result_label: Static | None = None
        self._collapsible: Collapsible | None = None
        self._tool_running = True
        self._output_style = output_style
        # Spinner state — set in on_mount, stopped in set_result / on_unmount.
        self._spinner_timer: Any = None
        self._spinner_frame_idx = 0

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        summary = _summarise_input(self._input)
        # Escape Rich markup chars in tool input to prevent MarkupError
        safe_summary = escape_markup(summary)
        safe_name = escape_markup(self._tool_name)
        title = f"Tool: {safe_name}({safe_summary})"
        with Collapsible(title=title, collapsed=False) as c:
            self._collapsible = c
            yield Label(f"Input: {safe_summary}", classes="tool-call-label")
            yield Static("⠋ running…", classes="spinner-message", id="tool-result")

    def on_mount(self) -> None:
        self._result_label = self.query_one("#tool-result", Static)
        # Cache the Collapsible handle so set_result() can toggle it even
        # on call-sites where compose()'s context-manager assignment was
        # short-circuited (belt-and-braces — matches ThinkingWidget.on_mount).
        try:
            self._collapsible = self.query_one(Collapsible)
        except Exception:
            pass
        # Race guard: if set_result() already fired (pre-mount), _running
        # is False and the result label has already been populated — do
        # NOT start the spinner.
        if not self._tool_running:
            return
        # CONCISE style skips animation entirely — one static frame only.
        if self._output_style == "concise":
            return
        # Start the animation timer.  ``set_interval`` returns a Timer we
        # keep a handle to so ``set_result`` can cancel it.
        self._spinner_timer = self.set_interval(
            self._SPINNER_INTERVAL_S, self._advance_spinner,
        )

    def on_unmount(self) -> None:
        """Safety net — cancel the timer if the widget is removed before
        a result arrives (e.g. app teardown mid-tool-call)."""
        self._stop_spinner()

    # ------------------------------------------------------------------
    # Spinner animation
    # ------------------------------------------------------------------

    def _advance_spinner(self) -> None:
        """Timer callback: cycle to the next Braille frame."""
        if not self._tool_running or self._result_label is None:
            # Either the result arrived between ticks, or the widget has
            # not composed its label yet — skip this tick.
            return
        self._spinner_frame_idx = (
            self._spinner_frame_idx + 1
        ) % len(_SPINNER_FRAMES)
        frame = _SPINNER_FRAMES[self._spinner_frame_idx]
        self._result_label.update(f"{frame} running…")

    def _stop_spinner(self) -> None:
        """Cancel the spinner timer if one is running.  Idempotent."""
        timer = self._spinner_timer
        if timer is None:
            return
        self._spinner_timer = None
        try:
            timer.stop()
        except Exception:  # pragma: no cover — defensive
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_result(
        self,
        output: str,
        is_error: bool,
        style: str = "default",
        tool_name: str | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        """Update the widget with the tool result.

        Parameters
        ----------
        output:
            Raw tool output text.
        is_error:
            Whether the tool returned an error.
        style:
            One of ``"default"``, ``"concise"``, ``"verbose"``.  Mapped to an
            :class:`OutputTruncationPolicy` (ADR-073) so the REPL and TUI
            render the same number of characters for the same style.
        tool_name:
            Name of the tool that produced this result.  When the tool is
            ``"Edit"`` or ``"Write"`` and the output looks like a diff,
            lines are rendered with green/red coloring.
        elapsed_ms:
            Wall-clock milliseconds the tool call took.  When provided the
            elapsed time is shown next to the status indicator, e.g.
            ``"OK (1.2s)"`` or ``"Error (0.3s)"``.
        """
        self._tool_running = False
        # Stop the animation the instant a result arrives, whether or not
        # the result label is composed yet.  (Stopping a never-started
        # timer is a no-op via the None check in ``_stop_spinner``.)
        self._stop_spinner()
        if self._result_label is None:
            return

        effective_name = tool_name or self._tool_name
        time_suffix = _format_elapsed(elapsed_ms)

        # Resolve the user-facing style string into an OutputStyle enum so we
        # can look up the shared truncation policy.  Unknown values degrade to
        # DEFAULT — matches the TUI's existing "fall through to default" logic.
        try:
            style_enum = OutputStyle(style)
        except ValueError:
            style_enum = OutputStyle.DEFAULT
        policy = OutputTruncationPolicy.for_style(style_enum, is_error=is_error)

        if is_error:
            if policy.max_chars <= 0 or policy.max_lines <= 0:
                # Concise-like policy that suppresses preview text entirely.
                self._result_label.update(f"[red]ERR[/red]{time_suffix}")
            else:
                truncated, was_truncated = policy.apply(output or "")
                preview = escape_markup(truncated) if truncated else "(empty)"
                suffix = " [dim](truncated)[/dim]" if was_truncated else ""
                self._result_label.update(
                    f"[red]Error[/red]{time_suffix}: {preview}{suffix}"
                )
            self._result_label.remove_class("spinner-message")
            self._result_label.add_class("tool-result-error")
        else:
            # Check for diff-like output from Edit/Write tools.
            # Diff rendering has its own line cap (_render_diff max_lines=60)
            # that matches VERBOSE's max_lines — intentionally independent of
            # OutputTruncationPolicy because diffs need their own line-oriented
            # truncation semantics (whole hunks, not arbitrary cuts).
            if effective_name in ("Edit", "Write") and _looks_like_diff(output):
                rendered = _render_diff(output, time_suffix=time_suffix)
                self._result_label.update(rendered)
            elif policy.max_chars <= 0 or policy.max_lines <= 0:
                # Concise success: status only, no preview.
                self._result_label.update(f"[green]OK[/green]{time_suffix}")
            elif (
                effective_name in ("Swarm", "Agent")
                and (multi_agent_summary := _summarise_multi_agent_output(output))
                is not None
            ):
                # Multi-agent tools (Swarm / Agent) emit a structured block
                # per sub-task.  Showing only the first line hides the other
                # N-1 results — the bug that prompted this branch.  Build a
                # task-aware summary instead.
                self._result_label.update(
                    f"[green]OK[/green]{time_suffix}: {multi_agent_summary}"
                )
            elif style_enum is OutputStyle.DEFAULT and output:
                # Preserve the historical "first line only" summary for the
                # DEFAULT style, then apply the char cap from the policy.
                first_line = output.split("\n", 1)[0]
                truncated, was_truncated = policy.apply(first_line)
                preview = escape_markup(truncated) if truncated else "(empty)"
                suffix = " [dim](truncated)[/dim]" if was_truncated else ""
                self._result_label.update(
                    f"[green]OK[/green]{time_suffix}: {preview}{suffix}"
                )
            else:
                truncated, was_truncated = policy.apply(output or "")
                preview = escape_markup(truncated) if truncated else "(empty)"
                suffix = " [dim](truncated)[/dim]" if was_truncated else ""
                self._result_label.update(
                    f"[green]OK[/green]{time_suffix}: {preview}{suffix}"
                )
            self._result_label.remove_class("spinner-message")
            self._result_label.add_class("tool-result-ok")

        # Post-result collapse policy:
        #   * Swarm / Agent   → always expanded (multi-sub-agent output).
        #   * Short success   → stay expanded (default from compose()).
        #   * Verbose success → auto-collapse so a chain of Read/Grep calls
        #     doesn't flood the viewport. Title still shows the one-line
        #     summary so users get signal without visual noise.
        #   * Errors          → stay expanded by default; the stack trace
        #     is often what the user wants to see next.
        if self._collapsible is not None:
            try:
                if effective_name in ("Swarm", "Agent"):
                    self._collapsible.collapsed = False
                elif not is_error and _is_verbose_output(output):
                    self._collapsible.collapsed = True
            except Exception:  # pragma: no cover — defensive
                pass


# Auto-collapse threshold — tool outputs beyond this are folded by default
# so chains of Read/Grep don't fill the viewport. Tuned to keep most
# edits/short commands visible while collapsing file reads and searches.
_VERBOSE_OUTPUT_CHAR_THRESHOLD: int = 500
_VERBOSE_OUTPUT_LINE_THRESHOLD: int = 10


def _is_verbose_output(output: str) -> bool:
    if not output:
        return False
    if len(output) > _VERBOSE_OUTPUT_CHAR_THRESHOLD:
        return True
    if output.count("\n") > _VERBOSE_OUTPUT_LINE_THRESHOLD:
        return True
    return False


def _format_elapsed(elapsed_ms: float | None) -> str:
    """Return a human-readable elapsed-time suffix like `` (1.2s)``."""
    if elapsed_ms is None:
        return ""
    secs = elapsed_ms / 1000.0
    if secs < 0.1:
        return f" ({elapsed_ms:.0f}ms)"
    return f" ({secs:.1f}s)"


def _summarise_input(inp: dict[str, Any]) -> str:
    """Return a compact one-line summary of a tool input dict."""
    if not inp:
        return ""
    parts = []
    for k, v in list(inp.items())[:2]:
        if isinstance(v, str) and len(v) > 40:
            v = v[:40] + "…"
        parts.append(f"{k}={v!r}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Multi-agent (Swarm / Agent) preview helper
# ---------------------------------------------------------------------------
#
# Swarm and Agent tools return output formatted as a sequence of per-task
# blocks::
#
#     --- Task 1/4 [researcher] ---
#     Prompt: ...
#     Status: OK
#     Result: ...
#
#     --- Task 2/4 [reviewer] ---
#     ...
#
# The default TUI preview path takes only the first line — so a 4-task
# swarm looked like a 1-task swarm in the collapsed view, which misled
# users into thinking only one sub-agent ran.  `_summarise_multi_agent_output`
# parses the block markers and produces a task-count summary that is
# visible from the collapsed state.

# Block-header regex.  Tolerant: allows variable whitespace, alphanumeric
# agent-type identifiers, and falls through cleanly when the pattern never
# matches (caller falls back to first-line preview).
_MULTI_AGENT_HEADER_RE = re.compile(
    r"---\s*Task\s+(\d+)\s*/\s*(\d+)\s*\[([^\]]+)\]\s*---",
)


def _summarise_multi_agent_output(text: str) -> str | None:
    """Return an ``"N/M tasks OK[, K errors]"`` summary or ``None``.

    Returns ``None`` when the text does not contain any recognisable
    ``--- Task N/M [type] ---`` headers — the caller should then fall
    back to the normal first-line preview.  This keeps the helper safe
    to call for any tool output without false positives.
    """
    if not text:
        return None
    headers = _MULTI_AGENT_HEADER_RE.findall(text)
    if not headers:
        return None

    # Defensive: prefer the highest declared total (M).  If different task
    # headers report different M values (malformed output) we still surface
    # a sensible number rather than crashing.
    try:
        total = max(int(m) for _n, m, _agent in headers)
    except ValueError:  # pragma: no cover — regex guarantees \d+
        return None
    # Clamp total to at least the number of headers we actually saw, so we
    # never report "1/0 tasks".  Also guard against M == 0.
    total = max(total, len(headers))
    if total <= 0:
        return None

    # Count Status lines across the full output.  Simple substring scan is
    # fine: the marker is unambiguous and this runs once per tool result.
    ok_count = 0
    err_count = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("Status:"):
            status_val = stripped[len("Status:"):].strip().upper()
            if status_val.startswith("OK"):
                ok_count += 1
            elif status_val.startswith("ERROR") or status_val.startswith("ERR") or status_val.startswith("FAIL"):
                err_count += 1

    # Singular vs plural "task" when total == 1 (Agent tool wrapping a
    # single sub-agent call — still hits the Swarm-like output path).
    noun = "task" if total == 1 else "tasks"
    summary = f"{ok_count}/{total} {noun} OK"
    if err_count:
        summary += f", {err_count} errors"
    return summary


# ---------------------------------------------------------------------------
# Diff rendering helpers
# ---------------------------------------------------------------------------


def _looks_like_diff(text: str) -> bool:
    """Return True if *text* appears to contain unified-diff markers."""
    if not text:
        return False
    for line in text.split("\n")[:20]:
        stripped = line.lstrip()
        if stripped.startswith("@@") or stripped.startswith("---") or stripped.startswith("+++"):
            return True
    return False


def _render_diff(text: str, max_lines: int = 60, *, time_suffix: str = "") -> str:
    """Render a diff with Rich markup: green for additions, red for removals.

    Lines starting with ``+`` (but not ``+++``) are green.
    Lines starting with ``-`` (but not ``---``) are red.
    Lines starting with ``@@`` are cyan (hunk headers).
    Everything else is left as-is.
    """
    lines = text.split("\n")[:max_lines]
    rendered: list[str] = [f"[green]OK[/green]{time_suffix}: diff preview"]
    for line in lines:
        safe = escape_markup(line)
        stripped = line.lstrip()
        if stripped.startswith("@@"):
            rendered.append(f"[cyan]{safe}[/cyan]")
        elif stripped.startswith("+++") or stripped.startswith("---"):
            rendered.append(f"[bold]{safe}[/bold]")
        elif stripped.startswith("+"):
            rendered.append(f"[green]{safe}[/green]")
        elif stripped.startswith("-"):
            rendered.append(f"[red]{safe}[/red]")
        else:
            rendered.append(safe)
    if len(text.split("\n")) > max_lines:
        rendered.append(f"[dim]… ({len(text.split(chr(10))) - max_lines} more lines)[/dim]")
    return "\n".join(rendered)


# ---------------------------------------------------------------------------
# ThinkingWidget
# ---------------------------------------------------------------------------


class ThinkingWidget(Widget):
    """Dim/italic block that accumulates extended-thinking tokens.

    Collapsed by default so it does not distract; users can expand it.

    Animated spinner
    ----------------
    While thinking is in progress (no :meth:`finish` call yet), a Braille
    spinner cycles in the collapsible title so the TUI visibly breathes
    while the model reasons. Frame rate matches :class:`ToolCallWidget`
    (80 ms = 12.5 FPS) so both widgets animate in lockstep.

    Title updates (character-count) are throttled to ~8 ms to avoid
    render churn during high-rate thinking streams — deltas accumulate
    but the title is repainted at most every ``_TITLE_REFRESH_S``.
    """

    DEFAULT_CSS = """
    ThinkingWidget {
        height: auto;
        margin: 0 0 0 2;
    }
    """

    _content: reactive[str] = reactive("", layout=True)

    # Match ToolCallWidget so the two animations tick in lockstep.
    _SPINNER_INTERVAL_S: float = 0.08
    # Title repaint cap — matches the text-delta coalescer's 8ms frame cap.
    _TITLE_REFRESH_S: float = 0.008

    def __init__(
        self,
        *,
        collapsed: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        merged = f"thinking-widget {classes}".strip() if classes else "thinking-widget"
        super().__init__(name=name, id=id, classes=merged)
        self._collapsed = collapsed
        self._body: Static | None = None
        self._collapsible: Collapsible | None = None
        self._thinking_running = True
        self._spinner_timer: Any = None
        self._spinner_frame_idx = 0
        self._title_dirty = False
        self._title_timer: Any = None

    def compose(self) -> ComposeResult:
        # Start with the first spinner frame so the title has animation
        # from the moment the widget appears (pre-first-delta).
        initial = f"{_SPINNER_FRAMES[0]} Thinking…"
        with Collapsible(title=initial, collapsed=self._collapsed) as c:
            self._collapsible = c
            yield Static("", classes="thinking-body", markup=False, id="thinking-body")

    def on_mount(self) -> None:
        self._body = self.query_one("#thinking-body", Static)
        try:
            self._collapsible = self.query_one(Collapsible)
        except Exception:
            pass
        # Race guard: if finish() already fired before on_mount (possible in
        # very fast turns or tests), don't start the spinner.
        if not self._thinking_running:
            return
        self._spinner_timer = self.set_interval(
            self._SPINNER_INTERVAL_S, self._advance_spinner,
        )
        # Coalesce title repaints — append() just flips a dirty flag; this
        # timer flushes it at _TITLE_REFRESH_S cadence so a high-rate
        # thinking stream doesn't thrash the UI.
        self._title_timer = self.set_interval(
            self._TITLE_REFRESH_S, self._flush_title,
        )

    def on_unmount(self) -> None:
        """Safety net — cancel timers if the widget is torn down early."""
        self._stop_timers()

    # ------------------------------------------------------------------
    # Spinner + title animation
    # ------------------------------------------------------------------

    def _advance_spinner(self) -> None:
        if not self._thinking_running or self._collapsible is None:
            return
        self._spinner_frame_idx = (
            self._spinner_frame_idx + 1
        ) % len(_SPINNER_FRAMES)
        self._repaint_title()

    def _flush_title(self) -> None:
        """Repaint the title if a delta arrived since the last tick."""
        if not self._title_dirty:
            return
        self._title_dirty = False
        self._repaint_title()

    # Chars of the latest thinking shown inline in the collapsed title —
    # lets users peek at reasoning without expanding. 60 fits ~half a
    # standard terminal width after the spinner glyph + char count.
    _PREVIEW_CHARS: int = 60

    def _repaint_title(self) -> None:
        if self._collapsible is None:
            return
        frame = _SPINNER_FRAMES[self._spinner_frame_idx]
        char_count = len(self._content)
        preview = ""
        if char_count:
            # Show the trailing slice — "ticker-tape" preview. Strip line
            # breaks so the title stays single-line.
            tail = self._content[-self._PREVIEW_CHARS:].replace("\n", " ").strip()
            if char_count > self._PREVIEW_CHARS:
                tail = "…" + tail
            preview = f" {tail}"
        if self._thinking_running:
            if char_count:
                self._collapsible.title = (
                    f"{frame} Thinking ({char_count:,} chars):{preview}"
                )
            else:
                self._collapsible.title = f"{frame} Thinking…"
        else:
            # Finished — static checkmark + final preview, if any.
            if char_count:
                self._collapsible.title = (
                    f"✓ Thinking ({char_count:,} chars):{preview}"
                )
            else:
                self._collapsible.title = "✓ Thinking"

    def _stop_timers(self) -> None:
        """Cancel both animation timers. Idempotent."""
        for attr in ("_spinner_timer", "_title_timer"):
            timer = getattr(self, attr, None)
            if timer is None:
                continue
            setattr(self, attr, None)
            try:
                timer.stop()
            except Exception:  # pragma: no cover — defensive
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, delta: str) -> None:
        """Append a thinking text delta."""
        self._content += delta
        if self._body is not None:
            self._body.update(self._content)
        # Mark the title dirty so the next _flush_title tick repaints it.
        # Direct repainting here would thrash the UI on high-rate streams.
        self._title_dirty = True

    def finish(self) -> None:
        """Mark the thinking block complete. Stops spinner, shows checkmark."""
        if not self._thinking_running:
            return
        self._thinking_running = False
        self._stop_timers()
        self._repaint_title()
