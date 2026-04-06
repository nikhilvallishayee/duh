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

from typing import Any, AsyncGenerator

from duh.kernel.deps import Deps
from duh.kernel.messages import (
    Message,
    ToolResultBlock,
    UserMessage,
)


async def query(
    *,
    messages: list[Message],
    system_prompt: str | list[str] = "",
    deps: Deps,
    tools: list[Any] | None = None,
    max_turns: int = 100,
    model: str = "",
    thinking: dict[str, Any] | None = None,
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
            ):
                # Pass through stream events
                event_type = event.get("type", "") if isinstance(event, dict) else ""

                if event_type in ("text_delta", "thinking_delta", "content_block_start",
                                  "content_block_stop", "content_block_delta"):
                    yield event

                elif event_type == "assistant":
                    assistant_message = event.get("message")
                    yield event

                    # Extract tool_use blocks
                    if assistant_message:
                        content = (
                            assistant_message.content
                            if isinstance(assistant_message, Message)
                            else assistant_message.get("content", [])
                            if isinstance(assistant_message, dict)
                            else []
                        )
                        if isinstance(content, list):
                            for block in content:
                                bt = (
                                    block.get("type", "")
                                    if isinstance(block, dict)
                                    else getattr(block, "type", "")
                                )
                                if bt == "tool_use":
                                    tool_use_blocks.append(
                                        block if isinstance(block, dict)
                                        else {"type": "tool_use",
                                              "id": getattr(block, "id", ""),
                                              "name": getattr(block, "name", ""),
                                              "input": getattr(block, "input", {})}
                                    )

        except Exception as e:
            yield {"type": "error", "error": str(e)}
            return

        # --- No tool use → done ---
        if not tool_use_blocks:
            stop_reason = "end_turn"
            if assistant_message and isinstance(assistant_message, Message):
                stop_reason = assistant_message.metadata.get("stop_reason", "end_turn")
            yield {"type": "done", "stop_reason": stop_reason, "turns": turn}
            return

        # --- Execute tools ---
        tool_results: list[ToolResultBlock] = []

        for block in tool_use_blocks:
            tool_id = block.get("id", "")
            tool_name = block.get("name", "")
            tool_input = block.get("input", {})

            yield {"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input}

            # Check approval
            if deps.approve:
                approval = await deps.approve(tool_name, tool_input)
                if not approval.get("allowed", True):
                    reason = approval.get("reason", "Permission denied")
                    result = ToolResultBlock(
                        tool_use_id=tool_id,
                        content=f"Tool use denied: {reason}",
                        is_error=True,
                    )
                    tool_results.append(result)
                    yield {"type": "tool_result", "tool_use_id": tool_id,
                           "output": result.content, "is_error": True}
                    continue

            # Execute
            if deps.run_tool:
                try:
                    output = await deps.run_tool(tool_name, tool_input)
                    result_text = output if isinstance(output, str) else str(output)
                    result = ToolResultBlock(
                        tool_use_id=tool_id,
                        content=result_text,
                    )
                except Exception as e:
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

            tool_results.append(result)
            yield {"type": "tool_result", "tool_use_id": tool_id,
                   "output": result.content, "is_error": result.is_error}

        # --- Build next turn messages ---
        # Add assistant message to history
        if assistant_message:
            if isinstance(assistant_message, Message):
                current_messages.append(assistant_message)
            else:
                current_messages.append(
                    Message(role="assistant", content=assistant_message.get("content", ""))
                )

        # Add tool results as user message
        current_messages.append(
            Message(
                role="user",
                content=[
                    {"type": "tool_result",
                     "tool_use_id": r.tool_use_id,
                     "content": r.content,
                     "is_error": r.is_error}
                    for r in tool_results
                ],
            )
        )

        # Continue the loop (next turn will call the model again)

    # Max turns reached
    yield {"type": "done", "stop_reason": "max_turns", "turns": turn}
