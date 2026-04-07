"""Ollama adapter — wraps the Ollama HTTP API into D.U.H. events.

Ollama runs local models (Llama, Qwen, Mistral, etc.) and exposes
an OpenAI-compatible API at http://localhost:11434.

This adapter translates between:
- D.U.H. Messages → Ollama /api/chat format
- Ollama streaming response → D.U.H. uniform events

Usage:
    from duh.adapters.ollama import OllamaProvider
    provider = OllamaProvider(model="qwen2.5-coder:1.5b")
    deps = Deps(call_model=provider.stream)
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import httpx

from duh.kernel.messages import Message


class OllamaProvider:
    """Wraps Ollama's HTTP API to produce D.U.H. uniform events.

    Implements the ModelProvider port contract for local LLMs.
    """

    def __init__(
        self,
        model: str = "qwen2.5-coder:1.5b",
        base_url: str = "http://localhost:11434",
        timeout: float = 300.0,
    ):
        self._default_model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

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
        """Stream model responses from Ollama, yielding D.U.H. events."""
        resolved_model = model or self._default_model

        # Tool choice emulation — Ollama doesn't support tool_choice natively
        effective_tools = tools
        effective_system = system_prompt
        if tool_choice == "none":
            # Simply don't send tools — guaranteed no tool calls
            effective_tools = None
        elif tool_choice == "any" and tools:
            # Inject system prompt hint (best effort for Ollama)
            hint = "You MUST call one of the available tools before responding with text."
            if isinstance(effective_system, list):
                effective_system = list(effective_system) + [hint]
            elif effective_system:
                effective_system = effective_system + "\n\n" + hint
            else:
                effective_system = hint
        # "auto" is default — no modification needed
        # Specific tool name — inject hint
        elif isinstance(tool_choice, str) and tool_choice not in ("auto", "none", "any") and tools:
            hint = f"You MUST call the '{tool_choice}' tool. Do not respond with plain text."
            if isinstance(effective_system, list):
                effective_system = list(effective_system) + [hint]
            elif effective_system:
                effective_system = effective_system + "\n\n" + hint
            else:
                effective_system = hint

        # Build Ollama messages
        api_messages = _to_ollama_messages(messages, effective_system)

        # Build request
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": api_messages,
            "stream": True,
        }

        # Add tools if supported (and not suppressed by tool_choice="none")
        if effective_tools:
            ollama_tools = _to_ollama_tools(effective_tools)
            if ollama_tools:
                payload["tools"] = ollama_tools

        if max_tokens:
            payload.setdefault("options", {})["num_predict"] = max_tokens

        # Stream response
        full_text = ""
        tool_calls: list[dict[str, Any]] = []

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        error_text = _interpret_ollama_error(response.status_code, body)
                        yield {
                            "type": "assistant",
                            "message": Message(
                                role="assistant",
                                content=[{"type": "text", "text": f"Ollama Error: {error_text}"}],
                                metadata={"is_error": True, "error": error_text},
                            ),
                        }
                        return

                    try:
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue

                            try:
                                chunk = json.loads(line)
                            except json.JSONDecodeError as json_err:
                                # Malformed NDJSON chunk — yield partial and error
                                if full_text or tool_calls:
                                    content_blocks: list[dict[str, Any]] = []
                                    if full_text:
                                        content_blocks.append({"type": "text", "text": full_text})
                                    for tc in tool_calls:
                                        content_blocks.append(tc)
                                    yield {
                                        "type": "assistant",
                                        "message": Message(
                                            role="assistant",
                                            content=content_blocks,
                                            metadata={
                                                "partial": True,
                                                "model": resolved_model,
                                                "stop_reason": "error",
                                            },
                                        ),
                                    }
                                yield {"type": "error", "error": f"Malformed JSON chunk: {json_err}"}
                                return

                            # Check for errors
                            if "error" in chunk:
                                yield {
                                    "type": "assistant",
                                    "message": Message(
                                        role="assistant",
                                        content=[{"type": "text", "text": f"Ollama Error: {chunk['error']}"}],
                                        metadata={"is_error": True, "error": chunk["error"]},
                                    ),
                                }
                                return

                            msg = chunk.get("message", {})
                            content = msg.get("content", "")

                            # Stream text deltas
                            if content:
                                full_text += content
                                yield {"type": "text_delta", "text": content}

                            # Check for tool calls
                            if msg.get("tool_calls"):
                                for tc in msg["tool_calls"]:
                                    fn = tc.get("function", {})
                                    tool_calls.append({
                                        "type": "tool_use",
                                        "id": f"ollama-{len(tool_calls)}",
                                        "name": fn.get("name", ""),
                                        "input": fn.get("arguments", {}),
                                    })

                            # Check if done
                            if chunk.get("done"):
                                break

                    except (httpx.ReadError, ConnectionError) as mid_err:
                        # Mid-stream disconnect — yield partial content
                        if full_text or tool_calls:
                            content_blocks_partial: list[dict[str, Any]] = []
                            if full_text:
                                content_blocks_partial.append({"type": "text", "text": full_text})
                            for tc in tool_calls:
                                content_blocks_partial.append(tc)
                            yield {
                                "type": "assistant",
                                "message": Message(
                                    role="assistant",
                                    content=content_blocks_partial,
                                    metadata={
                                        "partial": True,
                                        "model": resolved_model,
                                        "stop_reason": "error",
                                    },
                                ),
                            }
                        yield {"type": "error", "error": f"Stream interrupted: {mid_err}"}
                        return

            # Fallback: some Ollama models output tool calls as JSON text
            # instead of structured tool_call blocks. Detect and parse.
            if not tool_calls and full_text.strip():
                extracted = _extract_tool_calls_from_text(full_text)
                if extracted:
                    tool_calls.extend(extracted)
                    # Remove the JSON text since we extracted the tool calls
                    full_text = ""

            # Build final assistant message
            content_blocks_final: list[dict[str, Any]] = []
            if full_text:
                content_blocks_final.append({"type": "text", "text": full_text})
            for tc in tool_calls:
                content_blocks_final.append(tc)

            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=content_blocks_final or full_text,
                    metadata={
                        "model": resolved_model,
                        "stop_reason": "end_turn",
                    },
                ),
            }

        except httpx.ConnectError:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "Ollama Error: Cannot connect to Ollama. Is it running? Start with: ollama serve"}],
                    metadata={"is_error": True, "error": "Connection refused"},
                ),
            }
        except Exception as e:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": f"Ollama Error: {e}"}],
                    metadata={"is_error": True, "error": str(e)},
                ),
            }


def _extract_tool_calls_from_text(text: str) -> list[dict[str, Any]]:
    """Extract tool calls from text that looks like JSON tool invocations.

    Some Ollama models output tool calls as JSON text instead of structured
    tool_call blocks. This detects patterns like:
      {"name": "Read", "arguments": {"file_path": "..."}}
    """
    import re
    results: list[dict[str, Any]] = []

    json_pattern = re.compile(
        r'\{[^{}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}[^}]*\}',
        re.DOTALL,
    )
    matches = json_pattern.findall(text)

    for match in matches:
        try:
            obj = json.loads(match)
            name = obj.get("name", "")
            arguments = obj.get("arguments", {})
            if name:
                results.append({
                    "type": "tool_use",
                    "id": f"ollama-extracted-{len(results)}",
                    "name": name,
                    "input": arguments,
                })
        except (json.JSONDecodeError, TypeError):
            continue

    return results


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

def _to_ollama_messages(
    messages: list[Any],
    system_prompt: str | list[str],
) -> list[dict[str, Any]]:
    """Translate D.U.H. Messages → Ollama chat format."""
    result: list[dict[str, Any]] = []

    # System prompt first
    sys_text = system_prompt if isinstance(system_prompt, str) else "\n\n".join(system_prompt)
    if sys_text:
        result.append({"role": "system", "content": sys_text})

    for msg in messages:
        if isinstance(msg, Message):
            content = msg.text if isinstance(msg.content, (str, list)) else str(msg.content)
            result.append({"role": msg.role, "content": content or ""})
        elif isinstance(msg, dict):
            result.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "") if isinstance(msg.get("content"), str) else str(msg.get("content", "")),
            })

    return result


def _to_ollama_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Translate D.U.H. Tool objects → Ollama tool format."""
    result = []
    for tool in tools:
        if isinstance(tool, dict):
            result.append(tool)
        elif hasattr(tool, "name") and hasattr(tool, "input_schema"):
            desc = getattr(tool, "description", "")
            if callable(desc):
                desc = desc()
            result.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": str(desc) if desc else "",
                    "parameters": tool.input_schema,
                },
            })
    return result


def _interpret_ollama_error(status_code: int, body: bytes) -> str:
    """Translate Ollama HTTP errors into actionable messages."""
    try:
        data = json.loads(body)
        error = data.get("error", "")
    except (json.JSONDecodeError, ValueError):
        error = body.decode("utf-8", errors="replace")

    if status_code == 404 or "not found" in error.lower():
        return f"Model not found. Pull it first: ollama pull <model-name>"
    if "connection refused" in error.lower():
        return "Cannot connect to Ollama. Start with: ollama serve"

    return f"HTTP {status_code}: {error}"
