"""Renderer classes for the interactive REPL (issue #26).

Extracted from :mod:`duh.cli.repl` to keep that module focused on the REPL
loop and slash-command dispatch while each renderer lives in its own file.

Two renderers are provided:

* :class:`PlainRenderer` — raw ANSI escape codes, no third-party deps.
* :class:`RichRenderer` — uses the ``rich`` library for panels, markdown,
  syntax highlighting, spinners, and a status bar (ADR-011 Tier 1).

The public module :mod:`duh.cli.repl` re-exports these under their legacy
``_PlainRenderer`` / ``_RichRenderer`` aliases so existing tests and call-sites
continue to work unchanged.

Behaviour of both renderers is identical to the pre-extraction code — this is
a pure file-level split.
"""

from __future__ import annotations

import sys
from typing import Any

# Shared prompt string. Kept here so the renderers are self-contained; the
# REPL also imports it from this module via the legacy name in ``repl.py``.
PROMPT = "\033[1;36mduh>\033[0m "  # bold cyan


# ---------------------------------------------------------------------------
# Optional rich dependency — graceful fallback when absent.
# ---------------------------------------------------------------------------

HAS_RICH = False
try:
    from rich.console import Console
    from rich.markdown import Markdown as RichMarkdown
    from rich.panel import Panel
    from rich.spinner import Spinner as RichSpinner  # noqa: F401 (kept for parity)
    from rich.syntax import Syntax as RichSyntax  # noqa: F401 (kept for parity)
    from rich.text import Text
    from rich.theme import Theme
    HAS_RICH = True
except ImportError:
    pass


class PlainRenderer:
    """Fallback renderer that uses raw ANSI escape codes."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._buf: list[str] = []  # accumulates text_delta chunks

    # -- prompt --------------------------------------------------------
    @staticmethod
    def prompt() -> str:
        return PROMPT

    # -- streaming text ------------------------------------------------
    def text_delta(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()
        self._buf.append(text)

    # -- markdown flush (no-op for plain) ------------------------------
    def flush_response(self) -> None:
        self._buf.clear()

    # -- thinking ------------------------------------------------------
    def thinking_delta(self, text: str) -> None:
        if self.debug:
            sys.stderr.write(f"\033[2;3m{text}\033[0m")
            sys.stderr.flush()

    # -- tool use & results --------------------------------------------
    def tool_use(self, name: str, inp: dict[str, Any]) -> None:
        summary = ", ".join(f"{k}={v!r}" for k, v in list(inp.items())[:2])
        sys.stderr.write(f"  \033[33m> {name}\033[0m({summary})\n")
        sys.stderr.flush()

    def tool_result(self, output: str, is_error: bool) -> None:
        if is_error:
            sys.stderr.write(f"  \033[31m! {output[:200]}\033[0m\n")
        elif self.debug:
            sys.stderr.write(f"  \033[32m< {output[:100]}\033[0m\n")

    # -- errors --------------------------------------------------------
    def error(self, hint: str) -> None:
        sys.stderr.write(f"\n\033[31mError: {hint}\033[0m\n")

    # -- end of turn separator -----------------------------------------
    def turn_end(self) -> None:
        sys.stdout.write("\n\n")

    # -- banner --------------------------------------------------------
    def banner(self, model: str) -> None:
        sys.stdout.write(
            f"D.U.H. interactive mode ({model}). "
            "Type /help for commands, /exit or Ctrl-D to quit.\n\n"
        )

    # -- stats update (no-op for plain) --------------------------------
    def update_stats(
        self,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        pass

    # -- status bar (no-op for plain) ----------------------------------
    def status_bar(self, model: str, turns: int) -> None:
        pass


class RichRenderer:
    """Renderer that uses the Rich library for styled terminal output.

    Tier 1 features (ADR-011):
    - Markdown rendering: assistant text is re-rendered as Rich Markdown after
      streaming, so headers, code blocks, lists, bold, and tables are styled.
    - Syntax highlighting: fenced code blocks get language-aware highlighting
      via Rich's built-in Markdown renderer (which uses ``rich.syntax.Syntax``
      internally for each fenced block, auto-detecting from the fence tag).
    - Collapsible tool output panels: successful tool results show a compact
      summary Panel (green, always visible); errors show a full Panel (red).
    - Progress spinners: a spinner character appears on stderr while a tool is
      running; cleared as soon as the result arrives.
    - Enhanced status bar: shows model, turn, cumulative token counts, and
      estimated cost so the user can track session economics at a glance.
    """

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._buf: list[str] = []
        # Track active tool name so the spinner label is meaningful.
        self._active_tool: str | None = None
        # Accumulated token counts and cost (updated via update_stats).
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._cost: float = 0.0
        theme = Theme({
            "tool": "bold yellow",
            "tool.ok": "green",
            "tool.err": "bold red",
            "thinking": "dim italic",
            "err": "bold red",
            "status": "dim",
        })
        self._console = Console(theme=theme, stderr=False)
        self._err_console = Console(theme=theme, stderr=True)

    # -- prompt --------------------------------------------------------
    @staticmethod
    def prompt() -> str:
        # Rich can style prompts, but readline integration is tricky.
        # We keep the ANSI prompt so readline calculates width correctly.
        return PROMPT

    # -- streaming text ------------------------------------------------
    def text_delta(self, text: str) -> None:
        # Stream tokens to stdout immediately so the user sees them live.
        sys.stdout.write(text)
        sys.stdout.flush()
        self._buf.append(text)

    # -- markdown flush ------------------------------------------------
    def flush_response(self) -> None:
        """Re-render the full response as Rich Markdown after streaming.

        Rich's Markdown renderer handles headers, bold/italic, fenced code
        blocks with syntax highlighting (auto-detected from the fence tag,
        e.g. ```python, ```bash, ```json), ordered/unordered lists, GFM
        tables, and blockquotes.
        """
        full = "".join(self._buf)
        self._buf.clear()
        if not full.strip():
            return
        # Heuristic: only use Markdown renderer when content looks like it
        # has markdown constructs (headers, code fences, lists, bold, etc.)
        md_indicators = ("```", "##", "**", "* ", "- ", "1. ", "> ", "| ")
        if any(ind in full for ind in md_indicators):
            # Move cursor up and overwrite the raw streamed text.
            # Count how many lines were streamed.
            lines = full.count("\n") + 1
            # Clear those lines
            sys.stdout.write(f"\033[{lines}A\033[J")
            sys.stdout.flush()
            self._console.print(RichMarkdown(full))

    # -- thinking ------------------------------------------------------
    def thinking_delta(self, text: str) -> None:
        if self.debug:
            self._err_console.print(Text(text, style="thinking"), end="")

    # -- tool use & results --------------------------------------------
    def tool_use(self, name: str, inp: dict[str, Any]) -> None:
        """Show a tool-call header and start a progress spinner on stderr."""
        self._active_tool = name
        summary = ", ".join(f"{k}={v!r}" for k, v in list(inp.items())[:2])
        self._err_console.print(
            Text.assemble(
                ("  > ", "tool"),
                (name, "bold yellow"),
                (f"({summary})", ""),
            )
        )
        # Inline spinner: a single overwritable line on stderr.
        sys.stderr.write(f"\r  \033[33m⠋\033[0m running {name}…")
        sys.stderr.flush()

    def tool_result(self, output: str, is_error: bool) -> None:
        """Clear the spinner and render the tool result in a Panel.

        - Errors: full Panel, red border (always shown).
        - Success: compact one-line summary Panel, green border (always shown).
          Full output additionally shown in debug mode.
        """
        # Clear the spinner line.
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()
        self._active_tool = None

        if is_error:
            self._err_console.print(
                Panel(
                    output[:300],
                    title="[bold red]tool error[/bold red]",
                    border_style="tool.err",
                    expand=False,
                )
            )
        else:
            # Always show a compact summary so the user sees the result.
            first_line = output.split("\n", 1)[0][:120] if output else "(empty)"
            summary_text = first_line.strip() or f"({len(output)} chars)"
            self._err_console.print(
                Panel(
                    Text(summary_text, style="tool.ok"),
                    title="[green]tool ok[/green]",
                    border_style="tool.ok",
                    expand=False,
                )
            )
            if self.debug and output and len(output) > len(summary_text):
                self._err_console.print(
                    Panel(
                        output[:500],
                        title="[green]tool output (full)[/green]",
                        border_style="tool.ok",
                        expand=False,
                    )
                )

    # -- stats update (called by REPL after each turn) -----------------
    def update_stats(
        self,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """Update running token and cost totals for the status bar."""
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._cost = cost

    # -- errors --------------------------------------------------------
    def error(self, hint: str) -> None:
        # Clear any active spinner before showing the error.
        if self._active_tool is not None:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
            self._active_tool = None
        self._err_console.print(
            Panel(hint, title="Error", border_style="err", expand=False)
        )

    # -- end of turn separator -----------------------------------------
    def turn_end(self) -> None:
        # Clear any leftover spinner.
        if self._active_tool is not None:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
            self._active_tool = None
        sys.stdout.write("\n\n")

    # -- banner --------------------------------------------------------
    def banner(self, model: str) -> None:
        self._console.print(
            Panel(
                f"[bold cyan]D.U.H.[/bold cyan] interactive mode\n"
                f"Model: [bold]{model}[/bold]  |  "
                "Type [bold]/help[/bold] for commands, "
                "[bold]/exit[/bold] or [bold]Ctrl-D[/bold] to quit.",
                border_style="cyan",
                expand=False,
            )
        )
        self._console.print()

    # -- status bar ----------------------------------------------------
    def status_bar(self, model: str, turns: int) -> None:
        """Render a status bar with model, turn, token counts, and cost."""
        tok_str = (
            f"  in={self._input_tokens:,} out={self._output_tokens:,}"
            if (self._input_tokens or self._output_tokens)
            else ""
        )
        cost_str = f"  ${self._cost:.4f}" if self._cost else ""
        self._err_console.print(
            Text(
                f"  [{model}] turn {turns}{tok_str}{cost_str}",
                style="status",
            )
        )


__all__ = [
    "PROMPT",
    "HAS_RICH",
    "PlainRenderer",
    "RichRenderer",
]
