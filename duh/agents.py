"""Multi-agent support -- spawn subagents as child Engines.

See ADR-012 for the full rationale.

Each agent is a new Engine with its own conversation, system prompt,
and (optionally) working directory. There is no special agent framework.
An agent is just another run of the same agentic loop.

Agent types are system prompt variations:
    general  -- general-purpose coding assistant
    coder    -- focus on writing clean, tested code
    researcher -- focus on reading, searching, understanding
    planner  -- focus on breaking down tasks, creating plans
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Agent type definitions (system prompt variations)
# ---------------------------------------------------------------------------

AGENT_PROMPTS: dict[str, str] = {
    "general": (
        "You are a general-purpose AI coding assistant. "
        "You have access to tools for reading, writing, editing files, "
        "running bash commands, globbing, and grepping. Use them to "
        "complete the task you've been given. Be thorough and concise."
    ),
    "coder": (
        "You are a coding agent. Your primary job is to write clean, "
        "correct, well-tested code. Read existing code to understand "
        "patterns and conventions before writing. Write tests alongside "
        "implementation. Prefer small, focused changes."
    ),
    "researcher": (
        "You are a research agent. Your primary job is to read, search, "
        "and understand code. Use Glob, Grep, and Read extensively to "
        "build a thorough understanding before answering. Summarize "
        "findings clearly with file paths and line numbers."
    ),
    "planner": (
        "You are a planning agent. Your primary job is to break down "
        "complex tasks into clear, actionable steps. Analyze the codebase "
        "to understand what exists, then create a concrete plan with "
        "specific files to create or modify. Do not implement -- just plan."
    ),
}

AGENT_TYPES = list(AGENT_PROMPTS.keys())


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

@dataclass
class AgentDef:
    """Definition for a subagent type."""

    agent_type: str
    system_prompt: str
    tools: list[str] | None = None  # None = inherit parent tools
    max_turns: int = 50
    description: str = ""

    @classmethod
    def from_type(cls, agent_type: str) -> AgentDef:
        """Create an AgentDef from a built-in type name."""
        prompt = AGENT_PROMPTS.get(agent_type)
        if prompt is None:
            raise ValueError(
                f"Unknown agent type: {agent_type!r}. "
                f"Available: {', '.join(AGENT_TYPES)}"
            )
        return cls(
            agent_type=agent_type,
            system_prompt=prompt,
            description=f"Built-in {agent_type} agent",
        )


# ---------------------------------------------------------------------------
# Agent tool (the tool the model calls to spawn subagents)
# ---------------------------------------------------------------------------

AGENT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "The task for the agent to perform.",
        },
        "agent_type": {
            "type": "string",
            "enum": AGENT_TYPES,
            "description": "Agent specialization (default: general).",
            "default": "general",
        },
    },
    "required": ["prompt"],
}


@dataclass
class AgentResult:
    """Result from a subagent run."""

    agent_type: str
    result_text: str
    turns_used: int = 0
    error: str = ""

    @property
    def is_error(self) -> bool:
        return bool(self.error)


async def run_agent(
    *,
    prompt: str,
    agent_type: str = "general",
    deps: Any = None,
    tools: list[Any] | None = None,
    cwd: str = ".",
    max_turns: int = 50,
) -> AgentResult:
    """Spawn and run a subagent to completion.

    Creates a new Engine with the agent's system prompt, runs the prompt
    to completion, and returns the final assistant text.

    Args:
        prompt: The task for the agent.
        agent_type: One of the built-in agent types.
        deps: Deps instance (call_model, run_tool, approve, etc.).
        tools: Tool instances. None = use whatever deps provides.
        cwd: Working directory for the agent.
        max_turns: Maximum agentic turns.

    Returns:
        AgentResult with the final text or error.
    """
    from duh.kernel.engine import Engine, EngineConfig
    from duh.kernel.messages import Message

    agent_def = AgentDef.from_type(agent_type)

    config = EngineConfig(
        system_prompt=agent_def.system_prompt,
        tools=tools or [],
        max_turns=min(max_turns, agent_def.max_turns),
        cwd=cwd,
    )
    engine = Engine(deps=deps, config=config)

    result_text = ""
    turns = 0
    error = ""

    try:
        async for event in engine.run(prompt):
            event_type = event.get("type", "")
            if event_type == "text_delta":
                result_text += event.get("text", "")
            elif event_type == "done":
                turns = event.get("turns", 0)
            elif event_type == "error":
                error = event.get("error", "unknown error")
    except Exception as exc:
        error = str(exc)

    return AgentResult(
        agent_type=agent_type,
        result_text=result_text,
        turns_used=turns,
        error=error,
    )
