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

    # MultiEdit
    try:
        from duh.tools.multi_edit import MultiEditTool
        tools.append(MultiEditTool())
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

    # WebFetch
    try:
        from duh.tools.web_fetch import WebFetchTool
        tools.append(WebFetchTool())
    except ImportError:
        pass

    # WebSearch
    try:
        from duh.tools.web_search import WebSearchTool
        tools.append(WebSearchTool())
    except ImportError:
        pass

    # Task (in-session task/todo tracking)
    try:
        from duh.tools.task_tool import TaskTool
        tools.append(TaskTool())
    except ImportError:
        pass

    # EnterWorktree
    try:
        from duh.tools.worktree import EnterWorktreeTool
        tools.append(EnterWorktreeTool())
    except ImportError:
        pass

    # ExitWorktree
    try:
        from duh.tools.worktree import ExitWorktreeTool
        tools.append(ExitWorktreeTool())
    except ImportError:
        pass

    # NotebookEdit (Jupyter .ipynb cell editing)
    try:
        from duh.tools.notebook_edit import NotebookEditTool
        tools.append(NotebookEditTool())
    except ImportError:
        pass

    # TestImpact (test impact analysis)
    try:
        from duh.tools.test_impact import TestImpactTool
        tools.append(TestImpactTool())
    except ImportError:
        pass

    # MemoryStore (persistent cross-session facts)
    try:
        from duh.tools.memory_tool import MemoryStoreTool
        tools.append(MemoryStoreTool())
    except ImportError:
        pass

    # MemoryRecall (search saved facts)
    try:
        from duh.tools.memory_tool import MemoryRecallTool
        tools.append(MemoryRecallTool())
    except ImportError:
        pass

    # HTTP (API testing)
    try:
        from duh.tools.http_tool import HTTPTool
        tools.append(HTTPTool())
    except ImportError:
        pass

    # Docker (container management)
    try:
        from duh.tools.docker_tool import DockerTool
        tools.append(DockerTool())
    except ImportError:
        pass

    # Database (read-only SQL queries against SQLite)
    try:
        from duh.tools.db_tool import DatabaseTool
        tools.append(DatabaseTool())
    except ImportError:
        pass

    # GitHub (PR workflow via gh CLI)
    try:
        from duh.tools.github_tool import GitHubTool
        tools.append(GitHubTool())
    except ImportError:
        pass

    # TodoWrite (structured checklist)
    try:
        from duh.tools.todo_tool import TodoWriteTool
        tools.append(TodoWriteTool())
    except ImportError:
        pass

    # AskUserQuestion (interactive user prompting)
    try:
        from duh.tools.ask_user_tool import AskUserQuestionTool
        tools.append(AskUserQuestionTool())
    except ImportError:
        pass

    # AgentTool (multi-agent — deps and tools wired by runner after construction)
    try:
        from duh.tools.agent_tool import AgentTool
        tools.append(AgentTool())
    except ImportError:
        pass

    # SwarmTool (parallel multi-agent — deps and tools wired by runner after construction)
    try:
        from duh.tools.swarm_tool import SwarmTool
        tools.append(SwarmTool())
    except ImportError:
        pass

    # LSP (deferred — registered via ToolSearch, not loaded eagerly)
    try:
        from duh.tools.lsp_tool import LSPTool
        from duh.tools.tool_search import DeferredTool

        lsp = LSPTool()
        lsp_deferred = DeferredTool(
            name=lsp.name,
            description=lsp.description,
            input_schema=lsp.input_schema,
            source="builtin",
        )
        # Attach to ToolSearchTool if present
        for t in tools:
            if hasattr(t, "add_tool") and getattr(t, "name", "") == "ToolSearch":
                t.add_tool(lsp_deferred)
                break
    except ImportError:
        pass

    return tools
