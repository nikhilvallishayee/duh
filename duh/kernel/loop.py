"""The query loop — the beating heart of D.U.H.

An async generator that implements the universal agentic cycle:

    prompt → model → [tool_use → tool_result →]* response

This is the most important file in the project. It must:
1. Pass all tests (Kent Beck Rule 1)
2. Reveal its intention through clear naming (Rule 2)
3. Have no duplication (Rule 3)
4. Have no unnecessary complexity (Rule 4)

The loop is provider-agnostic. It receives events from `deps.call_model`
and dispatches tool calls via `deps.run_tool`. It never imports a
provider SDK. It never touches the filesystem. It never renders UI.

    async for event in query(messages, deps):
        match event:
            case {"type": "text_delta", "text": text}:
                print(text, end="", flush=True)
            case {"type": "assistant", "message": msg}:
                print()  # newline after streaming
            case {"type": "tool_use", "name": name}:
                print(f"Using {name}...")
            case {"type": "error", "error": err}:
                print(f"Error: {err}")
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncGenerator

from duh.hooks import HookEvent, execute_hooks
from duh.kernel.deps import Deps
from duh.kernel.messages import (
    Message,
    ToolResultBlock,
    UserMessage,
)

# Max chars per tool result sent to the model (prevents context explosion)
MAX_RESULT_SIZE = 80_000


def _build_read_only_set(tools: list[Any] | None) -> set[str]:
    """Return the set of tool names whose ``is_read_only`` is True.

    Defensive — any tool without the attribute is treated as mutating
    (the safe default).  Used by the loop to decide which tool_use
    blocks can run concurrently in a single turn.
    """
    if not tools:
        return set()
    out: set[str] = set()
    for t in tools:
        try:
            if getattr(t, "is_read_only", False):
                out.add(getattr(t, "name", ""))
        except Exception:
            continue
    out.discard("")
    return out


def _extract_tool_use_blocks(content: Any) -> list[dict[str, Any]]:
    """Extract tool_use blocks from message content (list of blocks)."""
    if not isinstance(content, list):
        return []
    blocks = []
    for block in content:
        bt = block.get("type", "") if isinstance(block, dict) else getattr(block, "type", "")
        if bt == "tool_use":
            if isinstance(block, dict):
                blocks.append(block)
            else:
                blocks.append({
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", {}),
                })
    return blocks


def _get_content(msg: Any) -> Any:
    """Get content from a Message or dict."""
    if isinstance(msg, Message):
        return msg.content
    if isinstance(msg, dict):
        return msg.get("content", [])
    return []


def _get_stop_reason(msg: Any) -> str:
    """Extract stop_reason from assistant message metadata."""
    if isinstance(msg, Message):
        return msg.metadata.get("stop_reason", "end_turn")
    if isinstance(msg, dict):
        return msg.get("metadata", {}).get("stop_reason", "end_turn")
    return "end_turn"


def _is_partial(msg: Any) -> bool:
    """Check if message is a partial (mid-stream error) response."""
    if isinstance(msg, Message):
        return msg.metadata.get("partial", False)
    if isinstance(msg, dict):
        return msg.get("metadata", {}).get("partial", False)
    return False


def _to_message(msg: Any) -> Message:
    """Ensure msg is a Message object."""
    if isinstance(msg, Message):
        return msg
    return Message(role="assistant", content=msg.get("content", "") if isinstance(msg, dict) else "")


def _truncate_result(text: str) -> str:
    """Truncate tool result to MAX_RESULT_SIZE to prevent context explosion."""
    if len(text) <= MAX_RESULT_SIZE:
        return text
    return text[:MAX_RESULT_SIZE] + f"\n... (truncated, {len(text) - MAX_RESULT_SIZE} chars omitted)"


class _BlockOutcome:
    """The result of a single tool_use block, with audit info."""

    __slots__ = ("result",)

    def __init__(self, result: ToolResultBlock) -> None:
        self.result = result


async def _execute_block(block: dict[str, Any], deps: Deps) -> _BlockOutcome:
    """Execute a single read-only tool_use block.

    Used by the parallel branch in :func:`query`. Hook emission and audit
    logging still happen here, but no events are yielded to the caller —
    the caller emits the ``tool_use`` and ``tool_result`` events itself
    in deterministic submission order.
    """
    tool_id = block.get("id", "")
    tool_name = block.get("name", "")
    tool_input = block.get("input", {})
    t0 = time.monotonic()

    # Approval
    if deps.approve:
        if deps.hook_registry:
            await execute_hooks(
                deps.hook_registry,
                HookEvent.PERMISSION_REQUEST,
                {"tool_name": tool_name, "input": tool_input},
                matcher_value=tool_name,
            )
        approval = await deps.approve(tool_name, tool_input)
        if not approval.get("allowed", True):
            reason = approval.get("reason", "Permission denied")
            if deps.hook_registry:
                await execute_hooks(
                    deps.hook_registry,
                    HookEvent.PERMISSION_DENIED,
                    {"tool_name": tool_name, "input": tool_input, "reason": reason},
                    matcher_value=tool_name,
                )
            result = ToolResultBlock(
                tool_use_id=tool_id,
                content=f"Tool use denied: {reason}",
                is_error=True,
            )
            _audit(deps, tool_name, tool_input, "denied", t0)
            return _BlockOutcome(result)

    # Confirmation gate
    if deps.confirm_gate:
        gate_decision = deps.confirm_gate(tool_name=tool_name, tool_input=tool_input)
        if gate_decision is not None and gate_decision.action == "block":
            result = ToolResultBlock(
                tool_use_id=tool_id,
                content=f"Tool blocked: {gate_decision.reason}",
                is_error=True,
            )
            _audit(deps, tool_name, tool_input, "denied", t0)
            return _BlockOutcome(result)

    # Execute
    if deps.run_tool:
        try:
            output = await deps.run_tool(tool_name, tool_input)
            result_text = _truncate_result(
                output if isinstance(output, str) else str(output)
            )
            result = ToolResultBlock(tool_use_id=tool_id, content=result_text)
        except Exception as e:
            if deps.hook_registry:
                await execute_hooks(
                    deps.hook_registry,
                    HookEvent.POST_TOOL_USE_FAILURE,
                    {"tool_name": tool_name, "error": str(e)},
                    matcher_value=tool_name,
                )
            result = ToolResultBlock(
                tool_use_id=tool_id,
                content=f"Tool error: {e}",
                is_error=True,
            )
    else:
        result = ToolResultBlock(
            tool_use_id=tool_id,
            content="No tool executor configured",
            is_error=True,
        )

    _audit(
        deps, tool_name, tool_input,
        "error" if result.is_error else "ok",
        t0,
    )
    return _BlockOutcome(result)


async def _execute_block_stream(block: dict[str, Any], deps: Deps):
    """Streaming variant for mutating (sequential) tool execution.

    Yields ``(event, result)`` pairs. ``event`` is None when there is no
    event to emit; ``result`` is None until the final tuple, where it
    carries the :class:`ToolResultBlock`.
    """
    tool_id = block.get("id", "")
    tool_name = block.get("name", "")
    tool_input = block.get("input", {})

    yield ({"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input}, None)
    t0 = time.monotonic()

    # Approval
    if deps.approve:
        if deps.hook_registry:
            await execute_hooks(
                deps.hook_registry,
                HookEvent.PERMISSION_REQUEST,
                {"tool_name": tool_name, "input": tool_input},
                matcher_value=tool_name,
            )
        approval = await deps.approve(tool_name, tool_input)
        if not approval.get("allowed", True):
            reason = approval.get("reason", "Permission denied")
            if deps.hook_registry:
                await execute_hooks(
                    deps.hook_registry,
                    HookEvent.PERMISSION_DENIED,
                    {"tool_name": tool_name, "input": tool_input, "reason": reason},
                    matcher_value=tool_name,
                )
            result = ToolResultBlock(
                tool_use_id=tool_id,
                content=f"Tool use denied: {reason}",
                is_error=True,
            )
            yield (
                {"type": "tool_result", "tool_use_id": tool_id,
                 "output": result.content, "is_error": True},
                result,
            )
            _audit(deps, tool_name, tool_input, "denied", t0)
            return

    # Confirmation gate
    if deps.confirm_gate:
        gate_decision = deps.confirm_gate(tool_name=tool_name, tool_input=tool_input)
        if gate_decision is not None and gate_decision.action == "block":
            result = ToolResultBlock(
                tool_use_id=tool_id,
                content=f"Tool blocked: {gate_decision.reason}",
                is_error=True,
            )
            yield (
                {"type": "tool_result", "tool_use_id": tool_id,
                 "output": result.content, "is_error": True},
                result,
            )
            _audit(deps, tool_name, tool_input, "denied", t0)
            return

    # Execute
    if deps.run_tool:
        try:
            output = await deps.run_tool(tool_name, tool_input)
            result_text = _truncate_result(
                output if isinstance(output, str) else str(output)
            )
            result = ToolResultBlock(tool_use_id=tool_id, content=result_text)
        except Exception as e:
            if deps.hook_registry:
                await execute_hooks(
                    deps.hook_registry,
                    HookEvent.POST_TOOL_USE_FAILURE,
                    {"tool_name": tool_name, "error": str(e)},
                    matcher_value=tool_name,
                )
            result = ToolResultBlock(
                tool_use_id=tool_id,
                content=f"Tool error: {e}",
                is_error=True,
            )
    else:
        result = ToolResultBlock(
            tool_use_id=tool_id,
            content="No tool executor configured",
            is_error=True,
        )

    yield (
        {"type": "tool_result", "tool_use_id": tool_id,
         "output": result.content, "is_error": result.is_error},
        result,
    )
    _audit(
        deps, tool_name, tool_input,
        "error" if result.is_error else "ok",
        t0,
    )


def _audit(
    deps: Deps,
    tool_name: str,
    tool_input: Any,
    status: str,
    t0: float,
) -> None:
    """Emit a structured audit-log entry for a tool invocation."""
    if not deps.audit_logger:
        return
    elapsed = int((time.monotonic() - t0) * 1000)
    deps.audit_logger.log_tool_call(
        session_id=deps.session_id,
        tool_name=tool_name,
        tool_input=tool_input if isinstance(tool_input, dict) else {},
        result_status=status,
        duration_ms=elapsed,
    )


async def query(
    *,
    messages: list[Message],
    system_prompt: str | list[str] = "",
    deps: Deps,
    tools: list[Any] | None = None,
    max_turns: int = 1000,
    model: str = "",
    thinking: dict[str, Any] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """The core agentic loop.

    Yields events as they happen:
    - {"type": "text_delta", "text": "..."} — streaming text
    - {"type": "thinking_delta", "text": "..."} — streaming thinking
    - {"type": "tool_use", "id": "...", "name": "...", "input": {...}} — tool call
    - {"type": "tool_result", "tool_use_id": "...", "output": "..."} — tool result
    - {"type": "assistant", "message": Message} — complete assistant message
    - {"type": "error", "error": "..."} — error
    - {"type": "done", "stop_reason": "..."} — loop finished
    """
    if not deps.call_model:
        yield {"type": "error", "error": "No model provider configured"}
        return

    turn = 0
    current_messages = list(messages)

    while turn < max_turns:
        turn += 1

        # --- Call the model ---
        assistant_message: Message | None = None
        tool_use_blocks: list[dict[str, Any]] = []

        try:
            async for event in deps.call_model(
                messages=current_messages,
                system_prompt=system_prompt,
                model=model,
                tools=tools,
                thinking=thinking,
                tool_choice=tool_choice,
            ):
                event_type = event.get("type", "") if isinstance(event, dict) else ""

                if event_type in ("text_delta", "thinking_delta", "content_block_start",
                                  "content_block_stop", "content_block_delta"):
                    yield event

                elif event_type == "assistant":
                    assistant_message = event.get("message")
                    yield event

                    if _is_partial(assistant_message):
                        yield {"type": "done", "stop_reason": "error", "turns": turn}
                        return

                    tool_use_blocks = _extract_tool_use_blocks(
                        _get_content(assistant_message)
                    )

        except Exception as e:
            yield {"type": "error", "error": str(e)}
            return

        # --- No tool use → done ---
        if not tool_use_blocks:
            stop_reason = _get_stop_reason(assistant_message) if assistant_message else "end_turn"
            yield {"type": "done", "stop_reason": stop_reason, "turns": turn}
            return

        # --- Execute tools ---
        # PERF-10: group consecutive read-only tool_use blocks and run
        # them concurrently. Mutating tools always run sequentially so
        # ordering / on-disk effects are preserved.
        read_only_names = _build_read_only_set(tools)
        tool_results: list[ToolResultBlock] = []

        # Build the per-block "plan": a list of (block, is_read_only) pairs.
        plan = [
            (block, block.get("name", "") in read_only_names)
            for block in tool_use_blocks
        ]

        i = 0
        while i < len(plan):
            block, is_ro = plan[i]
            if is_ro:
                # Collect the maximal run of consecutive read-only blocks.
                group: list[dict[str, Any]] = []
                while i < len(plan) and plan[i][1]:
                    group.append(plan[i][0])
                    i += 1

                # Yield tool_use events first so the UI sees activity.
                for b in group:
                    yield {
                        "type": "tool_use",
                        "id": b.get("id", ""),
                        "name": b.get("name", ""),
                        "input": b.get("input", {}),
                    }

                # Run all read-only executions concurrently.
                tasks = [
                    asyncio.create_task(_execute_block(b, deps))
                    for b in group
                ]
                outcomes = await asyncio.gather(*tasks)

                # Emit results in submission order, preserving correctness.
                for outcome in outcomes:
                    tool_results.append(outcome.result)
                    yield {
                        "type": "tool_result",
                        "tool_use_id": outcome.result.tool_use_id,
                        "output": outcome.result.content,
                        "is_error": outcome.result.is_error,
                    }
            else:
                # Mutating tool — execute sequentially via the streaming path
                # so we preserve interleaved hook events.
                async for evt, maybe_result in _execute_block_stream(block, deps):
                    if evt is not None:
                        yield evt
                    if maybe_result is not None:
                        tool_results.append(maybe_result)
                i += 1

        # --- Build next turn messages ---
        if assistant_message:
            current_messages.append(_to_message(assistant_message))

        # All tool results in ONE user message (required by Anthropic API)
        tool_result_msg = Message(
            role="user",
            content=[
                {"type": "tool_result",
                 "tool_use_id": r.tool_use_id,
                 "content": r.content,
                 "is_error": r.is_error}
                for r in tool_results
            ],
        )
        current_messages.append(tool_result_msg)

        # Yield the tool_result message so the engine can capture it
        yield {"type": "tool_result_message", "message": tool_result_msg}

    # Max turns reached — grace turn: let the model summarize without tools
    yield {
        "type": "text_delta",
        "text": f"\n\n---\n*Reached {max_turns}-turn limit. Summarizing...*\n\n",
    }

    try:
        grace_messages = list(current_messages)
        grace_messages.append(Message(
            role="user",
            content=(
                f"You've reached the {max_turns}-turn limit. "
                "Give a brief summary of what you accomplished and what remains. "
                "Do NOT use any tools — just respond with text."
            ),
        ))
        async for event in deps.call_model(
            messages=grace_messages,
            model=model,
            system_prompt=system_prompt,
            tools=[],  # no tools — text only
        ):
            if event.get("type") in ("text_delta", "thinking_delta"):
                yield event
    except Exception:
        pass  # grace turn is best-effort

    yield {"type": "done", "stop_reason": "max_turns", "turns": turn}
