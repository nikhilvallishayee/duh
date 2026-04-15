"""Agent tool — lets the model spawn subagents."""
from __future__ import annotations
from typing import Any
from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability

class AgentTool:
    name = "Agent"
    capabilities = Capability.EXEC
    description = "Spawn a subagent to handle a task independently. Use for research, coding, or planning subtasks."
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The task for the subagent."},
            "agent_type": {"type": "string", "enum": ["general", "coder", "researcher", "planner", "reviewer", "subagent"], "description": "Agent specialization. Default: general."},
            "model": {"type": "string", "enum": ["haiku", "sonnet", "opus", "inherit"], "description": "Model for subagent. Defaults to agent type's preferred model."},
        },
        "required": ["prompt"],
    }
    is_read_only = False
    is_destructive = False

    def __init__(self, *, parent_deps: Any = None):
        self._parent_deps = parent_deps

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        from duh.agents import run_agent
        try:
            result = await run_agent(
                prompt=input.get("prompt", ""),
                agent_type=input.get("agent_type", "general"),
                model=input.get("model", ""),
                deps=self._parent_deps,
            )
            return ToolResult(output=result.result_text if hasattr(result, 'result_text') else str(result))
        except Exception as e:
            return ToolResult(output=f"Agent error: {e}", is_error=True)

    async def check_permissions(self, input: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {"allowed": True}
