"""Tool registry -- instantiate all available core tools.

get_all_tools() returns a list of tool instances for the 6 core tools
plus the Skill and ToolSearch meta-tools.

Tools that haven't been implemented yet are silently skipped so the
CLI can function with whatever tools are available.
"""

from __future__ import annotations

from typing import Any


def get_all_tools(
    *,
    skills: list[Any] | None = None,
    deferred_tools: list[Any] | None = None,
) -> list[Any]:
    """Return instances of all available core tools.

    Tools are imported individually so missing implementations
    don't prevent the rest from loading.

    Args:
        skills: Optional list of SkillDef objects to register with SkillTool.
        deferred_tools: Optional list of DeferredTool objects for ToolSearchTool.
    """
    tools: list[Any] = []

    # Read
    try:
        from duh.tools.read import ReadTool
        tools.append(ReadTool())
    except ImportError:
        pass

    # Write
    try:
        from duh.tools.write import WriteTool
        tools.append(WriteTool())
    except ImportError:
        pass

    # Edit
    try:
        from duh.tools.edit import EditTool
        tools.append(EditTool())
    except ImportError:
        pass

    # Bash
    try:
        from duh.tools.bash import BashTool
        tools.append(BashTool())
    except ImportError:
        pass

    # Glob
    try:
        from duh.tools.glob_tool import GlobTool
        tools.append(GlobTool())
    except ImportError:
        pass

    # Grep
    try:
        from duh.tools.grep import GrepTool
        tools.append(GrepTool())
    except ImportError:
        pass

    # Skill (ADR-017)
    try:
        from duh.tools.skill_tool import SkillTool
        tools.append(SkillTool(skills=skills or []))
    except ImportError:
        pass

    # ToolSearch (ADR-018)
    try:
        from duh.tools.tool_search import ToolSearchTool
        tools.append(ToolSearchTool(deferred_tools=deferred_tools or []))
    except ImportError:
        pass

    return tools
