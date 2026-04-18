"""Gemini adapter — wraps the official `google-genai` SDK into D.U.H. events.

Replaces the LiteLLM path for Gemini models (ADR-075). Exposes the provider
features that LiteLLM hides:

- Gemini 2.5 ``ThinkingConfig`` (dynamic or fixed ``thinking_budget``)
- Explicit persistent caches via ``client.caches.create()``
- ``system_instruction`` kept distinct from conversation messages
- Usage metadata including ``cached_content_token_count`` and
  ``thoughts_token_count``

This adapter translates between:
- D.U.H. Messages → Gemini ``Content`` / ``Part`` objects
- Gemini streaming events → D.U.H. uniform events
- D.U.H. tool schemas → Gemini ``FunctionDeclaration`` / ``Tool`` objects

Usage:
    from duh.adapters.gemini import GeminiProvider
    provider = GeminiProvider(api_key="...", model="gemini-2.5-pro")
    deps = Deps(call_model=provider.stream)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import httpx

from duh.adapters.anthropic import ParsedToolUse
from duh.kernel.backoff import with_backoff
from duh.kernel.messages import Message
from duh.kernel.untrusted import TaintSource, UntrustedStr


# ---------------------------------------------------------------------------
# SDK import — guarded so the module itself is importable without google-genai
# ---------------------------------------------------------------------------

try:  # pragma: no cover - trivial import guard
    from google import genai as _genai  # type: ignore[import-not-found]
    from google.genai import types as _genai_types  # type: ignore[import-not-found]

    _GENAI_AVAILABLE = True
    _GENAI_IMPORT_ERROR: Exception | None = None
except Exception as _imp_err:  # pragma: no cover - only hit when SDK missing
    _genai = None  # type: ignore[assignment]
    _genai_types = None  # type: ignore[assignment]
    _GENAI_AVAILABLE = False
    _GENAI_IMPORT_ERROR = _imp_err


def _require_genai() -> None:
    """Raise a clear ImportError if the SDK isn't installed."""
    if not _GENAI_AVAILABLE:
        raise ImportError(
            "google-genai is not installed. pip install google-genai"
        ) from _GENAI_IMPORT_ERROR


# ---------------------------------------------------------------------------
# Secret scrubbing
# ---------------------------------------------------------------------------

_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


def _scrub(text: str) -> str:
    """Strip things that look like Gemini API keys from an error string."""
    return _API_KEY_RE.sub("[redacted]", text)


def _safe_error(exc: BaseException) -> str:
    return _scrub(str(exc))


def _wrap_model_output(text: str) -> UntrustedStr:
    """Tag Gemini provider output as MODEL_OUTPUT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.MODEL_OUTPUT)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class GeminiProvider:
    """Wraps the google-genai SDK to produce D.U.H. uniform events.

    Implements the ModelProvider port contract.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-pro",
        thinking_budget: int | None = None,
    ):
        _require_genai()
        resolved_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or ""
        )
        self._api_key = resolved_key
        self._client = _genai.Client(api_key=resolved_key)
        self._default_model = model
        # None = provider default; -1 = dynamic; 0 = disabled; int = fixed budget
        self._thinking_budget = thinking_budget

    # ------------------------------------------------------------------
    # Differential-fuzzer parity (ADR-054 §9)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Cache management (Gemini-specific feature)
    # ------------------------------------------------------------------

    def create_cache(
        self,
        content: str | list[Any],
        *,
        ttl_seconds: int = 3600,
        model: str | None = None,
        system_instruction: str | None = None,
    ) -> str:
        """Create a persistent cache and return its name (cache_id).

        The returned string can be passed to ``.stream(..., cached_content=...)``
        or reused across invocations until TTL expiry.
        """
        resolved_model = model or self._default_model
        contents = (
            [_make_user_content(content)]
            if isinstance(content, str)
            else _contents_from_messages(content)
        )
        cfg = _genai_types.CreateCachedContentConfig(
            contents=contents,
            ttl=f"{int(ttl_seconds)}s",
            system_instruction=system_instruction,
        )
        cache = self._client.caches.create(model=resolved_model, config=cfg)
        return getattr(cache, "name", "") or ""

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

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
        cached_content: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream model responses, yielding D.U.H. uniform events."""
        _require_genai()
        resolved_model = model or self._default_model
        # Gemini SDK expects bare model IDs ("gemini-2.5-pro"), not LiteLLM-prefixed
        # ones ("gemini/gemini-2.5-pro"). The registry may hand either form; strip.
        if resolved_model.startswith("gemini/"):
            resolved_model = resolved_model[len("gemini/"):]

        # System prompt extracted from messages — Gemini uses system_instruction
        system_text = _build_system_text(system_prompt)
        extra_system, api_contents = _contents_with_extracted_system(messages)
        if extra_system:
            # Respect both the explicit system_prompt arg and any system-role
            # messages that slipped into the messages list.
            system_text = (system_text + "\n\n" + extra_system).strip() if system_text else extra_system

        # Config
        cfg_kwargs: dict[str, Any] = {}
        if system_text:
            cfg_kwargs["system_instruction"] = system_text
        if max_tokens:
            cfg_kwargs["max_output_tokens"] = max_tokens

        # Thinking — only supported on gemini-2.5-*
        if _supports_thinking(resolved_model):
            budget = _resolve_thinking_budget(thinking, self._thinking_budget)
            if budget is not None:
                cfg_kwargs["thinking_config"] = _genai_types.ThinkingConfig(
                    thinking_budget=budget,
                    include_thoughts=budget != 0,
                )

        # Tools + tool_choice
        if tools:
            cfg_kwargs["tools"] = _to_api_tools(tools)
            tc = _to_tool_config(tool_choice)
            if tc is not None:
                cfg_kwargs["tool_config"] = tc

        # Explicit cache object (Gemini-specific)
        if cached_content:
            cfg_kwargs["cached_content"] = cached_content

        config = _genai_types.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None

        # Stream with exponential backoff on 429/5xx
        accumulated_text: list[str] = []
        accumulated_thinking: list[str] = []
        function_calls: list[dict[str, Any]] = []
        usage: dict[str, int] = {}
        finish_reason: str = "end_turn"

        async def _do_stream() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal accumulated_text, accumulated_thinking, function_calls, usage, finish_reason
            accumulated_text = []
            accumulated_thinking = []
            function_calls = []
            usage = {}

            stream_kwargs: dict[str, Any] = {
                "model": resolved_model,
                "contents": api_contents,
            }
            if config is not None:
                stream_kwargs["config"] = config

            try:
                iterator = await self._client.aio.models.generate_content_stream(
                    **stream_kwargs
                )
            except TypeError:
                # Some SDK versions return the async iterator synchronously.
                iterator = self._client.aio.models.generate_content_stream(
                    **stream_kwargs
                )

            try:
                async for chunk in iterator:
                    # Usage metadata may appear on any chunk (often the last)
                    um = getattr(chunk, "usage_metadata", None)
                    if um is not None:
                        usage.update(_extract_usage(um))

                    # Finish reason
                    candidates = getattr(chunk, "candidates", None) or []
                    for cand in candidates:
                        fr = getattr(cand, "finish_reason", None)
                        if fr:
                            finish_reason = _normalize_finish_reason(fr)

                    # Parts may live on chunk.candidates[0].content.parts
                    parts = _extract_parts(chunk)
                    for part in parts:
                        # Thinking / reasoning parts
                        if getattr(part, "thought", False):
                            thought_text = getattr(part, "text", "") or ""
                            if thought_text:
                                accumulated_thinking.append(thought_text)
                                yield {
                                    "type": "thinking_delta",
                                    "text": thought_text,
                                }
                            continue

                        # Function calls
                        fc = getattr(part, "function_call", None)
                        if fc is not None:
                            name = getattr(fc, "name", "") or ""
                            args = getattr(fc, "args", None) or {}
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {}
                            # Gemini sometimes includes its own id; otherwise synth one
                            fc_id = getattr(fc, "id", None) or f"toolu_{uuid.uuid4().hex[:24]}"
                            function_calls.append({
                                "id": fc_id,
                                "name": name,
                                "input": dict(args) if isinstance(args, dict) else {},
                            })
                            yield {
                                "type": "tool_use",
                                "id": fc_id,
                                "name": name,
                                "input": dict(args) if isinstance(args, dict) else {},
                            }
                            continue

                        # Regular text
                        text = getattr(part, "text", "") or ""
                        if text:
                            accumulated_text.append(text)
                            yield {"type": "text_delta", "text": text}
            except (ConnectionError, httpx.ReadError, asyncio.TimeoutError) as mid_err:
                partial_text = "".join(accumulated_text)
                if partial_text or function_calls:
                    yield {
                        "type": "assistant",
                        "message": Message(
                            role="assistant",
                            content=_build_content_blocks(
                                accumulated_text, accumulated_thinking, function_calls
                            ),
                            metadata={
                                "partial": True,
                                "model": resolved_model,
                                "stop_reason": "error",
                                "usage": usage,
                            },
                        ),
                    }
                yield {"type": "error", "error": f"Stream interrupted: {_safe_error(mid_err)}"}
                return

            # usage_delta lets callers react before the final message if they want
            if usage:
                yield {"type": "usage_delta", "usage": dict(usage)}

            # Final assistant message
            assistant_msg = Message(
                role="assistant",
                content=_build_content_blocks(
                    accumulated_text, accumulated_thinking, function_calls
                ),
                metadata={
                    "model": resolved_model,
                    "stop_reason": finish_reason,
                    "usage": usage,
                },
            )
            yield {"type": "assistant", "message": assistant_msg}
            yield {"type": "done", "usage": dict(usage), "stop_reason": finish_reason}

        try:
            async for event in with_backoff(_do_stream):
                yield event
        except Exception as e:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": f"API Error: {_safe_error(e)}"}],
                    metadata={"is_error": True, "error": _safe_error(e)},
                ),
            }


# ---------------------------------------------------------------------------
# Helpers — content translation
# ---------------------------------------------------------------------------

def _build_system_text(system_prompt: str | list[str]) -> str:
    if isinstance(system_prompt, list):
        return "\n\n".join(p for p in system_prompt if p)
    return system_prompt or ""


def _supports_thinking(model: str) -> bool:
    return "gemini-2.5" in (model or "").lower()


def _resolve_thinking_budget(
    thinking: dict[str, Any] | None,
    default_budget: int | None,
) -> int | None:
    """Decide the int budget to pass to ThinkingConfig, or None to skip."""
    if thinking is not None:
        t_type = thinking.get("type", "")
        if t_type in ("disabled", "off"):
            return 0
        if "budget_tokens" in thinking:
            val = thinking["budget_tokens"]
            if isinstance(val, int):
                return val
        if t_type in ("adaptive", "dynamic"):
            return -1
        if t_type == "enabled":
            return default_budget if default_budget is not None else -1
    # Fall back to constructor default
    if default_budget is not None:
        return default_budget
    return None


def _make_user_content(text: str) -> Any:
    return _genai_types.Content(
        role="user",
        parts=[_genai_types.Part(text=text)],
    )


def _contents_with_extracted_system(
    messages: list[Any],
) -> tuple[str, list[Any]]:
    """Return (extracted_system_text, api_contents).

    Any message with role="system" is pulled out of the messages list — Gemini
    expects the system prompt separately via system_instruction.
    """
    system_parts: list[str] = []
    kept: list[Any] = []
    for msg in messages:
        role = msg.role if isinstance(msg, Message) else msg.get("role", "user")
        if role == "system":
            content = msg.content if isinstance(msg, Message) else msg.get("content", "")
            if isinstance(content, str):
                if content:
                    system_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    bt = block.get("type", "") if isinstance(block, dict) else getattr(block, "type", "")
                    if bt == "text":
                        t = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                        if t:
                            system_parts.append(t)
            continue
        kept.append(msg)
    return "\n\n".join(system_parts), _contents_from_messages(kept)


def _contents_from_messages(messages: list[Any]) -> list[Any]:
    """Convert D.U.H. messages → list of google.genai Content objects."""
    contents: list[Any] = []
    for msg in messages:
        role = msg.role if isinstance(msg, Message) else msg.get("role", "user")
        content = msg.content if isinstance(msg, Message) else msg.get("content", "")

        gemini_role = "model" if role == "assistant" else "user"
        parts = _parts_from_content(content)
        if not parts:
            continue
        contents.append(_genai_types.Content(role=gemini_role, parts=parts))
    return contents


def _parts_from_content(content: Any) -> list[Any]:
    """Turn a Message.content into a list of google.genai Part objects."""
    if isinstance(content, str):
        return [_genai_types.Part(text=content)] if content else []

    if not isinstance(content, list):
        s = str(content)
        return [_genai_types.Part(text=s)] if s else []

    out: list[Any] = []
    for block in content:
        bt = block.get("type", "") if isinstance(block, dict) else getattr(block, "type", "")

        if bt == "text":
            text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
            if text:
                out.append(_genai_types.Part(text=str(text)))

        elif bt == "tool_use":
            # Assistant function call
            name = block.get("name", "") if isinstance(block, dict) else getattr(block, "name", "")
            inp = block.get("input", {}) if isinstance(block, dict) else getattr(block, "input", {})
            fc_id = block.get("id", "") if isinstance(block, dict) else getattr(block, "id", "")
            out.append(
                _genai_types.Part(
                    function_call=_genai_types.FunctionCall(
                        id=fc_id or None,
                        name=str(name),
                        args=inp if isinstance(inp, dict) else {},
                    )
                )
            )

        elif bt == "tool_result":
            # Translate D.U.H. tool_result → Gemini FunctionResponse part
            tool_use_id = (
                block.get("tool_use_id", "") if isinstance(block, dict)
                else getattr(block, "tool_use_id", "")
            )
            result = (
                block.get("content", "") if isinstance(block, dict)
                else getattr(block, "content", "")
            )
            is_error = (
                block.get("is_error", False) if isinstance(block, dict)
                else getattr(block, "is_error", False)
            )
            # Gemini expects a dict response payload
            response_payload: dict[str, Any]
            if isinstance(result, str):
                response_payload = {"error": result} if is_error else {"result": result}
            elif isinstance(result, list):
                flattened = []
                for item in result:
                    if isinstance(item, dict) and item.get("type") == "text":
                        flattened.append(item.get("text", ""))
                    else:
                        flattened.append(str(item))
                text = "\n".join(flattened)
                response_payload = {"error": text} if is_error else {"result": text}
            else:
                response_payload = {"error": str(result)} if is_error else {"result": str(result)}

            out.append(
                _genai_types.Part(
                    function_response=_genai_types.FunctionResponse(
                        id=tool_use_id or None,
                        # Gemini pairs responses by name when id isn't present
                        name=_extract_name_hint(tool_use_id) or "tool",
                        response=response_payload,
                    )
                )
            )

        elif bt == "image":
            media_type = (
                block.get("media_type", "") if isinstance(block, dict)
                else getattr(block, "media_type", "")
            )
            data = (
                block.get("data", "") if isinstance(block, dict)
                else getattr(block, "data", "")
            )
            if data:
                try:
                    import base64

                    raw = base64.b64decode(data)
                    out.append(
                        _genai_types.Part(
                            inline_data=_genai_types.Blob(
                                mime_type=media_type or "image/png",
                                data=raw,
                            )
                        )
                    )
                except Exception:
                    pass

        elif bt == "thinking":
            # We don't replay thinking blocks back to Gemini — it tracks its own
            continue

    return out


def _extract_name_hint(tool_use_id: str) -> str | None:
    """Best-effort: extract a function name from an id shaped like `name_xxx`."""
    if not tool_use_id:
        return None
    if "_" in tool_use_id:
        return tool_use_id.split("_", 1)[0]
    return None


# ---------------------------------------------------------------------------
# Helpers — tools
# ---------------------------------------------------------------------------

def _to_api_tools(tools: list[Any]) -> list[Any]:
    """Convert D.U.H. Tool objects → Gemini Tool with FunctionDeclarations.

    Gemini groups all function declarations under a single Tool object.
    """
    declarations: list[Any] = []
    for tool in tools:
        if isinstance(tool, dict):
            name = tool.get("name", "")
            description = tool.get("description", "") or ""
            schema = tool.get("input_schema") or tool.get("parameters") or {}
        else:
            name = getattr(tool, "name", "")
            description = getattr(tool, "description", "") or ""
            if callable(description):
                description = description()
            schema = getattr(tool, "input_schema", {}) or {}
        if not name:
            continue
        declarations.append(
            _genai_types.FunctionDeclaration(
                name=str(name),
                description=str(description),
                parameters_json_schema=_sanitize_schema_for_gemini(schema),
            )
        )
    if not declarations:
        return []
    return [_genai_types.Tool(function_declarations=declarations)]


def _sanitize_schema_for_gemini(schema: Any) -> dict[str, Any]:
    """Gemini is stricter about JSON schemas — drop unsupported keys.

    Keep the common happy path (type, properties, required, items, description,
    enum, format). Anything exotic is dropped.
    """
    if not isinstance(schema, dict):
        return {"type": "object"}
    # If the caller already provided a complete schema, keep it mostly as-is;
    # Gemini's parameters_json_schema accepts plain JSON Schema.
    return schema


def _to_tool_config(tool_choice: str | dict[str, Any] | None) -> Any | None:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, dict):
        # Assume caller gave a Gemini-shaped config already
        return tool_choice
    if tool_choice == "none":
        mode = "NONE"
        allowed = None
    elif tool_choice in ("any", "required"):
        mode = "ANY"
        allowed = None
    elif tool_choice == "auto":
        mode = "AUTO"
        allowed = None
    else:
        # Assume specific tool name
        mode = "ANY"
        allowed = [str(tool_choice)]
    fc_kwargs: dict[str, Any] = {"mode": mode}
    if allowed is not None:
        fc_kwargs["allowed_function_names"] = allowed
    return _genai_types.ToolConfig(
        function_calling_config=_genai_types.FunctionCallingConfig(**fc_kwargs)
    )


# ---------------------------------------------------------------------------
# Helpers — stream parsing
# ---------------------------------------------------------------------------

def _extract_parts(chunk: Any) -> list[Any]:
    """Pull the `parts` out of a streaming chunk."""
    candidates = getattr(chunk, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None) or []
        if parts:
            return list(parts)
    return []


def _extract_usage(um: Any) -> dict[str, int]:
    """Map Gemini UsageMetadata → D.U.H. usage dict.

    D.U.H. naming (input_tokens, output_tokens, cache_read_input_tokens)
    mirrors the Anthropic adapter so CacheTracker can consume it uniformly.
    """
    def _i(attr: str) -> int:
        v = getattr(um, attr, 0)
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "input_tokens": _i("prompt_token_count"),
        "output_tokens": _i("candidates_token_count"),
        "cache_read_input_tokens": _i("cached_content_token_count"),
        "cache_creation_input_tokens": 0,  # Gemini manages cache creation out-of-band
        "thoughts_tokens": _i("thoughts_token_count"),
        "total_tokens": _i("total_token_count"),
    }


def _normalize_finish_reason(reason: Any) -> str:
    s = str(reason).upper()
    if "STOP" in s:
        return "end_turn"
    if "MAX_TOKENS" in s or "LENGTH" in s:
        return "max_tokens"
    if "SAFETY" in s or "BLOCK" in s:
        return "content_filter"
    if "TOOL" in s or "FUNCTION" in s:
        return "tool_use"
    return s.lower() or "end_turn"


def _build_content_blocks(
    text_parts: list[str],
    thinking_parts: list[str],
    function_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    thinking_text = "".join(thinking_parts)
    if thinking_text:
        blocks.append({"type": "thinking", "thinking": thinking_text})
    full_text = "".join(text_parts)
    if full_text:
        blocks.append({"type": "text", "text": full_text})
    for fc in function_calls:
        blocks.append({
            "type": "tool_use",
            "id": fc["id"],
            "name": fc["name"],
            "input": fc["input"],
        })
    return blocks
