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

from duh.constitution import build_system_prompt, ConstitutionConfig, AGENT_OVERLAYS

# Agent types are defined in the constitution — single source of truth
AGENT_TYPES = list(AGENT_OVERLAYS.keys())

# Build agent prompts from the constitution (not hardcoded)
AGENT_PROMPTS: dict[str, str] = {
    agent_type: build_system_prompt(ConstitutionConfig(agent_type=agent_type))
    for agent_type in AGENT_TYPES
}

# ---------------------------------------------------------------------------
# Default model per agent type (used when caller doesn't specify)
# ---------------------------------------------------------------------------

AGENT_TYPE_DEFAULTS: dict[str, str] = {
    "general": "inherit",   # use parent's model
    "coder": "sonnet",      # balanced speed/quality
    "researcher": "haiku",  # fast and cheap for search
    "planner": "opus",      # complex reasoning
}


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
        "model": {
            "type": "string",
            "enum": ["haiku", "sonnet", "opus", "inherit"],
            "description": (
                "Model for the subagent. 'inherit' (or omitted) uses the "
                "agent type's default: general=inherit, coder=sonnet, "
                "researcher=haiku, planner=opus."
            ),
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


def _resolve_model(model: str, agent_type: str) -> str:
    """Resolve the effective model name for a subagent.

    Priority: explicit model > agent type default > inherit (empty string).
    'inherit' or '' means: use the parent's deps/model unchanged.
    """
    effective = model or AGENT_TYPE_DEFAULTS.get(agent_type, "inherit")
    if effective == "inherit":
        return ""
    return effective


async def run_agent(
    *,
    prompt: str,
    agent_type: str = "general",
    model: str = "",
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
        model: Model override ('haiku', 'sonnet', 'opus', 'inherit', or '').
            Empty string uses the agent type's default. 'inherit' uses
            the parent's model unchanged.
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
    resolved_model = _resolve_model(model, agent_type)

    config = EngineConfig(
        model=resolved_model,
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
