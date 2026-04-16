"""Tool registry -- instantiate all available core tools.

get_all_tools() returns a list of tool instances for the 6 core tools
plus the Skill and ToolSearch meta-tools.

Tools that haven't been implemented yet are silently skipped so the
CLI can function with whatever tools are available.

Lazy mode (``lazy_mode=True``) returns ``LazyTool`` proxies that defer
instantiation of the underlying tool until first attribute access.
This shaves the import cost of unused tools off CLI startup.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from duh.kernel.schema_validator import SchemaValidationError, validate_tool_schema

logger = logging.getLogger(__name__)


class LazyTool:
    """A proxy that defers tool instantiation until first attribute access.

    The proxy exposes ``name`` (provided at construction) so a registry
    walker can identify it without forcing a load. Any other attribute
    access triggers the factory and from then on delegates to the real
    tool instance.

    Used by ``get_all_tools(lazy_mode=True)`` to keep CLI startup fast.
    """

    __slots__ = ("name", "_factory", "_instance")

    def __init__(self, name: str, factory: Callable[[], Any]) -> None:
        self.name = name
        self._factory = factory
        self._instance: Any | None = None

    def _resolve(self) -> Any:
        if self._instance is None:
            self._instance = self._factory()
        return self._instance

    def __getattr__(self, item: str) -> Any:
        # __slots__ attrs are handled by Python before __getattr__, so we
        # only get here for "real" tool attributes.
        return getattr(self._resolve(), item)

    def __repr__(self) -> str:
        loaded = "loaded" if self._instance is not None else "deferred"
        return f"<LazyTool name={self.name!r} {loaded}>"


def _validate_registered_tool(tool: Any) -> None:
    """Validate a tool's input_schema after registration.

    Logs warnings but never blocks registration -- tools with bad schemas
    still work, just suboptimally.
    """
    name = getattr(tool, "name", "<unknown>")
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        return
    try:
        warnings = validate_tool_schema(name, schema)
        for w in warnings:
            logger.warning("Schema warning: %s", w)
    except SchemaValidationError:
        logger.exception("Schema validation error for tool '%s'", name)


def get_all_tools(
    *,
    skills: list[Any] | None = None,
    deferred_tools: list[Any] | None = None,
    path_policy: Any | None = None,
    lazy_mode: bool = False,
) -> list[Any]:
    """Return instances of all available core tools.

    Tools are imported individually so missing implementations
    don't prevent the rest from loading.

    Args:
        skills: Optional list of SkillDef objects to register with SkillTool.
        deferred_tools: Optional list of DeferredTool objects for ToolSearchTool.
        path_policy: Optional PathPolicy for filesystem boundary enforcement
            (ADR-072).  Passed to Read, Write, Edit, and MultiEdit tools.
        lazy_mode: When True, return ``LazyTool`` proxies that defer
            instantiation (and the underlying module import) until first
            attribute access.  Saves CLI startup time when most tools
            are never used.
    """
    if lazy_mode:
        return _get_all_tools_lazy(
            skills=skills,
            deferred_tools=deferred_tools,
            path_policy=path_policy,
        )

    tools: list[Any] = []

    # Read
    try:
        from duh.tools.read import ReadTool
        tools.append(ReadTool(path_policy=path_policy))
    except ImportError:
        pass

    # Write
    try:
        from duh.tools.write import WriteTool
        tools.append(WriteTool(path_policy=path_policy))
    except ImportError:
        pass

    # Edit
    try:
        from duh.tools.edit import EditTool
        tools.append(EditTool(path_policy=path_policy))
    except ImportError:
        pass

    # MultiEdit
    try:
        from duh.tools.multi_edit import MultiEditTool
        tools.append(MultiEditTool(path_policy=path_policy))
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

    # ADR-068: validate tool schemas at registration time
    for tool in tools:
        _validate_registered_tool(tool)

    return tools


# ---------------------------------------------------------------------------
# Lazy-loading mode (PERF-9)
# ---------------------------------------------------------------------------

# (name, module_path, class_name, takes_path_policy)
# When ``takes_path_policy`` is True the constructor receives ``path_policy``.
_LAZY_TOOL_SPECS: list[tuple[str, str, str, bool]] = [
    ("Read", "duh.tools.read", "ReadTool", True),
    ("Write", "duh.tools.write", "WriteTool", True),
    ("Edit", "duh.tools.edit", "EditTool", True),
    ("MultiEdit", "duh.tools.multi_edit", "MultiEditTool", True),
    ("Bash", "duh.tools.bash", "BashTool", False),
    ("Glob", "duh.tools.glob_tool", "GlobTool", False),
    ("Grep", "duh.tools.grep", "GrepTool", False),
    ("WebFetch", "duh.tools.web_fetch", "WebFetchTool", False),
    ("WebSearch", "duh.tools.web_search", "WebSearchTool", False),
    ("Task", "duh.tools.task_tool", "TaskTool", False),
    ("EnterWorktree", "duh.tools.worktree", "EnterWorktreeTool", False),
    ("ExitWorktree", "duh.tools.worktree", "ExitWorktreeTool", False),
    ("NotebookEdit", "duh.tools.notebook_edit", "NotebookEditTool", False),
    ("TestImpact", "duh.tools.test_impact", "TestImpactTool", False),
    ("MemoryStore", "duh.tools.memory_tool", "MemoryStoreTool", False),
    ("MemoryRecall", "duh.tools.memory_tool", "MemoryRecallTool", False),
    ("HTTP", "duh.tools.http_tool", "HTTPTool", False),
    ("Docker", "duh.tools.docker_tool", "DockerTool", False),
    ("Database", "duh.tools.db_tool", "DatabaseTool", False),
    ("GitHub", "duh.tools.github_tool", "GitHubTool", False),
    ("TodoWrite", "duh.tools.todo_tool", "TodoWriteTool", False),
    ("AskUserQuestion", "duh.tools.ask_user_tool", "AskUserQuestionTool", False),
    ("AgentTool", "duh.tools.agent_tool", "AgentTool", False),
    ("SwarmTool", "duh.tools.swarm_tool", "SwarmTool", False),
]


def _make_factory(
    module_path: str,
    class_name: str,
    takes_path_policy: bool,
    path_policy: Any | None,
) -> Callable[[], Any]:
    """Build a zero-arg factory that imports + instantiates on call."""

    def factory() -> Any:
        from importlib import import_module
        mod = import_module(module_path)
        cls = getattr(mod, class_name)
        if takes_path_policy:
            return cls(path_policy=path_policy)
        return cls()

    return factory


def _get_all_tools_lazy(
    *,
    skills: list[Any] | None,
    deferred_tools: list[Any] | None,
    path_policy: Any | None,
) -> list[Any]:
    """Lazy variant of :func:`get_all_tools`.

    Returns a list of ``LazyTool`` proxies for the standard tools whose
    constructors don't need extra wiring beyond ``path_policy``.

    Skill, ToolSearch, and LSP still need the eager path because they
    take constructor arguments that we don't want to capture at module
    load (skills/deferred_tools are mutated by the runner).
    """
    tools: list[Any] = []

    for name, module_path, class_name, takes_pp in _LAZY_TOOL_SPECS:
        try:
            factory = _make_factory(module_path, class_name, takes_pp, path_policy)
            tools.append(LazyTool(name, factory))
        except Exception:
            # Defensive — factory creation itself shouldn't fail, but never
            # let a single tool break the registry.
            continue

    # Skill — needs the live skills list; instantiate eagerly.
    try:
        from duh.tools.skill_tool import SkillTool
        tools.append(SkillTool(skills=skills or []))
    except ImportError:
        pass

    # ToolSearch — needs the live deferred_tools list; instantiate eagerly.
    try:
        from duh.tools.tool_search import ToolSearchTool
        tools.append(ToolSearchTool(deferred_tools=deferred_tools or []))
    except ImportError:
        pass

    # LSP — registered as deferred via ToolSearch; no proxy here.
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
        # Find ToolSearch by name only; touching arbitrary attributes
        # (hasattr / add_tool) on a LazyTool would force it to resolve.
        for t in tools:
            if getattr(t, "name", "") != "ToolSearch":
                continue
            # Resolve the underlying instance only for the ToolSearch
            # match (which is itself eager, not a LazyTool).
            if hasattr(t, "add_tool"):
                t.add_tool(lsp_deferred)
            break
    except ImportError:
        pass

    # NB: we deliberately skip the schema-validation loop here — touching
    # ``input_schema`` would force every lazy tool to load, defeating the
    # point.  Validation runs in eager mode and in tests.
    return tools
