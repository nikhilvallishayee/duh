"""Renderer adapters -- Bare, Rich, and JSON rendering tiers.

See ADR-011 for the full rationale.

BareRenderer:  print/write, works everywhere, no dependencies.
RichRenderer:  uses the ``rich`` library for styled output (optional dep).
JsonRenderer:  machine-readable JSON to stdout.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BareRenderer:
    """Tier 0: plain text output using only print/write.

    Text deltas go to stdout (streaming). Everything else goes to stderr.
    Works on any terminal, no dependencies.
    """

    _had_output: bool = field(default=False, init=False, repr=False)
    debug: bool = False

    def render_text_delta(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()
        self._had_output = True

    def render_tool_use(self, name: str, input: dict[str, Any]) -> None:
        summary = ", ".join(
            f"{k}={v!r}" for k, v in list(input.items())[:2]
        )
        sys.stderr.write(f"  \033[33m> {name}\033[0m({summary})\n")
        sys.stderr.flush()

    def render_tool_result(self, output: str, is_error: bool) -> None:
        if is_error:
            sys.stderr.write(f"  \033[31m! {output[:200]}\033[0m\n")
        elif self.debug:
            sys.stderr.write(f"  \033[32m< {output[:100]}\033[0m\n")

    def render_thinking(self, text: str) -> None:
        if self.debug:
            sys.stderr.write(f"\033[2;3m{text}\033[0m")
            sys.stderr.flush()

    def render_error(self, error: str) -> None:
        sys.stderr.write(f"\n\033[31mError: {error}\033[0m\n")

    def render_permission_request(
        self, tool_name: str, input: dict[str, Any]
    ) -> None:
        summary = ", ".join(
            f"{k}={v!r}" for k, v in list(input.items())[:2]
        )
        sys.stderr.write(
            f"  \033[33m? Permission needed: {tool_name}\033[0m({summary})\n"
        )

    def finish(self) -> None:
        if self._had_output:
            print()  # final newline after streaming

    def handle(self, event: dict[str, Any]) -> None:
        """Dispatch an engine event to the appropriate render method."""
        t = event.get("type", "")
        if t == "text_delta":
            self.render_text_delta(event.get("text", ""))
        elif t == "tool_use":
            self.render_tool_use(
                event.get("name", "?"), event.get("input", {})
            )
        elif t == "tool_result":
            self.render_tool_result(
                str(event.get("output", "")), event.get("is_error", False)
            )
        elif t == "thinking_delta":
            self.render_thinking(event.get("text", ""))
        elif t == "error":
            self.render_error(event.get("error", "unknown"))


class RichRenderer:
    """Tier 1: Rich-styled output.

    Requires the ``rich`` package.  Falls back gracefully to BareRenderer
    behaviour (via ``select_renderer``) when Rich is not installed.

    Tier 1 features (ADR-011):
    - Markdown rendering with syntax-highlighted code blocks (via Rich
      Markdown component, which uses ``rich.syntax.Syntax`` internally).
    - Collapsible tool-result Panels: errors in red, success in green.
    - Progress spinner on stderr while a tool is executing.
    - Enhanced status bar showing model, tokens, and cost.
    """

    def __init__(self, debug: bool = False):
        from rich.console import Console
        from rich.theme import Theme

        self.debug = debug
        self._buf: list[str] = []
        self._active_tool: str | None = None
        self._had_output = False

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

    # -- streaming text ------------------------------------------------
    def render_text_delta(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()
        self._buf.append(text)
        self._had_output = True

    # -- markdown flush ------------------------------------------------
    def _flush_markdown(self) -> None:
        """Re-render buffered text as Rich Markdown.

        Called internally after streaming ends.  Rich Markdown handles
        headers, fenced code blocks (with per-language syntax highlighting
        via ``rich.syntax.Syntax``), lists, GFM tables, and blockquotes.
        """
        from rich.markdown import Markdown as RichMarkdown

        full = "".join(self._buf)
        self._buf.clear()
        if not full.strip():
            return
        md_indicators = ("```", "##", "**", "* ", "- ", "1. ", "> ", "| ")
        if any(ind in full for ind in md_indicators):
            lines = full.count("\n") + 1
            sys.stdout.write(f"\033[{lines}A\033[J")
            sys.stdout.flush()
            self._console.print(RichMarkdown(full))

    # -- tool use & results --------------------------------------------
    def render_tool_use(self, name: str, input: dict[str, Any]) -> None:
        """Display tool call and start a spinner on stderr."""
        from rich.text import Text

        self._active_tool = name
        summary = ", ".join(f"{k}={v!r}" for k, v in list(input.items())[:2])
        self._err_console.print(
            Text.assemble(
                ("  > ", "tool"),
                (name, "bold yellow"),
                (f"({summary})", ""),
            )
        )
        sys.stderr.write(f"\r  \033[33m⠋\033[0m running {name}…")
        sys.stderr.flush()

    def render_tool_result(self, output: str, is_error: bool) -> None:
        """Clear spinner and show result Panel."""
        from rich.panel import Panel
        from rich.text import Text

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

    # -- thinking ------------------------------------------------------
    def render_thinking(self, text: str) -> None:
        if self.debug:
            from rich.text import Text
            self._err_console.print(Text(text, style="thinking"), end="")

    # -- errors --------------------------------------------------------
    def render_error(self, error: str) -> None:
        from rich.panel import Panel

        if self._active_tool is not None:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
            self._active_tool = None
        self._err_console.print(
            Panel(error, title="Error", border_style="err", expand=False)
        )

    # -- permission prompt ---------------------------------------------
    def render_permission_request(
        self, tool_name: str, input: dict[str, Any]
    ) -> None:
        from rich.panel import Panel

        summary = ", ".join(f"{k}={v!r}" for k, v in list(input.items())[:2])
        self._err_console.print(
            Panel(
                f"Tool: [bold yellow]{tool_name}[/bold yellow]\n"
                f"Input: {summary}",
                title="[yellow]Permission Request[/yellow]",
                border_style="yellow",
                expand=False,
            )
        )

    # -- finish --------------------------------------------------------
    def finish(self) -> None:
        """Flush buffered Markdown and emit a trailing newline."""
        # Clear any leftover spinner.
        if self._active_tool is not None:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
            self._active_tool = None
        self._flush_markdown()
        if self._had_output:
            sys.stdout.write("\n")

    # -- event dispatch ------------------------------------------------
    def handle(self, event: dict[str, Any]) -> None:
        """Dispatch an engine event to the appropriate render method."""
        t = event.get("type", "")
        if t == "text_delta":
            self.render_text_delta(event.get("text", ""))
        elif t == "tool_use":
            self.render_tool_use(
                event.get("name", "?"), event.get("input", {})
            )
        elif t == "tool_result":
            self.render_tool_result(
                str(event.get("output", "")), event.get("is_error", False)
            )
        elif t == "thinking_delta":
            self.render_thinking(event.get("text", ""))
        elif t == "error":
            self.render_error(event.get("error", "unknown"))


@dataclass
class JsonRenderer:
    """Machine-readable JSON output.

    Collects all events and writes them as a JSON array on finish().
    """

    _events: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    def render_text_delta(self, text: str) -> None:
        self._events.append({"type": "text_delta", "text": text})

    def render_tool_use(self, name: str, input: dict[str, Any]) -> None:
        self._events.append({"type": "tool_use", "name": name, "input": input})

    def render_tool_result(self, output: str, is_error: bool) -> None:
        self._events.append(
            {"type": "tool_result", "output": output, "is_error": is_error}
        )

    def render_thinking(self, text: str) -> None:
        self._events.append({"type": "thinking", "text": text})

    def render_error(self, error: str) -> None:
        self._events.append({"type": "error", "error": error})

    def render_permission_request(
        self, tool_name: str, input: dict[str, Any]
    ) -> None:
        self._events.append(
            {"type": "permission_request", "tool": tool_name, "input": input}
        )

    def finish(self) -> None:
        sys.stdout.write(json.dumps(self._events, indent=2, default=str))
        sys.stdout.write("\n")

    def handle(self, event: dict[str, Any]) -> None:
        """Dispatch an engine event to the appropriate render method."""
        t = event.get("type", "")
        if t == "text_delta":
            self.render_text_delta(event.get("text", ""))
        elif t == "tool_use":
            self.render_tool_use(
                event.get("name", "?"), event.get("input", {})
            )
        elif t == "tool_result":
            self.render_tool_result(
                str(event.get("output", "")), event.get("is_error", False)
            )
        elif t == "thinking_delta":
            self.render_thinking(event.get("text", ""))
        elif t == "error":
            self.render_error(event.get("error", "unknown"))


def select_renderer(
    *,
    output_format: str = "text",
    debug: bool = False,
) -> BareRenderer | RichRenderer | JsonRenderer:
    """Select the best available renderer.

    Priority:
    1. JSON format requested -> JsonRenderer
    2. Rich installed + TTY  -> RichRenderer
    3. Fallback              -> BareRenderer
    """
    if output_format == "json":
        return JsonRenderer()
    try:
        import rich  # noqa: F401
        if sys.stdout.isatty():
            return RichRenderer(debug=debug)
    except ImportError:
        pass
    return BareRenderer(debug=debug)
