"""litellm adapter -- wraps litellm.acompletion() into D.U.H. events.

litellm provides a unified interface to 100+ LLM providers (Gemini, Bedrock,
Azure, Groq, Together, Fireworks, Mistral, Cohere, etc.) via a single
acompletion() call that returns OpenAI-compatible streaming chunks.

This adapter translates between:
- D.U.H. Messages -> litellm/OpenAI chat completion format
- litellm streaming events -> D.U.H. uniform events

Usage:
    from duh.adapters.litellm_provider import LiteLLMProvider
    provider = LiteLLMProvider(model="gemini/gemini-2.5-flash")
    deps = Deps(call_model=provider.stream)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import httpx

from duh.adapters.anthropic import ParsedToolUse
from duh.kernel.messages import Message
from duh.kernel.untrusted import TaintSource, UntrustedStr


def _wrap_model_output(text: str) -> UntrustedStr:
    """Tag litellm provider output as MODEL_OUTPUT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.MODEL_OUTPUT)


class LiteLLMProvider:
    """Wraps litellm.acompletion() to produce D.U.H. uniform events.

    Implements the ModelProvider port contract. litellm handles auth
    via environment variables per provider (GEMINI_API_KEY, AWS_ACCESS_KEY_ID,
    AZURE_API_KEY, etc.).
    """

    def __init__(
        self,
        model: str = "gemini/gemini-2.5-flash",
        timeout: float = 600.0,
        **litellm_kwargs: Any,
    ):
        self._default_model = model
        self._timeout = timeout
        self._litellm_kwargs = litellm_kwargs

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
        """Stream model responses via litellm, yielding D.U.H. events."""
        try:
            import litellm
        except ImportError:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{
                        "type": "text",
                        "text": "litellm is not installed. Install with: pip install duh-cli[litellm]",
                    }],
                    metadata={"is_error": True, "error": "litellm not installed"},
                ),
            }
            return

        resolved_model = model or self._default_model

        # Build messages in OpenAI format
        api_messages = _to_litellm_messages(messages, system_prompt)

        # Build tools in OpenAI function-calling format
        api_tools = _to_litellm_tools(tools) if tools else None

        # Build request kwargs
        request: dict[str, Any] = {
            "model": resolved_model,
            "messages": api_messages,
            "stream": True,
            "timeout": self._timeout,
        }
        if api_tools:
            request["tools"] = api_tools
        if max_tokens:
            request["max_tokens"] = max_tokens

        # Tool choice translation
        if tool_choice and api_tools:
            if tool_choice == "any":
                request["tool_choice"] = "required"
            elif tool_choice == "none":
                request["tool_choice"] = "none"
            elif tool_choice == "auto":
                request["tool_choice"] = "auto"
            elif isinstance(tool_choice, str):
                request["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice},
                }

        # Merge any extra litellm kwargs from constructor
        request.update(self._litellm_kwargs)

        # Stream the response
        text_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}

        try:
            response = await litellm.acompletion(**request)

            try:
                async for chunk in response:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue

                    choice = choices[0]
                    delta = getattr(choice, "delta", None)
                    if not delta:
                        continue

                    # Track finish reason
                    fr = getattr(choice, "finish_reason", None)
                    if fr:
                        finish_reason = fr

                    # Text content
                    content = getattr(delta, "content", None)
                    if content:
                        text_parts.append(content)
                        yield {"type": "text_delta", "text": content}

                    # Tool calls (streamed incrementally)
                    delta_tool_calls = getattr(delta, "tool_calls", None)
                    if delta_tool_calls:
                        for tc in delta_tool_calls:
                            idx = getattr(tc, "index", 0)
                            if idx not in tool_calls:
                                tool_calls[idx] = {
                                    "id": "",
                                    "name": "",
                                    "arguments": "",
                                }
                            tc_id = getattr(tc, "id", None)
                            if tc_id:
                                tool_calls[idx]["id"] = tc_id
                            fn = getattr(tc, "function", None)
                            if fn:
                                fn_name = getattr(fn, "name", None)
                                if fn_name:
                                    tool_calls[idx]["name"] = fn_name
                                fn_args = getattr(fn, "arguments", None)
                                if fn_args:
                                    tool_calls[idx]["arguments"] += fn_args

                    # Usage info (some providers include it in stream chunks)
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage:
                        input_t = getattr(chunk_usage, "prompt_tokens", 0)
                        output_t = getattr(chunk_usage, "completion_tokens", 0)
                        if input_t:
                            usage["input_tokens"] = input_t
                        if output_t:
                            usage["output_tokens"] = output_t

            except (ConnectionError, httpx.ReadError, asyncio.TimeoutError) as mid_err:
                # Mid-stream error -- yield partial content if we have any
                if text_parts or tool_calls:
                    content_blocks = _build_content_blocks(text_parts, tool_calls)
                    yield {
                        "type": "assistant",
                        "message": Message(
                            role="assistant",
                            content=content_blocks,
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

            # Build the complete assistant message
            content_blocks = _build_content_blocks(text_parts, tool_calls)
            stop_reason = "end_turn" if finish_reason == "stop" else finish_reason

            assistant_msg = Message(
                role="assistant",
                content=content_blocks,
                metadata={
                    "model": resolved_model,
                    "stop_reason": stop_reason,
                    "usage": usage,
                },
            )
            yield {"type": "assistant", "message": assistant_msg}

        except Exception as e:
            error_text = str(e)
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": f"litellm Error: {error_text}"}],
                    metadata={"is_error": True, "error": error_text},
                ),
            }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_content_blocks(
    text_parts: list[str],
    tool_calls: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build D.U.H. content blocks from accumulated text and tool calls."""
    blocks: list[dict[str, Any]] = []
    full_text = "".join(text_parts)
    if full_text:
        blocks.append({"type": "text", "text": full_text})

    for idx in sorted(tool_calls):
        tc = tool_calls[idx]
        try:
            parsed_input = json.loads(tc["arguments"])
        except (json.JSONDecodeError, KeyError):
            parsed_input = {}
        blocks.append({
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["name"],
            "input": parsed_input,
        })
    return blocks


# ---------------------------------------------------------------------------
# Message conversion (OpenAI-compatible format for litellm)
# ---------------------------------------------------------------------------

def _to_litellm_messages(
    messages: list[Any],
    system_prompt: str | list[str],
) -> list[dict[str, Any]]:
    """Convert D.U.H. messages to OpenAI/litellm chat format."""
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
                tc_list = []
                for block in content:
                    bt = block.get("type", "") if isinstance(block, dict) else getattr(block, "type", "")
                    if bt == "text":
                        text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                        text_parts.append(text)
                    elif bt == "tool_use":
                        name = block.get("name", "") if isinstance(block, dict) else getattr(block, "name", "")
                        bid = block.get("id", "") if isinstance(block, dict) else getattr(block, "id", "")
                        inp = block.get("input", {}) if isinstance(block, dict) else getattr(block, "input", {})
                        tc_list.append({
                            "id": bid,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(inp),
                            },
                        })
                if text_parts:
                    msg_dict["content"] = "".join(text_parts)
                if tc_list:
                    msg_dict["tool_calls"] = tc_list
                    if "content" not in msg_dict:
                        msg_dict["content"] = None
            api_messages.append(msg_dict)

    return api_messages


def _build_system_text(system_prompt: str | list[str]) -> str:
    """Build system prompt text from string or list."""
    if isinstance(system_prompt, list):
        return "\n\n".join(system_prompt)
    return system_prompt


def _to_litellm_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert D.U.H. tools to OpenAI function-calling format for litellm."""
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
