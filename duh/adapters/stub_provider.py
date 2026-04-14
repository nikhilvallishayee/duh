"""StubProvider — a deterministic provider for tests and offline runs.

Activated by setting the ``DUH_STUB_PROVIDER=1`` environment variable.  It
short-circuits the entire provider stack with a canned single-turn response
so subprocess-based integration tests can exercise the full CLI/runner/REPL
path without needing real API credentials.

It is *not* meant for production use.  ``resolve_provider_name`` checks for
the env var first; if set, it returns ``"stub"`` and ``build_model_backend``
wires up this provider regardless of any other auth state.

The response format mimics what a normal provider would yield for a single
text-only assistant turn — a ``"text_delta"`` followed by an ``"assistant"``
event with a ``Message`` and a ``"done"`` event.  That is enough to satisfy
the kernel loop, the SDK NDJSON runner, and the print-mode runner.
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator

from duh.kernel.messages import Message


STUB_PROVIDER_ENV = "DUH_STUB_PROVIDER"
STUB_RESPONSE_ENV = "DUH_STUB_RESPONSE"
DEFAULT_STUB_RESPONSE = "stub-ok"


def stub_provider_enabled() -> bool:
    """Return True if the stub provider is enabled via env var."""
    return os.environ.get(STUB_PROVIDER_ENV, "") == "1"


def stub_response_text() -> str:
    """Return the canned response text (env-overridable)."""
    return os.environ.get(STUB_RESPONSE_ENV, DEFAULT_STUB_RESPONSE)


class StubProvider:
    """Deterministic provider for tests / offline runs."""

    def __init__(self, model: str = "stub-model") -> None:
        self._model = model

    async def stream(
        self,
        *,
        messages: list[Any],
        system_prompt: str | list[str] = "",
        model: str = "",
        tools: list[Any] | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        text = stub_response_text()
        yield {"type": "text_delta", "text": text}
        yield {
            "type": "assistant",
            "message": Message(
                role="assistant",
                content=[{"type": "text", "text": text}],
                metadata={"stop_reason": "end_turn", "model": model or self._model},
            ),
        }
        yield {"type": "done", "stop_reason": "end_turn", "turns": 1}
