"""Groq adapter — wraps the official ``groq`` Python SDK into D.U.H. events.

Groq's public API is OpenAI-Chat-Completions-shaped. This adapter translates:
- D.U.H. ``Message`` list → Groq ``messages`` (role / content / tool_calls)
- Groq streaming chunks → D.U.H. uniform events (``text_delta``, ``tool_use``,
  ``usage_delta``, ``done``, ``assistant``, ``error``)

Features (ADR-075):
- Native SDK (no LiteLLM in the path).
- Rate-limit headers surfaced in the ``done`` event metadata so callers can
  adapt to ``x-ratelimit-remaining-tokens`` / ``-requests`` / ``x-groq-region``.
- All provider output wrapped as ``UntrustedStr(..., MODEL_OUTPUT)`` (taint).
- ``with_backoff`` retries 429 / transient errors (exponential + jitter).
- The registry emits models as ``groq/<name>`` for display; we strip the
  ``groq/`` prefix before sending because the Groq API expects bare names.

Usage::

    from duh.adapters.groq import GroqProvider
    provider = GroqProvider(api_key="gsk-...", model="llama-3.3-70b-versatile")
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
from duh.kernel.untrusted import TaintSource, UntrustedStr


def _wrap_model_output(text: str) -> UntrustedStr:
    """Tag Groq provider output as MODEL_OUTPUT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.MODEL_OUTPUT)


def _strip_namespace(model: str) -> str:
    """Strip the ``groq/`` registry prefix before handing to the Groq API.

    Delegates to the shared provider-prefix registry so that adding a new
    provider prefix only requires editing one table.
    """
    from duh.providers.registry import strip_provider_prefix
    return strip_provider_prefix(model)


class GroqProvider:
    """Wraps the Groq Python SDK to produce D.U.H. uniform events.

    Implements the ModelProvider port contract (see ``AnthropicProvider``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "llama-3.3-70b-versatile",
        max_retries: int = 2,
        timeout: float = 600.0,
        base_url: str | None = None,
    ):
        try:
            from groq import AsyncGroq
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "GroqProvider requires the 'groq' package. "
                "Install with: pip install 'groq>=0.11,<1'"
            ) from exc

        # Env-var chain lives in ``duh.providers.registry.PROVIDER_ENV_VARS``.
        from duh.providers.registry import get_api_key
        self._default_model = model
        self._client = AsyncGroq(
            api_key=api_key or get_api_key("groq"),
            max_retries=max_retries,
            timeout=timeout,
            **({"base_url": base_url} if base_url else {}),
        )

    @classmethod
    def _parse_tool_use_block(cls, block: dict[str, Any]) -> ParsedToolUse:
        """Parse a raw tool_use JSON block into a ParsedToolUse.

        All providers must agree on the output for the same input
        (ADR-054 §9 — differential fuzzer).
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
        """Stream model responses from Groq, yielding D.U.H. events."""
        # Keep the namespaced form for display / tracking; only the API call
        # gets the bare model name.
        display_model = model or self._default_model
        api_model = _strip_namespace(display_model)

        api_messages = _to_groq_messages(messages, system_prompt)
        api_tools = _to_groq_tools(tools) if tools else None

        request: dict[str, Any] = {
            "model": api_model,
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
                request["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice},
                }
            elif isinstance(tool_choice, dict):
                request["tool_choice"] = tool_choice

        async def _do_stream() -> AsyncGenerator[dict[str, Any], None]:
            # Use with_raw_response so we get the http headers
            # (x-ratelimit-remaining-tokens, x-groq-region, ...) which the
            # plain streaming call drops.
            raw = await self._client.chat.completions.with_raw_response.create(**request)
            rate_limit_meta = _extract_rate_limit_headers(raw)
            response = await raw.parse()

            text_parts: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}
            finish_reason: str | None = None
            usage: dict[str, int] = {}

            try:
                async for chunk in response:
                    choices = getattr(chunk, "choices", None) or []
                    if choices:
                        choice = choices[0]
                        delta = getattr(choice, "delta", None)

                        fr = getattr(choice, "finish_reason", None)
                        if fr:
                            finish_reason = fr

                        if delta is not None:
                            # Text content
                            content = getattr(delta, "content", None)
                            if content:
                                text_parts.append(content)
                                # Taint the streamed text before yielding.
                                yield {
                                    "type": "text_delta",
                                    "text": _wrap_model_output(content),
                                }

                            # Tool-call deltas — buffer until complete.
                            delta_tool_calls = getattr(delta, "tool_calls", None) or []
                            for tc in delta_tool_calls:
                                idx = getattr(tc, "index", 0) or 0
                                slot = tool_calls.setdefault(
                                    idx,
                                    {"id": "", "name": "", "arguments": ""},
                                )
                                tc_id = getattr(tc, "id", None)
                                if tc_id:
                                    slot["id"] = tc_id
                                fn = getattr(tc, "function", None)
                                if fn is not None:
                                    fn_name = getattr(fn, "name", None)
                                    if fn_name:
                                        slot["name"] = fn_name
                                    fn_args = getattr(fn, "arguments", None)
                                    if fn_args:
                                        slot["arguments"] += fn_args

                    # Groq puts usage in the final chunk's top-level `usage`
                    # (sometimes via x_groq.usage).
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is None:
                        x_groq = getattr(chunk, "x_groq", None)
                        if x_groq is not None:
                            chunk_usage = getattr(x_groq, "usage", None)
                    if chunk_usage is not None:
                        input_t = getattr(chunk_usage, "prompt_tokens", 0) or 0
                        output_t = getattr(chunk_usage, "completion_tokens", 0) or 0
                        if input_t:
                            usage["input_tokens"] = int(input_t)
                        if output_t:
                            usage["output_tokens"] = int(output_t)
            except (ConnectionError, httpx.ReadError, asyncio.TimeoutError) as mid_err:
                # Mid-stream interruption — emit partial assistant + error.
                if text_parts or tool_calls:
                    partial_blocks = _build_content_blocks(text_parts, tool_calls)
                    yield {
                        "type": "assistant",
                        "message": Message(
                            role="assistant",
                            content=partial_blocks,
                            metadata={
                                "partial": True,
                                "model": display_model,
                                "stop_reason": "error",
                                "usage": usage,
                                "rate_limit": rate_limit_meta,
                            },
                        ),
                    }
                yield {"type": "error", "error": f"Stream interrupted: {mid_err}"}
                return

            # Emit a single tool_use event per completed tool call so the
            # differential fuzzer + downstream consumers can act on them
            # without re-parsing the accumulated args.
            for idx in sorted(tool_calls):
                tc = tool_calls[idx]
                try:
                    parsed_input = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except (json.JSONDecodeError, KeyError):
                    parsed_input = {}
                yield {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": parsed_input,
                }

            content_blocks = _build_content_blocks(text_parts, tool_calls)
            stop_reason = "end_turn" if (finish_reason in (None, "stop")) else finish_reason

            # Usage delta (cache tracker integration, ADR-061).
            yield {"type": "usage_delta", "usage": usage}

            # Terminal done event — carries rate-limit metadata for PERF-6 /
            # future auto-throttle.
            yield {
                "type": "done",
                "model": display_model,
                "stop_reason": stop_reason,
                "usage": usage,
                "rate_limit": rate_limit_meta,
            }

            assistant_msg = Message(
                role="assistant",
                content=content_blocks,
                metadata={
                    "model": display_model,
                    "stop_reason": stop_reason,
                    "usage": usage,
                    "rate_limit": rate_limit_meta,
                },
            )
            yield {"type": "assistant", "message": assistant_msg}

        try:
            async for event in with_backoff(_do_stream):
                yield event
        except Exception as e:
            error_text = _redact_api_key(str(e))
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": f"Groq API Error: {error_text}"}],
                    metadata={"is_error": True, "error": error_text},
                ),
            }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RATE_LIMIT_HEADER_KEYS = (
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-groq-region",
    "retry-after",
)


def _extract_rate_limit_headers(raw_response: Any) -> dict[str, str]:
    """Extract Groq rate-limit / region metadata from a raw SDK response."""
    headers = getattr(raw_response, "headers", None)
    if headers is None:
        return {}
    meta: dict[str, str] = {}
    for key in _RATE_LIMIT_HEADER_KEYS:
        try:
            val = headers.get(key)
        except Exception:
            val = None
        if val is None:
            try:
                val = headers.get(key.lower())
            except Exception:
                val = None
        if val is not None:
            meta[key] = str(val)
    return meta


def _redact_api_key(text: str) -> str:
    """Drop Groq API keys out of exception strings before surfacing them.

    Groq keys are of the form ``gsk_...``; we also redact anything that
    looks like an ``api_key=...`` query fragment, in case the SDK ever
    serialises one into a message.
    """
    import re

    text = re.sub(r"gsk_[A-Za-z0-9_-]{8,}", "gsk_***REDACTED***", text)
    text = re.sub(r"(api[_-]?key=)[^&\s]+", r"\1***REDACTED***", text, flags=re.I)
    return text


def _build_content_blocks(
    text_parts: list[str],
    tool_calls: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build D.U.H. content blocks from accumulated text + tool calls."""
    blocks: list[dict[str, Any]] = []
    full_text = "".join(text_parts)
    if full_text:
        blocks.append({"type": "text", "text": _wrap_model_output(full_text)})
    for idx in sorted(tool_calls):
        tc = tool_calls[idx]
        try:
            parsed_input = json.loads(tc["arguments"]) if tc["arguments"] else {}
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
# Message / tool conversion — OpenAI-Chat-Completions shape
# ---------------------------------------------------------------------------

def _to_groq_messages(
    messages: list[Any],
    system_prompt: str | list[str],
) -> list[dict[str, Any]]:
    """Convert D.U.H. messages → Groq (OpenAI-compatible) chat format."""
    api_messages: list[dict[str, Any]] = []

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
                        tool_use_id = (
                            block.get("tool_use_id", "")
                            if isinstance(block, dict)
                            else getattr(block, "tool_use_id", "")
                        )
                        result_content = (
                            block.get("content", "")
                            if isinstance(block, dict)
                            else getattr(block, "content", "")
                        )
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
                text_parts: list[str] = []
                tc_list: list[dict[str, Any]] = []
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
    if isinstance(system_prompt, list):
        return "\n\n".join(p for p in system_prompt if p)
    return system_prompt


def _to_groq_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert D.U.H. tools → Groq (OpenAI-compatible) function tools."""
    api_tools = []
    for tool in tools:
        if isinstance(tool, dict):
            # Already in API shape — pass through.
            if tool.get("type") == "function" and "function" in tool:
                api_tools.append(tool)
                continue
            name = tool.get("name", "")
            description = tool.get("description", "")
            schema = tool.get("input_schema", tool.get("parameters", {}))
        else:
            name = getattr(tool, "name", "")
            description = getattr(tool, "description", "")
            if callable(description):
                description = description()
            schema = getattr(tool, "input_schema", {})
        if not name:
            continue
        api_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": str(description) if description else "",
                "parameters": schema,
            },
        })
    return api_tools
