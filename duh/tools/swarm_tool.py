"""Swarm tool — spawn multiple subagents in parallel.

Uses asyncio.gather to run N agents concurrently and collects all results.
Each child agent gets the parent's deps and tools (minus Agent and Swarm
to prevent recursion).

See ADR-063 for design rationale.
"""
from __future__ import annotations

import asyncio
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability
from duh.agents import AGENT_TYPES


class SwarmTool:
    name = "Swarm"
    capabilities = Capability.EXEC
    description = (
        "Spawn multiple subagents in parallel. Each gets its own conversation "
        "and can use all tools (Read, Bash, Grep, etc.) but cannot spawn "
        "further agents or swarms. Use for parallelizable subtasks."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "description": "List of tasks to run in parallel.",
                "items": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The task for the subagent.",
                        },
                        "agent_type": {
                            "type": "string",
                            "enum": AGENT_TYPES,
                            "description": "Agent specialization. Default: general.",
                            "default": "general",
                        },
                        "model": {
                            "type": "string",
                            "enum": ["haiku", "sonnet", "opus", "inherit"],
                            "description": "Model for subagent. Default: inherit.",
                            "default": "inherit",
                        },
                    },
                    "required": ["prompt"],
                },
            },
        },
        "required": ["tasks"],
    }
    is_read_only = False
    is_destructive = False

    def __init__(
        self,
        *,
        parent_deps: Any = None,
        parent_tools: list[Any] | None = None,
    ):
        self._parent_deps = parent_deps
        self._parent_tools = parent_tools

    def _child_tools(self) -> list[Any]:
        """Return parent tools minus Agent and Swarm (prevent recursion)."""
        if not self._parent_tools:
            return []
        excluded = {"Agent", "Swarm"}
        return [t for t in self._parent_tools if getattr(t, "name", "") not in excluded]

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        if self._parent_deps is None:
            return ToolResult(output="Swarm error: no parent deps configured", is_error=True)

        tasks_input = input.get("tasks", [])
        if not tasks_input:
            return ToolResult(output="Swarm error: no tasks provided", is_error=True)

        from duh.agents import run_agent

        child_tools = self._child_tools()

        async def _run_one(index: int, task: dict[str, Any]) -> tuple[int, Any]:
            """Run a single agent and return (index, AgentResult|Exception)."""
            result = await run_agent(
                prompt=task.get("prompt", ""),
                agent_type=task.get("agent_type", "general"),
                model=task.get("model", ""),
                deps=self._parent_deps,
                tools=child_tools,
            )
            return (index, result)

        # Launch all agents in parallel, catching per-task exceptions
        coros = [_run_one(i, t) for i, t in enumerate(tasks_input)]
        raw_results = await asyncio.gather(*coros, return_exceptions=True)

        # Format results
        lines: list[str] = []
        any_success = False
        any_failure = False

        for i, raw in enumerate(raw_results):
            task_spec = tasks_input[i]
            agent_type = task_spec.get("agent_type", "general")
            prompt_preview = task_spec.get("prompt", "")[:60]

            if isinstance(raw, Exception):
                any_failure = True
                lines.append(
                    f"--- Task {i + 1}/{len(tasks_input)} [{agent_type}] ---\n"
                    f"Prompt: {prompt_preview}\n"
                    f"Status: ERROR\n"
                    f"Error: {raw}\n"
                )
            else:
                # raw is (index, AgentResult)
                _idx, agent_result = raw
                if agent_result.is_error:
                    any_failure = True
                    lines.append(
                        f"--- Task {i + 1}/{len(tasks_input)} [{agent_type}] ---\n"
                        f"Prompt: {prompt_preview}\n"
                        f"Status: ERROR\n"
                        f"Error: {agent_result.error}\n"
                    )
                else:
                    any_success = True
                    text = agent_result.result_text or "(no output)"
                    lines.append(
                        f"--- Task {i + 1}/{len(tasks_input)} [{agent_type}] ---\n"
                        f"Prompt: {prompt_preview}\n"
                        f"Status: OK ({agent_result.turns_used} turns)\n"
                        f"Result:\n{text}\n"
                    )

        output = "\n".join(lines)

        # If all tasks failed, mark the overall result as error
        if any_failure and not any_success:
            return ToolResult(output=output, is_error=True)

        return ToolResult(output=output)

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
