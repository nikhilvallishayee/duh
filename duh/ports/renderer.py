"""Renderer port -- how D.U.H. displays output to the user.

The kernel yields events. Renderers consume them and produce terminal output.
The kernel never calls print(). It never imports a rendering library.
The UI is a port, not baked in.

See ADR-011 for the full rationale.

Three tiers:
    Bare  -- print() / sys.stdout.write()
    Rich  -- Rich library (styled panels, spinners, markdown)
    Full  -- textual or custom (future)

    renderer = BareRenderer()
    async for event in engine.run(prompt):
        renderer.handle(event)
    renderer.finish()
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Renderer(Protocol):
    """Consumes engine events and produces terminal output.

    Every rendering tier (bare, rich, full TUI) implements this protocol.
    The CLI selects the appropriate renderer based on capabilities.
    """

    def render_text_delta(self, text: str) -> None:
        """Streaming text from the model (character by character)."""
        ...

    def render_tool_use(self, name: str, input: dict[str, Any]) -> None:
        """A tool call is about to execute."""
        ...

    def render_tool_result(self, output: str, is_error: bool) -> None:
        """A tool call returned a result."""
        ...

    def render_thinking(self, text: str) -> None:
        """Model thinking/reasoning text (usually dimmed)."""
        ...

    def render_error(self, error: str) -> None:
        """An error occurred."""
        ...

    def render_permission_request(
        self, tool_name: str, input: dict[str, Any]
    ) -> None:
        """Display a permission request (informational; approval is separate)."""
        ...

    def finish(self) -> None:
        """Called when the engine run is complete."""
        ...

    def handle(self, event: dict[str, Any]) -> None:
        """Dispatch an engine event to the appropriate render method.

        Default implementation routes by event['type'].
        """
        ...
