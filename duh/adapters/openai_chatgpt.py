"""OpenAI ChatGPT subscription adapter for Codex-family models.

Uses OAuth access tokens and the ChatGPT backend Codex responses endpoint.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import httpx

from duh.adapters.anthropic import ParsedToolUse
from duh.auth.openai_chatgpt import get_valid_openai_chatgpt_oauth
from duh.kernel.messages import Message
from duh.kernel.untrusted import TaintSource, UntrustedStr


@dataclass
class _StreamState:
    """Mutable state threaded through SSE event dispatch.

    ``text_chunks`` preserves streaming order (for the final
    ``"".join(text_chunks)`` fallback) while ``text_chunks_seen`` gives
    O(1) dedup against ``.done``/``.added`` events that echo already-
    streamed text (PERF-13).  ``final_response`` is populated from
    ``response.completed`` or the fetched-by-id fallback.
    ``streamed_calls`` / ``streamed_item_to_call`` accumulate function-call
    arguments that arrive fragmented across events.  ``events`` is the
    queue of events the outer generator should yield next; ``done``
    signals a terminal error path.
    """

    text_chunks: list[str] = field(default_factory=list)
    text_chunks_seen: set[str] = field(default_factory=set)
    response_id: str = ""
    final_response: dict[str, Any] | None = None
    streamed_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    streamed_item_to_call: dict[str, str] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    model: str = ""


def _wrap_model_output(text: str) -> UntrustedStr:
    """Tag OpenAI ChatGPT provider output as MODEL_OUTPUT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.MODEL_OUTPUT)


def _error_assistant_event(text: str) -> dict[str, Any]:
    """Build a canonical ``type=assistant`` event with ``is_error=True``.

    Used for OAuth failures, HTTP errors, server-reported errors, exceptions,
    and the "stream ended without content" terminal path.
    """
    return {
        "type": "assistant",
        "message": Message(
            role="assistant",
            content=[{"type": "text", "text": text}],
            metadata={"is_error": True},
        ),
    }


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    """Parse one ``data: ...`` SSE line into a JSON event, or ``None``.

    Returns ``None`` for keep-alive lines, ``[DONE]`` sentinels, and lines
    that fail to decode as JSON — the caller should simply skip them.
    """
    if not line.startswith("data: "):
        return None
    raw = line[6:].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _build_request_body(
    *,
    messages: list[Any],
    system_prompt: str | list[str],
    resolved_model: str,
    tools: list[Any] | None,
    max_tokens: int | None,
    tool_choice: str | dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the Codex ``/responses`` POST body from stream() arguments."""
    instructions = _build_system_text(system_prompt).strip()
    if not instructions:
        from duh.constitution import build_system_prompt
        instructions = build_system_prompt()

    body: dict[str, Any] = {
        "model": resolved_model,
        "instructions": instructions,
        "input": _to_responses_input(messages, ""),
        "stream": True,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    if tools:
        body["tools"] = _to_responses_tools(tools)
    if max_tokens:
        body["max_output_tokens"] = max_tokens
    if tool_choice and tools:
        if tool_choice == "any":
            body["tool_choice"] = "required"
        elif tool_choice in ("none", "auto"):
            body["tool_choice"] = tool_choice
        elif isinstance(tool_choice, str):
            body["tool_choice"] = {"type": "function", "name": tool_choice}
    return body


def _validate_oauth(oauth: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return an ``is_error`` assistant event when *oauth* is missing bits.

    Returns ``None`` when the token is usable (access + account_id present).
    The caller can then ``yield`` the event and return without caring which
    specific field was missing.
    """
    if not oauth:
        return _error_assistant_event(
            "OpenAI ChatGPT auth required. Use /connect openai."
        )
    if not oauth.get("access_token") or not oauth.get("account_id"):
        return _error_assistant_event(
            "OpenAI ChatGPT auth is invalid. Re-run /connect openai."
        )
    return None


def _build_request_headers(access: str, account_id: Any) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "chatgpt-account-id": str(account_id),
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }


# ---------------------------------------------------------------------------
# Per-event-type SSE handlers (extracted from _dispatch_sse_event for CC).
# Each handler takes the raw ``event`` dict + the shared ``_StreamState``
# and mutates the state in place (appending to ``events``, accumulating
# ``text_chunks``, or setting ``final_response`` / ``done``).
# ---------------------------------------------------------------------------


def _handle_text_delta(event: dict[str, Any], state: _StreamState) -> None:
    delta = event.get("delta", "")
    if not delta:
        return
    delta_s = str(delta)
    state.text_chunks.append(delta_s)
    state.text_chunks_seen.add(delta_s)
    state.events.append({"type": "text_delta", "text": delta_s})


def _handle_text_done(event: dict[str, Any], state: _StreamState) -> None:
    text = event.get("text", "")
    if not text:
        return
    text_s = str(text)
    if text_s in state.text_chunks_seen:
        return
    state.text_chunks.append(text_s)
    state.text_chunks_seen.add(text_s)
    state.events.append({"type": "text_delta", "text": text_s})


def _handle_response_completed(event: dict[str, Any], state: _StreamState) -> None:
    resp_obj = event.get("response")
    if isinstance(resp_obj, dict):
        state.final_response = resp_obj


def _handle_error_event(event: dict[str, Any], state: _StreamState) -> None:
    """Queue an ``is_error`` assistant event and mark the stream done."""
    e = event.get("error", {})
    message = (
        e.get("message", "Unknown error")
        if isinstance(e, dict)
        else str(e)
    )
    state.events.append(_error_assistant_event(message))
    state.done = True


def _handle_generic_delta(event: dict[str, Any], state: _StreamState) -> None:
    event_type = str(event.get("type", ""))
    if "function_call_arguments" in event_type:
        return
    for t in _extract_texts_from_event(event):
        state.text_chunks.append(t)
        state.text_chunks_seen.add(t)
        state.events.append({"type": "text_delta", "text": t})


def _handle_generic_done_or_added(event: dict[str, Any], state: _StreamState) -> None:
    event_type = str(event.get("type", ""))
    if "function_call_arguments" in event_type:
        return
    for t in _extract_texts_from_event(event):
        if t not in state.text_chunks_seen:
            state.text_chunks.append(t)
            state.text_chunks_seen.add(t)
            state.events.append({"type": "text_delta", "text": t})


# Exact-match dispatch table for the fast path.  Suffix-based types
# (".delta"/".done"/".added") are handled separately after lookup.
_EXACT_SSE_HANDLERS: dict[str, Any] = {
    "response.output_text.delta": _handle_text_delta,
    "response.output_text.done": _handle_text_done,
    "response.completed": _handle_response_completed,
    "response.done": _handle_response_completed,
    "response.error": _handle_error_event,
    "error": _handle_error_event,
}


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
        """Stream a Codex ``/responses`` turn.

        Thin coordinator: validates auth, builds the request, delegates the
        SSE pump to :meth:`_pump_sse_events`, and calls
        :meth:`_build_final_response` for the terminal assistant event.
        Individual SSE event dispatch lives in :meth:`_dispatch_sse_event`.
        """
        debug_sse = os.environ.get("DUH_OPENAI_CHATGPT_DEBUG", "") == "1"
        oauth = get_valid_openai_chatgpt_oauth()
        auth_error = _validate_oauth(oauth)
        if auth_error is not None:
            yield auth_error
            return

        resolved_model = (model or self._default_model).strip() or "gpt-5.2-codex"
        access = oauth.get("access_token", "")
        account_id = oauth.get("account_id", "")
        request_body = _build_request_body(
            messages=messages,
            system_prompt=system_prompt,
            resolved_model=resolved_model,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
        )
        headers = _build_request_headers(access, account_id)
        state = _StreamState(model=resolved_model)

        try:
            async for ev in self._pump_sse_events(
                request_body=request_body,
                headers=headers,
                state=state,
                debug_sse=debug_sse,
            ):
                yield ev
        except Exception as exc:
            yield _error_assistant_event(str(exc))
            return

        if state.done:
            return
        yield await self._build_final_response(state, headers, debug_sse=debug_sse)

    async def _pump_sse_events(
        self,
        *,
        request_body: dict[str, Any],
        headers: dict[str, str],
        state: _StreamState,
        debug_sse: bool,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Open the SSE stream and yield per-event output until EOS.

        Yields the same events a caller of :meth:`stream` would see during
        the streaming phase (text deltas, and error-assistant events on
        HTTP/server failures).  Sets ``state.done`` on terminal paths so
        the outer :meth:`stream` knows whether to run
        :meth:`_build_final_response`.
        """
        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream(
                "POST",
                "https://chatgpt.com/backend-api/codex/responses",
                headers=headers,
                json=request_body,
            ) as resp:
                if resp.status_code >= 400:
                    raw_body = await resp.aread()
                    err = raw_body.decode("utf-8", errors="replace")[:500]
                    state.done = True
                    yield _error_assistant_event(
                        f"OpenAI ChatGPT error ({resp.status_code}): {err}"
                    )
                    return

                async for line in resp.aiter_lines():
                    event = _parse_sse_line(line)
                    if event is None:
                        continue
                    self._dispatch_sse_event(event, state, debug_sse=debug_sse)
                    for out in state.events:
                        yield out
                    state.events.clear()
                    if state.done:
                        return

    def _dispatch_sse_event(
        self,
        event: dict[str, Any],
        state: _StreamState,
        *,
        debug_sse: bool = False,
    ) -> None:
        """Route a single SSE ``event`` to the correct state update.

        All caller-visible events are appended to ``state.events``; setting
        ``state.done`` signals a terminal error path.  Per-event-type
        handlers live in ``_SSE_HANDLERS`` below; this method just looks
        up the right one by exact type and suffix.
        """
        event_type = event.get("type", "")
        if debug_sse:
            sys.stderr.write(f"[openai-chatgpt] event={event_type}\n")

        rid = _extract_response_id(event)
        if rid:
            state.response_id = rid
        _accumulate_streamed_function_calls(
            event, state.streamed_calls, state.streamed_item_to_call,
        )

        handler = _EXACT_SSE_HANDLERS.get(event_type)
        if handler is not None:
            handler(event, state)
            return
        if event_type.endswith(".delta"):
            _handle_generic_delta(event, state)
            return
        if event_type.endswith(".done") or event_type.endswith(".added"):
            _handle_generic_done_or_added(event, state)
            return
        if debug_sse:
            sys.stderr.write(
                f"[openai-chatgpt] unhandled={event_type} keys={list(event.keys())}\n"
            )

    async def _build_final_response(
        self,
        state: _StreamState,
        headers: dict[str, str],
        *,
        debug_sse: bool = False,
    ) -> dict[str, Any]:
        """Assemble the terminal ``assistant`` event from *state*.

        Runs the three recovery paths that follow a successful SSE stream:
        (1) fetch-by-id when the completion event carried no content,
        (2) synthesize a minimal response from streamed text chunks, and
        (3) merge fragmented function-call arguments back into the response.
        Returns an error event when no meaningful content blocks result.
        """
        final_response = state.final_response
        if _response_missing_content(final_response) and state.response_id:
            fetched = await _fetch_response_by_id(state.response_id, headers)
            if isinstance(fetched, dict):
                final_response = fetched

        if final_response is None and state.text_chunks:
            final_response = {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "".join(state.text_chunks)},
                        ],
                    }
                ],
            }

        if state.streamed_calls:
            final_response = _merge_streamed_calls_into_response(
                final_response, state.streamed_calls,
            )

        content_blocks = _response_to_content_blocks(final_response)
        if not _has_meaningful_content_blocks(content_blocks):
            if debug_sse:
                sys.stderr.write(
                    f"[openai-chatgpt] empty content. response_id={state.response_id!r} "
                    f"chunks={len(state.text_chunks)} has_final={final_response is not None}\n"
                )
            return _error_assistant_event(
                "OpenAI ChatGPT stream ended without assistant content."
            )

        assistant_msg = Message(
            role="assistant",
            content=content_blocks,
            metadata={
                "stop_reason": (final_response or {}).get("status", "end_turn"),
                "usage": {},
                "model": state.model,
            },
        )
        return {"type": "assistant", "message": assistant_msg}


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
