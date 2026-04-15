"""Agent tool — lets the model spawn subagents.

The child agent gets the parent's deps (call_model, run_tool, approve)
and the parent's tool list (minus AgentTool itself, to prevent infinite
recursion). This means a child agent can Read, Bash, Grep, etc. — it
just can't spawn further children.
"""
from __future__ import annotations
from typing import Any
from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability

# Maximum nesting depth. 1 = parent can spawn children, children cannot
# spawn grandchildren. Prevents runaway recursive agent spawning.
MAX_AGENT_DEPTH = 1


class AgentTool:
    name = "Agent"
    capabilities = Capability.EXEC
    description = (
        "Spawn a subagent to handle a task independently. The subagent "
        "gets its own conversation and can use all tools (Read, Bash, "
        "Grep, etc.) but cannot spawn further agents. Use for research, "
        "coding, or planning subtasks."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task for the subagent.",
            },
            "agent_type": {
                "type": "string",
                "enum": ["general", "coder", "researcher", "planner", "reviewer", "subagent"],
                "description": "Agent specialization. Default: general.",
            },
            "model": {
                "type": "string",
                "enum": ["haiku", "sonnet", "opus", "inherit"],
                "description": "Model for subagent. Defaults to agent type's preferred model.",
            },
        },
        "required": ["prompt"],
    }
    is_read_only = False
    is_destructive = False

    def __init__(self, *, parent_deps: Any = None, parent_tools: list[Any] | None = None):
        self._parent_deps = parent_deps
        self._parent_tools = parent_tools

    def _child_tools(self) -> list[Any]:
        """Return parent tools minus AgentTool (prevent recursion)."""
        if not self._parent_tools:
            return []
        return [t for t in self._parent_tools if getattr(t, "name", "") != "Agent"]

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        if self._parent_deps is None:
            return ToolResult(output="Agent error: no parent deps configured", is_error=True)

        from duh.agents import run_agent
        try:
            result = await run_agent(
                prompt=input.get("prompt", ""),
                agent_type=input.get("agent_type", "general"),
                model=input.get("model", ""),
                deps=self._parent_deps,
                tools=self._child_tools(),
            )
            if result.is_error:
                return ToolResult(output=f"Agent error: {result.error}", is_error=True)
            return ToolResult(output=result.result_text or "(agent produced no output)")
        except Exception as e:
            return ToolResult(output=f"Agent error: {e}", is_error=True)

    async def check_permissions(self, input: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {"allowed": True}
