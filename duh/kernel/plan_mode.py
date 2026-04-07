"""Plan mode — two-phase planning and execution.

Wraps an Engine with a structured two-phase approach:

1. **Planning phase**: The model proposes a plan (tool_choice="none",
   so no tool execution is allowed). The plan is a numbered list of
   steps extracted from the model's response.

2. **Execution phase**: After user approval, the model executes the
   plan with full tool access.

    plan_mode = PlanMode(engine)
    async for event in plan_mode.plan("refactor the auth module"):
        handle(event)
    # plan_mode.steps now contains the proposed plan
    # User reviews, then:
    async for event in plan_mode.execute():
        handle(event)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, AsyncGenerator

from duh.kernel.engine import Engine
from duh.kernel.loop import query
from duh.kernel.messages import Message


class PlanState(Enum):
    """Current state of a plan mode session."""
    EMPTY = auto()       # No plan yet
    PLANNING = auto()    # Plan request in flight
    PROPOSED = auto()    # Plan proposed, awaiting approval
    EXECUTING = auto()   # Approved, execution in progress
    DONE = auto()        # Execution complete


@dataclass
class PlanStep:
    """A single step in a plan."""
    number: int
    description: str
    done: bool = False


PLAN_PROMPT_PREFIX = (
    "Create a numbered plan for the following task. "
    "Output ONLY the plan as a numbered list (1. 2. 3. etc.), "
    "with a clear, actionable description for each step. "
    "Do NOT execute anything yet.\n\n"
    "Task: "
)

EXECUTE_PROMPT_PREFIX = (
    "Execute the following plan step by step, using tools as needed. "
    "Here is the approved plan:\n\n"
)


def _parse_steps(text: str) -> list[PlanStep]:
    """Extract numbered steps from model response text.

    Handles patterns like:
        1. Do something
        2. Do something else
        3) Another thing

    Returns a list of PlanStep, preserving order.
    """
    pattern = re.compile(r"^\s*(\d+)[.)]\s+(.+)", re.MULTILINE)
    steps: list[PlanStep] = []
    for match in pattern.finditer(text):
        num = int(match.group(1))
        desc = match.group(2).strip()
        if desc:
            steps.append(PlanStep(number=num, description=desc))
    return steps


class PlanMode:
    """Two-phase plan-then-execute wrapper around an Engine.

    Usage:
        pm = PlanMode(engine)

        # Phase 1: propose a plan (no tool execution)
        async for event in pm.plan("refactor auth"):
            ...
        print(pm.format_plan())

        # Phase 2: user approves, execute with tools
        async for event in pm.execute():
            ...
    """

    def __init__(self, engine: Engine):
        self._engine = engine
        self._steps: list[PlanStep] = []
        self._state: PlanState = PlanState.EMPTY
        self._plan_description: str = ""
        self._raw_plan_text: str = ""

    @property
    def state(self) -> PlanState:
        return self._state

    @property
    def steps(self) -> list[PlanStep]:
        return list(self._steps)

    @property
    def description(self) -> str:
        return self._plan_description

    def format_plan(self) -> str:
        """Format the current plan as a readable string."""
        if not self._steps:
            return "No plan."
        lines = [f"Plan: {self._plan_description}", ""]
        for step in self._steps:
            marker = "[x]" if step.done else "[ ]"
            lines.append(f"  {marker} {step.number}. {step.description}")
        return "\n".join(lines)

    def clear(self) -> None:
        """Clear the current plan and reset state."""
        self._steps = []
        self._state = PlanState.EMPTY
        self._plan_description = ""
        self._raw_plan_text = ""

    async def plan(
        self,
        description: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Phase 1: Ask the model to propose a plan (no tool execution).

        Yields the same events as Engine.run(), but forces
        tool_choice="none" so the model cannot call tools.

        After completion, self.steps contains the parsed plan.
        """
        self._plan_description = description
        self._state = PlanState.PLANNING
        self._steps = []
        self._raw_plan_text = ""

        prompt = PLAN_PROMPT_PREFIX + description

        # Save original tool_choice, override to "none" for planning
        original_tool_choice = self._engine._config.tool_choice
        self._engine._config.tool_choice = "none"

        collected_text: list[str] = []

        try:
            async for event in self._engine.run(prompt):
                event_type = event.get("type", "")

                # Collect text deltas for step parsing
                if event_type == "text_delta":
                    collected_text.append(event.get("text", ""))

                # Also grab the full assistant message text
                if event_type == "assistant":
                    msg = event.get("message")
                    if isinstance(msg, Message):
                        self._raw_plan_text = msg.text

                yield event
        finally:
            # Always restore original tool_choice
            self._engine._config.tool_choice = original_tool_choice

        # If we didn't get text from assistant message, use collected deltas
        if not self._raw_plan_text:
            self._raw_plan_text = "".join(collected_text)

        # Parse steps from the response
        self._steps = _parse_steps(self._raw_plan_text)
        self._state = PlanState.PROPOSED

    async def execute(self) -> AsyncGenerator[dict[str, Any], None]:
        """Phase 2: Execute the approved plan with full tool access.

        The model receives the plan as context and executes it step by step.

        Raises ValueError if no plan has been proposed yet.
        """
        if self._state not in (PlanState.PROPOSED,):
            raise ValueError(
                f"Cannot execute: plan state is {self._state.name}, "
                "expected PROPOSED"
            )

        self._state = PlanState.EXECUTING

        # Build the execution prompt with the plan
        plan_text = "\n".join(
            f"{s.number}. {s.description}" for s in self._steps
        )
        prompt = EXECUTE_PROMPT_PREFIX + plan_text

        async for event in self._engine.run(prompt):
            yield event

        # Mark all steps done (the model executed the full plan)
        for step in self._steps:
            step.done = True
        self._state = PlanState.DONE
