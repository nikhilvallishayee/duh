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
from duh.tools.memory_tool import MemoryRecallTool, MemoryStoreTool
from duh.tools.notebook_edit import NotebookEditTool
from duh.tools.worktree import EnterWorktreeTool, ExitWorktreeTool
from duh.tools.write import WriteTool
from duh.tools.agent_tool import AgentTool

ALL_TOOLS = [ReadTool, WriteTool, EditTool, MultiEditTool, BashTool, GlobTool, GrepTool, SkillTool, ToolSearchTool, WebFetchTool, WebSearchTool, TaskTool, EnterWorktreeTool, ExitWorktreeTool, NotebookEditTool, MemoryStoreTool, MemoryRecallTool, AgentTool]

__all__ = [
    "ReadTool",
    "WriteTool",
    "EditTool",
    "MultiEditTool",
    "BashTool",
    "GlobTool",
    "GrepTool",
    "MemoryRecallTool",
    "MemoryStoreTool",
    "NotebookEditTool",
    "SkillTool",
    "TaskTool",
    "ToolSearchTool",
    "WebFetchTool",
    "WebSearchTool",
    "EnterWorktreeTool",
    "ExitWorktreeTool",
    "AgentTool",
    "ALL_TOOLS",
]
