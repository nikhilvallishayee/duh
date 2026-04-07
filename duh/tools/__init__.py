"""Built-in tools for D.U.H.

Each tool implements the Tool protocol from duh.kernel.tool.
"""

from duh.tools.bash import BashTool
from duh.tools.edit import EditTool
from duh.tools.glob_tool import GlobTool
from duh.tools.grep import GrepTool
from duh.tools.multi_edit import MultiEditTool
from duh.tools.read import ReadTool
from duh.tools.skill_tool import SkillTool
from duh.tools.task_tool import TaskTool
from duh.tools.tool_search import ToolSearchTool
from duh.tools.web_fetch import WebFetchTool
from duh.tools.web_search import WebSearchTool
from duh.tools.write import WriteTool

ALL_TOOLS = [ReadTool, WriteTool, EditTool, MultiEditTool, BashTool, GlobTool, GrepTool, SkillTool, ToolSearchTool, WebFetchTool, WebSearchTool, TaskTool]

__all__ = [
    "ReadTool",
    "WriteTool",
    "EditTool",
    "MultiEditTool",
    "BashTool",
    "GlobTool",
    "GrepTool",
    "SkillTool",
    "TaskTool",
    "ToolSearchTool",
    "WebFetchTool",
    "WebSearchTool",
    "ALL_TOOLS",
]
