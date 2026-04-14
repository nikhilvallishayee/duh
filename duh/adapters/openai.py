"""OpenAI adapter — wraps the openai Python SDK into D.U.H. events.

Supports GPT-4o, o1, o3, and any OpenAI-compatible API (vLLM, Together, etc.)
by setting base_url.

This adapter translates between:
- D.U.H. Messages → OpenAI chat completion format
- OpenAI streaming events → D.U.H. uniform events

Usage:
    from duh.adapters.openai import OpenAIProvider
    provider = OpenAIProvider(api_key="sk-...", model="gpt-4o")
    deps = Deps(call_model=provider.stream)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncGenerator

import httpx

from duh.adapters.anthropic import ParsedToolUse
from duh.kernel.backoff import with_backoff
from duh.kernel.messages import Message


class OpenAIProvider:
    """Wraps the OpenAI Python SDK to produce D.U.H. uniform events.

    Implements the ModelProvider port contract.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o",
        base_url: str | None = None,
        timeout: float = 600.0,
        max_retries: int = 2,
    ):
        import openai

        self._default_model = model
        self._client = openai.AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
            **({"base_url": base_url} if base_url else {}),
            timeout=timeout,
            max_retries=max_retries,
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
        """Stream model responses from OpenAI, yielding D.U.H. events."""
        resolved_model = model or self._default_model

        # Build messages
        api_messages = _to_openai_messages(messages, system_prompt)

        # Build tools
        api_tools = _to_openai_tools(tools) if tools else None

        # Build request kwargs
        request: dict[str, Any] = {
            "model": resolved_model,
            "messages": api_messages,
            "stream": True,
        }
        if api_tools:
            request["tools"] = api_tools
        if max_tokens:
            request["max_tokens"] = max_tokens
        if tool_choice and api_tools:
            if tool_choice == "any":
                request["tool_choice"] = "required"
            elif tool_choice == "none":
                request["tool_choice"] = "none"
            elif tool_choice == "auto":
                request["tool_choice"] = "auto"
            elif isinstance(tool_choice, str):
                request["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}

        # Stream with exponential backoff for transient errors
        async def _do_stream() -> AsyncGenerator[dict[str, Any], None]:
            response = await self._client.chat.completions.create(**request)

            # Accumulate the full message
            text_parts: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}
            finish_reason = "stop"

            try:
                async for chunk in response:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Track finish reason
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason

                    # Text content
                    if delta.content:
                        text_parts.append(delta.content)
                        yield {"type": "text_delta", "text": delta.content}

                    # Tool calls (streamed incrementally)
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls:
                                tool_calls[idx] = {
                                    "id": tc.id or "",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.id:
                                tool_calls[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls[idx]["arguments"] += tc.function.arguments

            except (ConnectionError, httpx.ReadError, asyncio.TimeoutError) as mid_err:
                # Mid-stream error — yield partial content if we have any
                if text_parts or tool_calls:
                    content_blocks: list[dict[str, Any]] = []
                    full_text = "".join(text_parts)
                    if full_text:
                        content_blocks.append({"type": "text", "text": full_text})
                    for idx in sorted(tool_calls):
                        tc = tool_calls[idx]
                        try:
                            parsed_input = json.loads(tc["arguments"])
                        except (json.JSONDecodeError, KeyError):
                            parsed_input = {}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": parsed_input,
                        })
                    yield {
                        "type": "assistant",
                        "message": Message(
                            role="assistant",
                            content=content_blocks,
                            metadata={
                                "partial": True,
                                "stop_reason": "error",
                                "usage": {},
                            },
                        ),
                    }
                yield {"type": "error", "error": f"Stream interrupted: {mid_err}"}
                return

            # Build the complete assistant message
            content_blocks: list[dict[str, Any]] = []
            full_text = "".join(text_parts)
            if full_text:
                content_blocks.append({"type": "text", "text": full_text})

            for idx in sorted(tool_calls):
                tc = tool_calls[idx]
                try:
                    parsed_input = json.loads(tc["arguments"])
                except (json.JSONDecodeError, KeyError):
                    parsed_input = {}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": parsed_input,
                })

            stop_reason = "end_turn" if finish_reason == "stop" else finish_reason
            assistant_msg = Message(
                role="assistant",
                content=content_blocks,
                metadata={
                    "stop_reason": stop_reason,
                    "usage": {},
                },
            )
            yield {"type": "assistant", "message": assistant_msg}

        try:
            async for event in with_backoff(_do_stream):
                yield event
        except Exception as e:
            error_msg = Message(
                role="assistant",
                content=[{"type": "text", "text": str(e)}],
                metadata={"is_error": True},
            )
            yield {"type": "assistant", "message": error_msg}


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------

def _to_openai_messages(
    messages: list[Any],
    system_prompt: str | list[str],
) -> list[dict[str, Any]]:
    """Convert D.U.H. messages to OpenAI chat format."""
    api_messages: list[dict[str, Any]] = []

    # System prompt
    sys_text = _build_system_text(system_prompt)
    if sys_text:
        api_messages.append({"role": "system", "content": sys_text})

    for msg in messages:
        role = msg.role if isinstance(msg, Message) else msg.get("role", "user")
        content = msg.content if isinstance(msg, Message) else msg.get("content", "")

        if role == "user":
            if isinstance(content, str):
                api_messages.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Check for tool_result blocks → convert to tool messages
                for block in content:
                    bt = block.get("type", "") if isinstance(block, dict) else getattr(block, "type", "")
                    if bt == "tool_result":
                        tool_use_id = block.get("tool_use_id", "") if isinstance(block, dict) else getattr(block, "tool_use_id", "")
                        result_content = block.get("content", "") if isinstance(block, dict) else getattr(block, "content", "")
                        api_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_use_id,
                            "content": result_content if isinstance(result_content, str) else str(result_content),
                        })
                    elif bt == "text":
                        text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                        api_messages.append({"role": "user", "content": text})

        elif role == "assistant":
            msg_dict: dict[str, Any] = {"role": "assistant"}
            if isinstance(content, str):
                msg_dict["content"] = content
            elif isinstance(content, list):
                text_parts = []
                tool_calls = []
                for block in content:
                    bt = block.get("type", "") if isinstance(block, dict) else getattr(block, "type", "")
                    if bt == "text":
                        text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                        text_parts.append(text)
                    elif bt == "tool_use":
                        name = block.get("name", "") if isinstance(block, dict) else getattr(block, "name", "")
                        bid = block.get("id", "") if isinstance(block, dict) else getattr(block, "id", "")
                        inp = block.get("input", {}) if isinstance(block, dict) else getattr(block, "input", {})
                        tool_calls.append({
                            "id": bid,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(inp),
                            },
                        })
                if text_parts:
                    msg_dict["content"] = "".join(text_parts)
                if tool_calls:
                    msg_dict["tool_calls"] = tool_calls
                    if "content" not in msg_dict:
                        msg_dict["content"] = None
            api_messages.append(msg_dict)

    return api_messages


def _build_system_text(system_prompt: str | list[str]) -> str:
    if isinstance(system_prompt, list):
        return "\n\n".join(system_prompt)
    return system_prompt


def _to_openai_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert D.U.H. tools to OpenAI function-calling format."""
    api_tools = []
    for tool in tools:
        name = getattr(tool, "name", "")
        description = getattr(tool, "description", "")
        schema = getattr(tool, "input_schema", {})
        if not name:
            continue
        api_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": schema,
            },
        })
    return api_tools
