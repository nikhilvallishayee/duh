"""Renderer adapters -- Bare and Rich rendering tiers.

See ADR-011 for the full rationale.

BareRenderer: print/write, works everywhere, no dependencies.
RichRenderer: uses the ``rich`` library for styled output (optional dep).
JsonRenderer: machine-readable JSON to stdout.
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
) -> BareRenderer | JsonRenderer:
    """Select the best available renderer.

    Priority:
    1. JSON format requested -> JsonRenderer
    2. Rich installed + TTY -> (future) RichRenderer
    3. Fallback -> BareRenderer
    """
    if output_format == "json":
        return JsonRenderer()
    # Future: check for rich availability and TTY
    # try:
    #     import rich  # noqa: F401
    #     if sys.stdout.isatty():
    #         return RichRenderer(debug=debug)
    # except ImportError:
    #     pass
    return BareRenderer(debug=debug)
