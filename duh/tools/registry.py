"""Tool registry — instantiate all available core tools.

get_all_tools() returns a list of tool instances for the 6 core tools.
Tools that haven't been implemented yet are silently skipped so the
CLI can function with whatever tools are available.
"""

from __future__ import annotations

from typing import Any


def get_all_tools() -> list[Any]:
    """Return instances of all available core tools.

    Tools are imported individually so missing implementations
    don't prevent the rest from loading.
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

    return tools
