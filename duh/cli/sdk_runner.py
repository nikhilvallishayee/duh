"""Stream-JSON runner for Claude Agent SDK compatibility.

Implements the NDJSON bidirectional protocol used by the Claude Agent SDK.
The SDK launches the CLI with --output-format stream-json --input-format stream-json
and communicates via stdin/stdout.

Protocol flow:
    1. SDK sends initialize control_request on stdin
    2. CLI responds with control_response on stdout
    3. SDK sends user messages on stdin
    4. CLI emits assistant/user/result messages on stdout
    5. SDK reads until result message, then closes stdin
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid as _uuid
from dataclasses import asdict
from typing import Any

from pathlib import Path

from duh.kernel.confirmation import ConfirmationMinter
from duh.kernel.untrusted import TaintSource, UntrustedStr


def load_preconfirm_allowlist(
    path: Path, minter: ConfirmationMinter, session_id: str
) -> list[str]:
    """Load a JSON allowlist and pre-mint tokens for each entry."""
    data = json.loads(path.read_text())
    tokens: list[str] = []
    for entry in data:
        token = minter.mint(session_id, entry["tool"], entry["input"])
        tokens.append(token)
    return tokens


def wrap_stream_user_message(msg: dict) -> dict:
    """Tag the content string of a stream-json user message as USER_INPUT."""
    content = msg.get("content", "")
    if isinstance(content, str) and not isinstance(content, UntrustedStr):
        msg = dict(msg)
        msg["content"] = UntrustedStr(content, TaintSource.USER_INPUT)
    return msg

from duh.adapters.anthropic import AnthropicProvider  # noqa: F401 (test/mocking compatibility)
from duh.adapters.native_executor import NativeExecutor
from duh.adapters.approvers import AutoApprover, InteractiveApprover
from duh.cli.ndjson import ndjson_read_line, ndjson_write
from duh.cli.runner import SYSTEM_PROMPT, _interpret_error
from duh.providers.registry import build_model_backend, resolve_provider_name
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.tools.registry import get_all_tools

logger = logging.getLogger("duh")


# ---------------------------------------------------------------------------
# Message formatting — D.U.H. events → SDK-compatible NDJSON
# ---------------------------------------------------------------------------

def _format_assistant_message(
    msg: Message,
    session_id: str,
    model: str,
) -> dict[str, Any]:
    """Format a D.U.H. Message as an SDK-compatible assistant message."""
    content_blocks: list[dict[str, Any]] = []

    if isinstance(msg.content, str):
        if msg.content:
            content_blocks.append({"type": "text", "text": msg.content})
    else:
        for block in msg.content:
            if isinstance(block, dict):
                content_blocks.append(block)
            elif hasattr(block, "__dataclass_fields__"):
                content_blocks.append(asdict(block))
            else:
                content_blocks.append({"type": "text", "text": str(block)})

    return {
        "type": "assistant",
        "message": {
            "content": content_blocks,
            "model": model,
            "id": msg.id,
            "stop_reason": msg.metadata.get("stop_reason", "end_turn"),
            "usage": msg.metadata.get("usage", {}),
        },
        "session_id": session_id,
        "uuid": str(_uuid.uuid4()),
    }


def _format_user_tool_results(
    tool_results: list[dict[str, Any]],
    session_id: str,
) -> dict[str, Any]:
    """Format tool results as an SDK-compatible user message."""
    content_blocks = []
    for tr in tool_results:
        content_blocks.append({
            "type": "tool_result",
            "tool_use_id": tr.get("tool_use_id", ""),
            "content": tr.get("output", ""),
            "is_error": tr.get("is_error", False),
        })
    return {
        "type": "user",
        "message": {"role": "user", "content": content_blocks},
        "session_id": session_id,
        "uuid": str(_uuid.uuid4()),
    }


def _format_result(
    session_id: str,
    *,
    is_error: bool = False,
    num_turns: int = 0,
    stop_reason: str = "end_turn",
    result_text: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Format a final result message."""
    return {
        "type": "result",
        "subtype": "error" if is_error else "success",
        "duration_ms": duration_ms,
        "duration_api_ms": 0,
        "is_error": is_error,
        "num_turns": num_turns,
        "session_id": session_id,
        "stop_reason": stop_reason,
        "total_cost_usd": 0,
        "usage": {},
        "result": result_text,
        "uuid": str(_uuid.uuid4()),
    }


# ---------------------------------------------------------------------------
# Control protocol
# ---------------------------------------------------------------------------

def _handle_control_request(msg: dict[str, Any]) -> dict[str, Any]:
    """Handle a control_request from the SDK and return a control_response."""
    request_id = msg.get("request_id", "")
    request = msg.get("request", {})
    subtype = request.get("subtype", "")

    if subtype == "initialize":
        return {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": {
                    "protocol_version": "1.0",
                },
            },
        }

    # Default: acknowledge with empty success
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": {},
        },
    }


# ---------------------------------------------------------------------------
# Stream-JSON mode
# ---------------------------------------------------------------------------

async def run_stream_json_mode(args: argparse.Namespace) -> int:
    """Run in stream-json mode for SDK compatibility.

    Reads NDJSON from stdin, processes through the engine, writes NDJSON to stdout.
    """
    debug = args.debug or getattr(args, "verbose", False)
    if debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                            format="[%(levelname)s] %(name)s: %(message)s")

    # --- Resolve provider ---
    def _check_ollama() -> bool:
        try:
            import httpx
            r = httpx.get("http://localhost:11434/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    provider_name = resolve_provider_name(
        explicit_provider=args.provider,
        model=getattr(args, "model", None),
        check_ollama=_check_ollama,
    )

    if not provider_name:
        ndjson_write(_format_result(
            "", is_error=True, result_text="No provider available",
        ))
        return 1

    backend = build_model_backend(provider_name, getattr(args, "model", None))
    if not backend.ok:
        ndjson_write(_format_result(
            "", is_error=True, result_text=backend.error or f"Unknown provider: {provider_name}",
        ))
        return 1
    model = backend.model
    call_model = backend.call_model

    cwd = os.getcwd()
    tools = list(get_all_tools())

    # --- Build system prompt ---
    system_prompt = args.system_prompt or SYSTEM_PROMPT

    # --- Build engine ---
    executor = NativeExecutor(tools=tools, cwd=cwd)
    # SDK mode always uses AutoApprover: the SDK itself handles permission
    # control via the bidirectional control protocol (control_request /
    # control_response).  InteractiveApprover would block on stdin which is
    # reserved for the NDJSON stream, so auto-approve is the only correct
    # choice here regardless of --dangerously-skip-permissions.
    approver: Any = AutoApprover()

    deps = Deps(
        call_model=call_model,
        run_tool=executor.run,
        approve=approver.check,
    )
    engine_config = EngineConfig(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        max_turns=args.max_turns,
    )
    engine = Engine(deps=deps, config=engine_config)
    session_id = engine.session_id

    # --- Read stdin NDJSON ---
    import time
    start_time = time.monotonic()
    had_error = False
    total_turns = 0

    for line in sys.stdin:
        msg = ndjson_read_line(line)
        if msg is None:  # pragma: no cover - malformed line skipped
            continue

        msg_type = msg.get("type", "")

        # Handle control protocol
        if msg_type == "control_request":
            response = _handle_control_request(msg)
            ndjson_write(response)
            continue

        if msg_type == "control_response":
            # SDK responding to our request — not used yet
            continue

        # Handle user messages
        if msg_type == "user":
            user_content = msg.get("message", {}).get("content", "")
            if not user_content:
                continue

            # Collect tool results from the run
            pending_tool_results: list[dict[str, Any]] = []

            async for event in engine.run(user_content):
                event_type = event.get("type", "")

                if event_type == "assistant":
                    # Emit the full assistant message
                    assistant_msg = event.get("message")
                    if isinstance(assistant_msg, Message):
                        ndjson_write(_format_assistant_message(
                            assistant_msg, session_id, model,
                        ))

                elif event_type == "tool_use":
                    # Tool use is part of the assistant message (already emitted)
                    pass

                elif event_type == "tool_result":
                    pending_tool_results.append(event)

                elif event_type == "error":
                    hint = _interpret_error(event.get("error", "unknown"))
                    had_error = True
                    logger.debug("Engine error: %s", hint)

                elif event_type == "done":
                    total_turns = event.get("turns", total_turns)

                    # Emit pending tool results as user message
                    if pending_tool_results:
                        ndjson_write(_format_user_tool_results(
                            pending_tool_results, session_id,
                        ))
                        pending_tool_results.clear()

    # --- Emit result ---
    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    result_text = ""
    if engine.messages:
        last = engine.messages[-1]
        if last.role == "assistant":
            result_text = last.text

    ndjson_write(_format_result(
        session_id,
        is_error=had_error,
        num_turns=total_turns,
        stop_reason="end_turn",
        result_text=result_text,
        duration_ms=elapsed_ms,
    ))

    return 1 if had_error else 0
