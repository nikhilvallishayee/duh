"""OpenAI ChatGPT subscription adapter for Codex-family models.

Uses OAuth access tokens and the ChatGPT backend Codex responses endpoint.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, AsyncGenerator

import httpx

from duh.adapters.anthropic import ParsedToolUse
from duh.auth.openai_chatgpt import get_valid_openai_chatgpt_oauth
from duh.kernel.messages import Message
from duh.kernel.untrusted import TaintSource, UntrustedStr


def _wrap_model_output(text: str) -> UntrustedStr:
    """Tag OpenAI ChatGPT provider output as MODEL_OUTPUT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.MODEL_OUTPUT)


class OpenAIChatGPTProvider:
    """Adapter for ChatGPT subscription-backed Codex models."""

    def __init__(self, model: str = "gpt-5.2-codex"):
        self._default_model = model

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
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        debug_sse = os.environ.get("DUH_OPENAI_CHATGPT_DEBUG", "") == "1"
        oauth = get_valid_openai_chatgpt_oauth()
        if not oauth:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "OpenAI ChatGPT auth required. Use /connect openai."}],
                    metadata={"is_error": True},
                ),
            }
            return

        resolved_model = (model or self._default_model).strip() or "gpt-5.2-codex"
        access = oauth.get("access_token", "")
        account_id = oauth.get("account_id", "")
        if not access or not account_id:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "OpenAI ChatGPT auth is invalid. Re-run /connect openai."}],
                    metadata={"is_error": True},
                ),
            }
            return

        instructions = _build_system_text(system_prompt).strip()
        if not instructions:
            instructions = "You are a coding assistant."

        request_body: dict[str, Any] = {
            "model": resolved_model,
            "instructions": instructions,
            "input": _to_responses_input(messages, ""),
            "stream": True,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if tools:
            request_body["tools"] = _to_responses_tools(tools)
        if max_tokens:
            request_body["max_output_tokens"] = max_tokens
        if tool_choice and tools:
            if tool_choice == "any":
                request_body["tool_choice"] = "required"
            elif tool_choice in ("none", "auto"):
                request_body["tool_choice"] = tool_choice
            elif isinstance(tool_choice, str):
                request_body["tool_choice"] = {"type": "function", "name": tool_choice}

        headers = {
            "Authorization": f"Bearer {access}",
            "chatgpt-account-id": str(account_id),
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }

        final_response: dict[str, Any] | None = None
        text_chunks: list[str] = []
        response_id = ""
        streamed_calls: dict[str, dict[str, Any]] = {}
        streamed_item_to_call: dict[str, str] = {}
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                async with client.stream(
                    "POST",
                    "https://chatgpt.com/backend-api/codex/responses",
                    headers=headers,
                    json=request_body,
                ) as resp:
                    if resp.status_code >= 400:
                        text = await resp.aread()
                        err = text.decode("utf-8", errors="replace")[:500]
                        yield {
                            "type": "assistant",
                            "message": Message(
                                role="assistant",
                                content=[{"type": "text", "text": f"OpenAI ChatGPT error ({resp.status_code}): {err}"}],
                                metadata={"is_error": True},
                            ),
                        }
                        return

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        event_type = event.get("type", "")
                        if debug_sse:
                            sys.stderr.write(f"[openai-chatgpt] event={event_type}\n")
                        rid = _extract_response_id(event)
                        if rid:
                            response_id = rid
                        _accumulate_streamed_function_calls(
                            event,
                            streamed_calls,
                            streamed_item_to_call,
                        )

                        if event_type == "response.output_text.delta":
                            delta = event.get("delta", "")
                            if delta:
                                text_chunks.append(str(delta))
                                yield {"type": "text_delta", "text": str(delta)}
                        elif event_type == "response.output_text.done":
                            text = event.get("text", "")
                            if text:
                                text_s = str(text)
                                if text_s not in text_chunks:
                                    text_chunks.append(text_s)
                                    yield {"type": "text_delta", "text": text_s}
                        elif event_type in ("response.completed", "response.done"):
                            resp_obj = event.get("response")
                            if isinstance(resp_obj, dict):
                                final_response = resp_obj
                        elif event_type.endswith(".delta"):
                            if "function_call_arguments" in event_type:
                                continue
                            for t in _extract_texts_from_event(event):
                                text_chunks.append(t)
                                yield {"type": "text_delta", "text": t}
                        elif event_type.endswith(".done") or event_type.endswith(".added"):
                            if "function_call_arguments" in event_type:
                                continue
                            for t in _extract_texts_from_event(event):
                                if t not in text_chunks:
                                    text_chunks.append(t)
                                    yield {"type": "text_delta", "text": t}
                        elif event_type == "response.error":
                            e = event.get("error", {})
                            message = (
                                e.get("message", "Unknown error")
                                if isinstance(e, dict)
                                else str(e)
                            )
                            yield {
                                "type": "assistant",
                                "message": Message(
                                    role="assistant",
                                    content=[{"type": "text", "text": message}],
                                    metadata={"is_error": True},
                                ),
                            }
                            return
                        elif event_type == "error":
                            e = event.get("error", {})
                            message = (
                                e.get("message", "Unknown error")
                                if isinstance(e, dict)
                                else str(e)
                            )
                            yield {
                                "type": "assistant",
                                "message": Message(
                                    role="assistant",
                                    content=[{"type": "text", "text": message}],
                                    metadata={"is_error": True},
                                ),
                            }
                            return
                        elif debug_sse:
                            sys.stderr.write(
                                f"[openai-chatgpt] unhandled={event_type} keys={list(event.keys())}\n"
                            )
        except Exception as exc:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": str(exc)}],
                    metadata={"is_error": True},
                ),
            }
            return

        if _response_missing_content(final_response) and response_id:
            fetched = await _fetch_response_by_id(response_id, headers)
            if isinstance(fetched, dict):
                final_response = fetched

        if final_response is None and text_chunks:
            final_response = {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "".join(text_chunks)}],
                    }
                ],
            }

        if streamed_calls:
            final_response = _merge_streamed_calls_into_response(final_response, streamed_calls)

        content_blocks = _response_to_content_blocks(final_response)
        if not _has_meaningful_content_blocks(content_blocks):
            if debug_sse:
                sys.stderr.write(
                    f"[openai-chatgpt] empty content. response_id={response_id!r} "
                    f"chunks={len(text_chunks)} has_final={final_response is not None}\n"
                )
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "OpenAI ChatGPT stream ended without assistant content."}],
                    metadata={"is_error": True},
                ),
            }
            return
        assistant_msg = Message(
            role="assistant",
            content=content_blocks,
            metadata={
                "stop_reason": (final_response or {}).get("status", "end_turn"),
                "usage": {},
                "model": resolved_model,
            },
        )
        yield {"type": "assistant", "message": assistant_msg}


def _build_system_text(system_prompt: str | list[str]) -> str:
    if isinstance(system_prompt, list):
        return "\n\n".join(system_prompt)
    return system_prompt


def _to_responses_input(messages: list[Any], system_prompt: str | list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    sys_text = _build_system_text(system_prompt)
    if sys_text:
        items.append(
            {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": sys_text}],
            }
        )

    for msg in messages:
        role = msg.role if isinstance(msg, Message) else msg.get("role", "user")
        content = msg.content if isinstance(msg, Message) else msg.get("content", "")
        if isinstance(content, str):
            if content:
                items.append(
                    {
                        "type": "message",
                        "role": role,
                        "content": [{"type": "input_text", "text": content}],
                    }
                )
            continue
        if not isinstance(content, list):
            continue

        text_parts: list[str] = []
        for block in content:
            b = block if isinstance(block, dict) else getattr(block, "__dict__", {})
            btype = b.get("type", "")
            if btype == "text":
                text = b.get("text", "")
                if text:
                    text_parts.append(str(text))
            elif btype == "tool_result":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": b.get("tool_use_id", ""),
                        "output": str(b.get("content", "")),
                    }
                )
            elif btype == "tool_use":
                items.append(
                    {
                        "type": "function_call",
                        "call_id": b.get("id", ""),
                        "name": b.get("name", ""),
                        "arguments": json.dumps(b.get("input", {})),
                    }
                )
        if text_parts:
            items.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": "".join(text_parts)}],
                }
            )
    return items


def _to_responses_tools(tools: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in tools:
        name = getattr(tool, "name", "")
        if not name:
            continue
        out.append(
            {
                "type": "function",
                "name": name,
                "description": getattr(tool, "description", ""),
                "parameters": getattr(tool, "input_schema", {}) or {"type": "object"},
            }
        )
    return out


def _response_to_content_blocks(response: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return [{"type": "text", "text": ""}]
    blocks: list[dict[str, Any]] = []

    output_text = response.get("output_text", "")
    if isinstance(output_text, str) and output_text:
        blocks.append({"type": "text", "text": output_text})

    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        itype = item.get("type", "")
        if itype == "message":
            for c in item.get("content", []) or []:
                if not isinstance(c, dict):
                    continue
                ctype = c.get("type", "")
                if ctype in ("output_text", "text"):
                    text = c.get("text", "")
                    if text:
                        blocks.append({"type": "text", "text": str(text)})
                elif ctype == "summary_text":
                    text = c.get("text", "")
                    if text:
                        blocks.append({"type": "text", "text": str(text)})
                elif ctype == "refusal":
                    text = c.get("refusal", "") or c.get("text", "")
                    if text:
                        blocks.append({"type": "text", "text": str(text)})
        elif itype in ("output_text", "text"):
            text = item.get("text", "")
            if text:
                blocks.append({"type": "text", "text": str(text)})
        elif itype == "reasoning":
            for s in item.get("summary", []) or []:
                if isinstance(s, dict):
                    text = s.get("text", "")
                    if text:
                        blocks.append({"type": "text", "text": str(text)})
        elif itype == "function_call":
            args = item.get("arguments", "{}")
            parsed: Any
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                parsed = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": item.get("call_id") or item.get("id", ""),
                    "name": item.get("name", ""),
                    "input": parsed if isinstance(parsed, dict) else {},
                }
            )
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    return blocks


def _response_missing_content(response: dict[str, Any] | None) -> bool:
    if not isinstance(response, dict):
        return True
    if isinstance(response.get("output_text"), str) and response.get("output_text"):
        return False
    output = response.get("output")
    return not (isinstance(output, list) and len(output) > 0)


def _has_meaningful_content_blocks(content_blocks: list[dict[str, Any]]) -> bool:
    if not content_blocks:
        return False
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype != "text":
            return True
        if isinstance(block.get("text"), str) and block.get("text"):
            return True
    return False


def _extract_response_id(event: dict[str, Any]) -> str:
    if not isinstance(event, dict):
        return ""
    rid = event.get("response_id", "")
    if isinstance(rid, str) and rid:
        return rid
    response = event.get("response")
    if isinstance(response, dict):
        rid = response.get("id", "")
        if isinstance(rid, str):
            return rid
    return ""


def _extract_texts_from_event(event: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    if not isinstance(event, dict):
        return texts

    delta = event.get("delta")
    if isinstance(delta, str) and delta:
        texts.append(delta)
    elif isinstance(delta, dict):
        t = delta.get("text", "")
        if isinstance(t, str) and t:
            texts.append(t)

    for key in ("text", "output_text"):
        value = event.get(key, "")
        if isinstance(value, str) and value:
            texts.append(value)

    item = event.get("item")
    if isinstance(item, dict):
        itype = item.get("type", "")
        if itype in ("output_text", "text"):
            t = item.get("text", "")
            if isinstance(t, str) and t:
                texts.append(t)
        if itype == "message":
            for c in item.get("content", []) or []:
                if not isinstance(c, dict):
                    continue
                ctype = c.get("type", "")
                if ctype in ("output_text", "text", "summary_text"):
                    t = c.get("text", "")
                    if isinstance(t, str) and t:
                        texts.append(t)
    return texts


def _accumulate_streamed_function_calls(
    event: dict[str, Any],
    streamed_calls: dict[str, dict[str, Any]],
    streamed_item_to_call: dict[str, str],
) -> None:
    if not isinstance(event, dict):
        return

    def _get_or_create(call_id: str, item_id: str = "") -> dict[str, Any]:
        key = call_id or item_id
        if not key:
            key = f"_unknown_{len(streamed_calls) + 1}"
        row = streamed_calls.get(key)
        if row is None:
            row = {"call_id": call_id or key, "name": "", "arguments": ""}
            streamed_calls[key] = row
        if item_id:
            streamed_item_to_call[item_id] = key
        return row

    item = event.get("item")
    if isinstance(item, dict) and item.get("type") == "function_call":
        call_id = str(item.get("call_id") or "")
        item_id = str(item.get("id") or "")
        row = _get_or_create(call_id, item_id)
        name = item.get("name")
        if isinstance(name, str) and name:
            row["name"] = name
        args = item.get("arguments")
        if isinstance(args, str):
            row["arguments"] = args

    etype = str(event.get("type") or "")
    if "function_call_arguments" not in etype:
        return

    item_id = str(event.get("item_id") or "")
    call_id = str(event.get("call_id") or "")
    key = streamed_item_to_call.get(item_id) or call_id
    row = _get_or_create(call_id, item_id) if not key else streamed_calls.get(key)
    if row is None:
        row = _get_or_create(call_id, item_id)
    delta = event.get("delta")
    if isinstance(delta, str) and delta:
        row["arguments"] = str(row.get("arguments", "")) + delta
    args = event.get("arguments")
    if isinstance(args, str) and args:
        row["arguments"] = args


def _merge_streamed_calls_into_response(
    response: dict[str, Any] | None,
    streamed_calls: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(response, dict):
        out = list(response.get("output") or [])
        base = dict(response)
    else:
        out = []
        base = {"status": "completed"}

    existing_ids = {
        str(item.get("call_id") or item.get("id") or "")
        for item in out
        if isinstance(item, dict) and item.get("type") == "function_call"
    }
    for row in streamed_calls.values():
        call_id = str(row.get("call_id") or "")
        if call_id and call_id in existing_ids:
            continue
        out.append(
            {
                "type": "function_call",
                "call_id": call_id or "",
                "name": str(row.get("name") or ""),
                "arguments": str(row.get("arguments") or "{}"),
            }
        )
    base["output"] = out
    return base


async def _fetch_response_by_id(response_id: str, headers: dict[str, str]) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"https://chatgpt.com/backend-api/codex/responses/{response_id}",
                headers=headers,
            )
        if resp.status_code >= 400:
            return None
        body = resp.json()
        if isinstance(body, dict):
            return body
    except Exception:
        return None
    return None
