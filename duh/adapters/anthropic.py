"""Anthropic adapter — wraps the anthropic Python SDK into D.U.H. events.

This adapter translates between:
- D.U.H. Messages → Anthropic API format (role/content dicts)
- Anthropic streaming events → D.U.H. uniform events
- Anthropic tool schemas → D.U.H. tool format

Usage:
    from duh.adapters.anthropic import AnthropicProvider
    provider = AnthropicProvider(api_key="sk-ant-...")
    deps = Deps(call_model=provider.stream)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import httpx

from duh.kernel.backoff import with_backoff
from duh.kernel.messages import Message
from duh.kernel.untrusted import TaintSource, UntrustedStr


def _wrap_model_output(text: str) -> UntrustedStr:
    """Tag Anthropic provider output as MODEL_OUTPUT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.MODEL_OUTPUT)


@dataclass
class ParsedToolUse:
    """Canonical parsed representation of a tool_use JSON block.

    All provider adapters must produce identical ParsedToolUse values
    for the same input (ADR-054 §9, workstream 7.8).
    """

    id: str
    name: str
    input: dict  # type: ignore[type-arg]


class AnthropicProvider:
    """Wraps the Anthropic Python SDK to produce D.U.H. uniform events.

    Implements the ModelProvider port contract.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        max_retries: int = 2,
        timeout: float = 600.0,
        base_url: str | None = None,
    ):
        import anthropic

        self._default_model = model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            max_retries=max_retries,
            timeout=timeout,
            **({"base_url": base_url} if base_url else {}),
        )

    @classmethod
    def _parse_tool_use_block(cls, block: dict[str, Any]) -> ParsedToolUse:
        """Parse a raw tool_use JSON block into a ParsedToolUse.

        All providers must agree on the output for the same input.
        """
        return ParsedToolUse(
            id=str(block.get("id", "")),
            name=str(block.get("name", "")),
            input=block.get("input", {}),
        )

    async def stream(
        self,
        *,
        messages: list[Any],
        system_prompt: str | list[str] = "",
        model: str = "",
        tools: list[Any] | None = None,
        thinking: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream model responses, yielding D.U.H. uniform events."""
        resolved_model = model or self._default_model
        resolved_max_tokens = max_tokens or _default_max_tokens(resolved_model)

        # Build API params
        api_messages = _to_api_messages(messages)
        params: dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": resolved_max_tokens,
            "messages": api_messages,
        }

        # System prompt — use cache_control for prompt caching (ADR-061 §1)
        system_text = _build_system_text(system_prompt)
        if system_text:
            params["system"] = _build_cached_system(system_text)

        # Message prefix caching (ADR-061 §2) — mark the boundary between
        # old messages (stable prefix) and the new user input so the API
        # can cache the entire conversation prefix across turns.
        _add_prefix_cache_marker(api_messages)

        # Tools
        if tools:
            params["tools"] = _to_api_tools(tools)

        # Thinking
        if thinking:
            thinking_type = thinking.get("type", "disabled")
            if thinking_type in ("adaptive", "enabled"):
                supports_adaptive = any(
                    tag in resolved_model
                    for tag in ("opus-4-6", "sonnet-4-6")
                )
                if supports_adaptive:
                    params["thinking"] = {"type": "adaptive"}
                elif thinking_type == "enabled":
                    budget = thinking.get("budget_tokens", resolved_max_tokens - 1)
                    params["thinking"] = {"type": "enabled", "budget_tokens": budget}

        # Tool choice — Anthropic supports natively
        if tool_choice and tools:
            if isinstance(tool_choice, dict):
                params["tool_choice"] = tool_choice
            elif tool_choice == "none":
                # Don't send tools at all — simplest way to prevent tool use
                del params["tools"]
            elif tool_choice == "auto":
                params["tool_choice"] = {"type": "auto"}
            elif tool_choice == "any":
                params["tool_choice"] = {"type": "any"}
            else:
                # Assume it's a tool name — force that specific tool
                params["tool_choice"] = {"type": "tool", "name": tool_choice}

        # Stream with exponential backoff for transient errors
        content_blocks: list[Any] = []
        accumulated_text: list[str] = []
        usage: dict[str, int] = {}

        async def _do_stream() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal content_blocks, accumulated_text, usage
            # Reset accumulators on each retry attempt
            content_blocks = []
            accumulated_text = []
            usage = {}

            async with self._client.messages.stream(**params) as stream:
                try:
                    async for event in stream:
                        event_type = getattr(event, "type", "")

                        if event_type == "content_block_start":
                            block = getattr(event, "content_block", None)
                            if block:
                                content_blocks.append(block)
                            yield {
                                "type": "content_block_start",
                                "index": getattr(event, "index", len(content_blocks) - 1),
                                "content_block": _block_to_dict(block) if block else {},
                            }

                        elif event_type == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta:
                                delta_type = getattr(delta, "type", "")
                                if delta_type == "text_delta":
                                    text = getattr(delta, "text", "")
                                    accumulated_text.append(text)
                                    yield {"type": "text_delta", "text": text}
                                elif delta_type == "thinking_delta":
                                    yield {"type": "thinking_delta", "text": getattr(delta, "thinking", "")}
                                elif delta_type == "input_json_delta":
                                    yield {"type": "input_json_delta", "partial_json": getattr(delta, "partial_json", "")}
                                elif delta_type == "signature_delta":
                                    pass  # Ignore signature deltas

                        elif event_type == "content_block_stop":
                            yield {
                                "type": "content_block_stop",
                                "index": getattr(event, "index", 0),
                            }

                        elif event_type == "message_start":
                            msg = getattr(event, "message", None)
                            if msg:
                                msg_usage = getattr(msg, "usage", None)
                                if msg_usage:
                                    usage = {
                                        "input_tokens": getattr(msg_usage, "input_tokens", 0),
                                        "output_tokens": getattr(msg_usage, "output_tokens", 0),
                                        # ADR-061 Phase 3: prompt cache tracking
                                        "cache_creation_input_tokens": getattr(msg_usage, "cache_creation_input_tokens", 0) or 0,
                                        "cache_read_input_tokens": getattr(msg_usage, "cache_read_input_tokens", 0) or 0,
                                    }

                        elif event_type == "message_delta":
                            delta_usage = getattr(event, "usage", None)
                            if delta_usage:
                                usage["output_tokens"] = getattr(delta_usage, "output_tokens", 0)

                except (ConnectionError, httpx.ReadError, asyncio.TimeoutError) as mid_err:
                    # Mid-stream error — yield partial content if we have any
                    partial_text = "".join(accumulated_text)
                    if partial_text:
                        yield {
                            "type": "assistant",
                            "message": Message(
                                role="assistant",
                                content=[{"type": "text", "text": partial_text}],
                                metadata={
                                    "partial": True,
                                    "model": resolved_model,
                                    "stop_reason": "error",
                                    "usage": usage,
                                },
                            ),
                        }
                    yield {"type": "error", "error": f"Stream interrupted: {mid_err}"}
                    return

                # Build final assistant message
                final = await stream.get_final_message()
                content = _normalize_content(list(final.content)) if final else []

                assistant_msg = Message(
                    role="assistant",
                    content=content,
                    id=getattr(final, "id", ""),
                    metadata={
                        "model": getattr(final, "model", resolved_model),
                        "stop_reason": getattr(final, "stop_reason", "end_turn"),
                        "usage": usage,
                    },
                )
                yield {"type": "assistant", "message": assistant_msg}

        try:
            async for event in with_backoff(_do_stream):
                yield event
        except Exception as e:
            error_text = str(e)
            # Yield error as an assistant message with error content
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": f"API Error: {error_text}"}],
                    metadata={"is_error": True, "error": error_text},
                ),
            }


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

def _to_api_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Translate D.U.H. Messages → Anthropic API format."""
    result = []
    for msg in messages:
        if isinstance(msg, Message):
            content = msg.content
            if isinstance(content, list):
                # Convert dataclass blocks to dicts
                api_content = []
                for block in content:
                    if isinstance(block, dict):
                        # Strip to API-allowed fields per block type
                        api_content.append(_sanitize_block(block))
                    elif hasattr(block, "__dataclass_fields__"):
                        from dataclasses import asdict
                        api_content.append(_sanitize_block(asdict(block)))
                    else:
                        api_content.append({"type": "text", "text": str(block)})
                result.append({"role": msg.role, "content": api_content})
            else:
                result.append({"role": msg.role, "content": str(content)})
        elif isinstance(msg, dict):
            result.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        else:
            result.append({"role": "user", "content": str(msg)})
    return result


def _sanitize_block(block: dict[str, Any]) -> dict[str, Any]:
    """Strip non-API fields from content blocks.

    ``cache_control`` is always allowed — the Anthropic API uses it for
    prompt caching (ADR-061).
    """
    ALLOWED = {
        "text": {"type", "text", "cache_control"},
        "tool_use": {"type", "id", "name", "input", "cache_control"},
        "tool_result": {"type", "tool_use_id", "content", "is_error", "cache_control"},
        "thinking": {"type", "thinking", "signature"},
    }
    bt = block.get("type", "")
    allowed = ALLOWED.get(bt)
    if allowed:
        return {k: v for k, v in block.items() if k in allowed}
    return block


def _to_api_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Translate D.U.H. Tool objects → Anthropic API tool schemas."""
    result = []
    for tool in tools:
        if isinstance(tool, dict):
            result.append(tool)
        elif hasattr(tool, "name") and hasattr(tool, "input_schema"):
            desc = getattr(tool, "description", "")
            if callable(desc):
                desc = desc()
            result.append({
                "name": tool.name,
                "description": str(desc) if desc else "",
                "input_schema": tool.input_schema,
            })
    return result


def _build_system_text(system_prompt: str | list[str]) -> str:
    """Build system prompt text."""
    if isinstance(system_prompt, list):
        return "\n\n".join(p for p in system_prompt if p)
    return system_prompt


def _build_cached_system(system_text: str) -> list[dict[str, Any]]:
    """Wrap system prompt text with cache_control for prompt caching (ADR-061).

    Returns a structured content block list that the Anthropic API accepts
    for the ``system`` parameter. The ``cache_control`` marker tells the API
    to cache this block across turns, saving ~90% on repeated system prompts.
    """
    return [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _add_prefix_cache_marker(api_messages: list[dict[str, Any]]) -> None:
    """Mark the conversation prefix boundary for caching (ADR-061 §2).

    Adds ``cache_control`` to the last content block of the second-to-last
    message (i.e. the last message before the newest user input). This tells
    the API that everything up to this point is stable and can be cached.

    Requires at least 2 messages (a prior turn + the new user input).
    Mutates ``api_messages`` in place.
    """
    if len(api_messages) < 2:
        return

    # The second-to-last message is the end of the "prefix" — mark it.
    prefix_msg = api_messages[-2]
    content = prefix_msg.get("content")

    if isinstance(content, list) and content:
        # Mark the last content block in the prefix message.
        last_block = content[-1]
        if isinstance(last_block, dict):
            last_block["cache_control"] = {"type": "ephemeral"}
    elif isinstance(content, str) and content:
        # Content is a plain string — convert to structured form so we can
        # attach cache_control.
        prefix_msg["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]


def _default_max_tokens(model: str) -> int:
    """Get default max tokens for a model."""
    if "opus" in model:
        return 16384
    if "haiku" in model:
        return 8192
    return 16384  # sonnet default


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Convert an SDK content block object to a dict."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    d: dict[str, Any] = {"type": getattr(block, "type", "unknown")}
    for attr in ("text", "thinking", "id", "name", "input", "signature"):
        val = getattr(block, attr, None)
        if val is not None:
            d[attr] = val
    return d


def _normalize_content(blocks: list[Any]) -> list[dict[str, Any]]:
    """Normalize SDK content blocks to dicts."""
    return [_block_to_dict(b) for b in blocks]
