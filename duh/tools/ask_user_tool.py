"""AskUserQuestion tool — prompts the user for input during execution.

This tool blocks execution and asks the user a question via the
provided ask_fn callback. The user's response is returned to the model.

    async def terminal_input(question: str) -> str:
        return input(f"  {question}\\n  > ")

    tool = AskUserQuestionTool(ask_fn=terminal_input)
    result = await tool.call({"question": "Which file?"}, context)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from duh.kernel.confirmation import ConfirmationMinter
from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability


def _mint_answer_token(
    minter: ConfirmationMinter, session_id: str, tool: str, input_obj: dict
) -> str:
    """Mint a confirmation token when the user answers an AskUserQuestion."""
    return minter.mint(session_id, tool, input_obj)

AskFn = Callable[[str], Awaitable[str]]


class AskUserQuestionTool:
    """Blocks execution and prompts the user for a response."""

    name = "AskUserQuestion"
    capabilities = Capability.NONE
    description = (
        "Ask the user a question and wait for their response. "
        "Use when you need clarification or a decision from the user."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
        },
        "required": ["question"],
    }

    def __init__(self, ask_fn: AskFn | None = None) -> None:
        self._ask_fn = ask_fn

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        question = input.get("question", "").strip()
        if not question:
            return ToolResult(
                output="Question cannot be empty.",
                is_error=True,
            )

        if self._ask_fn is None:
            return ToolResult(
                output="No input handler available (non-interactive mode).",
                is_error=True,
            )

        try:
            answer = await self._ask_fn(question)
            return ToolResult(output=answer)
        except (EOFError, KeyboardInterrupt):
            return ToolResult(output="(user cancelled)")
        except Exception as e:
            return ToolResult(
                output=f"Failed to get user input: {e}",
                is_error=True,
            )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
