"""ModelProvider port — how D.U.H. talks to LLMs.

Every provider (Anthropic, OpenAI, Ollama, etc.) implements this protocol.
The kernel calls `stream()` and receives a uniform event stream regardless
of which provider is behind it.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Protocol, runtime_checkable


@runtime_checkable
class ModelProvider(Protocol):
    """Abstract interface for LLM providers.

    Implementations translate between D.U.H.'s uniform event format
    and the provider's native API (REST, SDK, local inference, etc.).
    """

    async def stream(
        self,
        *,
        messages: list[Any],
        system_prompt: str | list[str] = "",
        model: str = "",
        tools: list[Any] | None = None,
        thinking: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream model responses.

        Yields events in D.U.H.'s uniform format:
        - {"type": "text_delta", "text": "..."}
        - {"type": "thinking_delta", "text": "..."}
        - {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
        - {"type": "assistant", "message": Message}
        - {"type": "error", "error": "..."}

        The provider adapter is responsible for:
        1. Translating messages to the provider's format
        2. Sending the API request (with auth, retries, etc.)
        3. Parsing the streaming response into uniform events
        4. Handling provider-specific errors gracefully
        """
        ...
        yield {}  # type: ignore  # pragma: no cover - Protocol stub
