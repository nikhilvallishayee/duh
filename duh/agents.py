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

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
    # Default every agent type to ``inherit`` so sub-agents use the parent's
    # currently-active model (and provider). Historically these used the
    # Anthropic aliases ``haiku``/``sonnet``/``opus``, which 404 when the
    # parent is running on Gemini/Groq/Ollama (no such model names exist
    # there). Users who want the older per-type split can still pass it
    # explicitly via the ``model`` field in each Swarm task.
    "general":    "inherit",
    "coder":      "inherit",
    "researcher": "inherit",
    "planner":    "inherit",
    "reviewer":   "inherit",
    "subagent":   "inherit",
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
            "enum": ["small", "medium", "large", "inherit"],
            "description": (
                "Size tier for the sub-agent, resolved per-provider. "
                "'small' = fast/cheap (Haiku, Flash, Llama-8B, Qwen-1.5B); "
                "'medium' = default (Sonnet, Pro, Llama-70B, Qwen-7B); "
                "'large' = most capable (Opus, Pro, DeepSeek-V2); "
                "'inherit' (default) uses the parent's current model."
            ),
            "default": "inherit",
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


def _resolve_model(model: str, agent_type: str, parent_model: str = "") -> str:
    """Resolve the effective model name for a subagent.

    Priority:
      1. Explicit ``model`` (if a tier like ``"small"``, a literal like
         ``"claude-haiku-4-5"``, or the sentinel ``"inherit"``).
      2. The agent-type default from :data:`AGENT_TYPE_DEFAULTS` (currently
         all ``"inherit"``).
      3. ``parent_model`` when the effective selection is ``"inherit"`` or
         empty — the sub-agent tracks whatever the parent is running.

    Tier aliases (``small`` / ``medium`` / ``large``) are resolved against
    ``parent_model``'s provider via
    :func:`duh.providers.registry.resolve_agent_tier`.
    """
    from duh.providers.registry import resolve_agent_tier

    effective = model or AGENT_TYPE_DEFAULTS.get(agent_type, "inherit")
    if effective == "inherit" or effective == "":
        return parent_model
    # Delegate tier / literal-passthrough to the registry helper.
    return resolve_agent_tier(effective, parent_model)


async def run_agent(
    *,
    prompt: str,
    agent_type: str = "general",
    model: str = "",
    parent_model: str = "",
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
        model: Tier or literal model for the subagent. One of
            ``"small"`` / ``"medium"`` / ``"large"`` / ``"inherit"`` (tiers
            resolved per-provider against *parent_model*), or a literal
            model name (``"claude-haiku-4-5"``, ``"gemini-2.5-pro"``, …).
            Empty string + ``"inherit"`` both mean "use parent's model".
        parent_model: The model the parent Engine is currently running.
            Required to resolve tier aliases into concrete per-provider
            model names. When omitted, tiers fall through to an empty
            string (the Engine then resorts to provider defaults).
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
    resolved_model = _resolve_model(model, agent_type, parent_model)

    config = EngineConfig(
        model=resolved_model,
        system_prompt=agent_def.system_prompt,
        tools=tools or [],
        max_turns=min(max_turns, agent_def.max_turns),
        cwd=cwd,
    )
    engine = Engine(deps=deps, config=config)

    # Accumulate text from both streaming deltas AND final assistant events.
    # Some providers only emit `assistant` events (full reconciled message);
    # others stream incrementally via `text_delta`. We capture both and
    # reconcile at the end: assistant text is authoritative, deltas are
    # the fallback. See ADR-012 and the "(no output)" hallucination bug.
    delta_text = ""
    assistant_text = ""
    turns = 0
    error = ""
    saw_tool_use = False

    try:
        async for event in engine.run(prompt):
            event_type = event.get("type", "")
            if event_type == "text_delta":
                delta_text += event.get("text", "")
            elif event_type == "assistant":
                msg = event.get("message")
                if msg is None:
                    continue
                # Extract text from the reconciled assistant message.
                # Prefer Message.text property (handles TextBlock + dict forms);
                # fall back to manual content-list walk for duck-typed inputs.
                extracted = ""
                text_attr = getattr(msg, "text", None)
                if isinstance(text_attr, str):
                    extracted = text_attr
                else:
                    content = getattr(msg, "content", None)
                    if isinstance(content, str):
                        extracted = content
                    elif isinstance(content, list):
                        parts: list[str] = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                parts.append(block.get("text", "") or "")
                            else:
                                btext = getattr(block, "text", None)
                                if isinstance(btext, str):
                                    parts.append(btext)
                        extracted = "".join(parts)
                # Keep the latest NON-EMPTY assistant text; intermediate
                # tool_use-only assistant events have empty .text and must
                # not wipe a prior populated value.
                if extracted:
                    assistant_text = extracted
            elif event_type == "tool_use":
                saw_tool_use = True
            elif event_type == "done":
                turns = event.get("turns", 0)
            elif event_type == "error":
                error = event.get("error", "unknown error")
    except Exception as exc:
        error = str(exc)

    # Reconcile: assistant wins if present, deltas are fallback.
    result_text = assistant_text or delta_text

    # Observability: surface the empty-output case that caused the live bug.
    if not result_text and not error:
        logger.info(
            "Swarm sub-agent [%s] completed %d turns with empty result_text (model=%s)",
            agent_type,
            turns,
            resolved_model or "inherit",
        )

    # Defensive guard: if we got no text and no error, and no tool_use was
    # observed, something is misconfigured. Surface it as an error so the
    # parent model can't mistake silent emptiness for success.
    if not result_text and not error and not saw_tool_use:
        error = (
            "Sub-agent completed without producing output. This is likely "
            "a misconfiguration (empty response, 0 max_turns, or "
            "provider-level failure)."
        )

    return AgentResult(
        agent_type=agent_type,
        result_text=result_text,
        turns_used=turns,
        error=error,
    )
