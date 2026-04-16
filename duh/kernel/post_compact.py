"""Post-compact state rebuild (ADR-058).

After compaction, re-read the most recently accessed files so the model
retains file context.  Also restores awareness of the active plan and
any invoked skills so the model doesn't lose working context.

    from duh.kernel.post_compact import (
        rebuild_post_compact_context,
        restore_plan_context,
        restore_skill_context,
    )

    messages = await rebuild_post_compact_context(
        messages, file_tracker, max_files=5, max_tokens_per_file=5000,
    )
"""

from __future__ import annotations

import logging
import os
from typing import Any

from duh.kernel.messages import Message

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_FILES = 5
DEFAULT_MAX_TOKENS_PER_FILE = 5_000

# Rough chars-per-token multiplier (same heuristic used elsewhere in D.U.H.)
_CHARS_PER_TOKEN = 4


async def rebuild_post_compact_context(
    messages: list[Any],
    file_tracker: Any,
    max_files: int = DEFAULT_MAX_FILES,
    max_tokens_per_file: int = DEFAULT_MAX_TOKENS_PER_FILE,
) -> list[Any]:
    """Append file-content messages for recently-read files after compaction.

    Walks the file tracker's operation list in reverse to find the most
    recently accessed *unique* paths (up to ``max_files``).  For each
    file that still exists, reads its content (truncated to
    ``max_tokens_per_file`` tokens) and appends a system message
    carrying the content.

    Args:
        messages: The post-compaction message list (modified copy returned).
        file_tracker: A ``FileTracker`` instance (or any object with an
            ``ops`` attribute yielding objects with a ``.path`` attribute).
            May be ``None``, in which case the input is returned as-is.
        max_files: Maximum number of files to re-inject.
        max_tokens_per_file: Per-file token budget (chars / 4).

    Returns:
        A new list with zero or more appended system messages carrying
        file contents.
    """
    if file_tracker is None:
        return list(messages)

    ops = file_tracker.ops if hasattr(file_tracker, "ops") else []
    if not ops:
        return list(messages)

    # Collect unique recent paths (most-recent first)
    seen: set[str] = set()
    recent_paths: list[str] = []
    for op in reversed(ops):
        path = op.path if hasattr(op, "path") else str(op)
        if path not in seen:
            seen.add(path)
            recent_paths.append(path)
        if len(recent_paths) >= max_files:
            break

    if not recent_paths:
        return list(messages)

    result = list(messages)
    max_chars = max_tokens_per_file * _CHARS_PER_TOKEN
    injected = 0

    for path in recent_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", errors="replace") as f:
                content = f.read(max_chars)
            if not content.strip():
                continue
            if len(content) >= max_chars:
                content = content[:max_chars - 3] + "..."
            restore_msg = Message(
                role="system",
                content=(
                    f"[Post-compaction file restore: {path}]\n"
                    f"```\n{content}\n```"
                ),
                metadata={
                    "subtype": "post_compact_file_restore",
                    "path": path,
                },
            )
            result.append(restore_msg)
            injected += 1
        except (OSError, PermissionError):
            logger.debug("Could not read file for post-compact restore: %s", path)
            continue

    if injected:
        logger.info(
            "Post-compact file rebuild: injected %d file(s) into context",
            injected,
        )

    return result


def restore_plan_context(engine: Any) -> str | None:
    """If plan mode is active, return the plan content as a context string.

    Inspects the engine for a ``_plan_mode`` attribute (set by the REPL
    or TUI when plan mode is in use).  If a plan is currently proposed
    or executing, returns a formatted string describing the plan and its
    steps so the model retains awareness after compaction.

    Args:
        engine: An ``Engine`` instance (or any object that may carry
            ``_plan_mode``).

    Returns:
        A context string if a plan is active, or ``None``.
    """
    plan_mode = getattr(engine, "_plan_mode", None)
    if plan_mode is None:
        return None

    # Import locally to avoid circular imports
    from duh.kernel.plan_mode import PlanState

    state = getattr(plan_mode, "state", None)
    if state is None or state in (PlanState.EMPTY, PlanState.DONE):
        return None

    # Build a summary of the active plan
    description = getattr(plan_mode, "description", "") or ""
    steps = getattr(plan_mode, "steps", []) or []

    if not description and not steps:
        return None

    parts: list[str] = [f"Active plan: {description}"]
    if steps:
        parts.append(f"State: {state.name}")
        for step in steps:
            marker = "[x]" if step.done else "[ ]"
            parts.append(f"  {marker} {step.number}. {step.description}")

    return "\n".join(parts)


def restore_skill_context(engine: Any) -> str | None:
    """If skills were loaded, return their names and descriptions.

    Inspects the engine's tool list (``engine._config.tools``) for a
    ``SkillTool`` instance and returns a summary of all registered skills
    so the model retains awareness of available skills after compaction.

    Args:
        engine: An ``Engine`` instance (or any object with
            ``_config.tools``).

    Returns:
        A context string listing loaded skills, or ``None``.
    """
    config = getattr(engine, "_config", None)
    if config is None:
        return None

    tools = getattr(config, "tools", None)
    if not tools:
        return None

    # Find the SkillTool in the tools list
    skill_tool = None
    for tool in tools:
        # Check by class name to avoid importing SkillTool (circular risk)
        if type(tool).__name__ == "SkillTool":
            skill_tool = tool
            break

    if skill_tool is None:
        return None

    skills = getattr(skill_tool, "skills", [])
    if not skills:
        return None

    lines: list[str] = ["Loaded skills (invoke via the Skill tool):"]
    for skill in skills:
        hint = f" ({skill.argument_hint})" if skill.argument_hint else ""
        lines.append(f"- {skill.name}: {skill.description}{hint}")

    return "\n".join(lines)
