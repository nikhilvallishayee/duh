"""Built-in tools for D.U.H.

Each tool implements the Tool protocol from duh.kernel.tool.
"""

from duh.tools.bash import BashTool
from duh.tools.edit import EditTool
from duh.tools.glob_tool import GlobTool
from duh.tools.grep import GrepTool
from duh.tools.read import ReadTool
from duh.tools.write import WriteTool

ALL_TOOLS = [ReadTool, WriteTool, EditTool, BashTool, GlobTool, GrepTool]

__all__ = [
    "ReadTool",
    "WriteTool",
    "EditTool",
    "BashTool",
    "GlobTool",
    "GrepTool",
    "ALL_TOOLS",
]
