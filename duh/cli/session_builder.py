"""SessionBuilder — shared setup for REPL and print-mode runners.

Both ``duh.cli.runner.run_print_mode`` and ``duh.cli.repl.run_repl`` need the
same ~150 lines of dependency-injection scaffolding:

  1. Provider resolution (flags → env → ollama fallback)
  2. PathPolicy from git root
  3. Tool loading (+ plugin-discovered deferred tools, + skills)
  4. Config loading (+ MCP servers + hooks)
  5. MCP connect-all and MCPToolWrapper registration
  6. System-prompt assembly (base + coordinator + brief + git + extras)
  7. Compactor + FileStore + NativeExecutor + approver
  8. Deps construction + AgentTool/SwarmTool parent patching
  9. EngineConfig + Engine + structured logger
 10. Session resume (``--continue``, ``--resume``, ``--session-id``, ``--summarize``)

This module extracts that shared sequence.  Runner- and REPL-specific bits
(permission-mode flags, prewarm, renderer, slash commands, plan mode, etc.)
stay in their respective call sites — the builder exposes the knobs they need
via :class:`SessionBuilderOptions`.

The builder is deliberately I/O-bearing: MCP servers are contacted, the git
root is resolved, files are read.  Callers own the lifetime of the returned
:class:`SessionBuild` — in particular they must call
``build.teardown_mcp()`` when they are done with the session.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from duh.adapters.approvers import (
    ApprovalMode,
    AutoApprover,
    InteractiveApprover,
    TieredApprover,
)
from duh.adapters.native_executor import NativeExecutor
from duh.adapters.simple_compactor import SimpleCompactor
from duh.hooks import HookRegistry
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.kernel.permission_cache import SessionPermissionCache
from duh.providers.registry import build_model_backend, resolve_provider_name
from duh.tools.registry import get_all_tools

logger = logging.getLogger("duh")


# ---------------------------------------------------------------------------
# Options + result + patch-target containers
# ---------------------------------------------------------------------------


@dataclass
class SessionBuilderOptions:
    """Knobs the caller uses to tailor what SessionBuilder does.

    The two call sites (REPL and print-mode) need slightly different
    assembly.  These flags expose the differences without duplicating the
    shared sequence.
    """

    # System-prompt extras
    include_skills_in_tools: bool = True
    include_deferred_tools: bool = True
    include_memory_prompt: bool = True
    include_env_block: bool = True
    include_templates_hint: bool = True
    include_model_context_block: bool = False  # REPL-only

    # Tool filtering (print-mode honours --allowedTools / --disallowedTools)
    honour_tool_filters: bool = True

    # Approver selection:
    #   "auto"       -> AutoApprover unconditionally (dry runs)
    #   "print_mode" -> skip_perms OR permission_mode in {bypassPermissions, dontAsk}
    #                   → AutoApprover, else InteractiveApprover
    #   "repl"       -> approval_mode flag → TieredApprover,
    #                   else skip_perms → AutoApprover,
    #                   else InteractiveApprover
    approver_mode: str = "print_mode"

    # Deps wiring
    wire_hook_registry_in_deps: bool = False  # REPL wires hooks into Deps
    wire_audit_logger_in_deps: bool = True    # runner wires audit logger

    # Engine config extras
    honour_tool_choice: bool = True
    honour_thinking: bool = True

    # Session ID / resume
    allow_session_id_override: bool = True

    # Log a warning when the approver is AutoApprover because of
    # --dangerously-skip-permissions or an automation permission_mode.
    log_skip_perms_warning: bool = True

    # Default system-prompt / brief strings — callers inject the runner
    # module-level constants so we don't re-resolve the constitution.
    default_system_prompt: str = ""
    brief_instruction: str = ""


@dataclass
class _BuilderPatchTargets:
    """Class references the builder uses to construct infrastructure.

    Callers (REPL and print-mode runner) override these so that legacy unit
    tests which patch module-level attributes such as
    ``duh.cli.runner.Engine`` continue to intercept construction.  When a
    field is None the builder uses its own import.
    """

    engine_cls: Any = None
    engine_config_cls: Any = None
    deps_cls: Any = None
    native_executor_cls: Any = None
    get_all_tools_fn: Any = None
    build_model_backend_fn: Any = None
    resolve_provider_name_fn: Any = None


@dataclass
class SessionBuild:
    """Everything the caller needs to start running turns.

    Produced by :meth:`SessionBuilder.build`.  The caller owns the lifetime
    of the MCP executor and structured logger; helpers are provided.
    """

    engine: Engine
    deps: Deps
    tools: list[Any]
    executor: Any
    provider_name: str
    model: str
    call_model: Any
    approver: Any
    compactor: SimpleCompactor
    store: Any
    mcp_executor: Any = None
    hook_registry: HookRegistry = field(default_factory=HookRegistry)
    structured_logger: Any = None
    task_manager: Any = None
    loaded_templates: list[Any] = field(default_factory=list)
    loaded_skills: list[Any] = field(default_factory=list)
    app_config: Any = None

    async def teardown_mcp(self) -> None:
        """Disconnect MCP servers — callers should invoke in their finally block."""
        if self.mcp_executor is not None:
            try:
                await self.mcp_executor.disconnect_all()
            except Exception:
                logger.debug("MCP disconnect failed", exc_info=True)

    def close_structured_logger(self) -> None:
        """Flush and close the structured JSON logger (if wired)."""
        if self.structured_logger is None:
            return
        try:
            self.structured_logger.session_end(
                turns=self.engine.turn_count,
                input_tokens=self.engine.total_input_tokens,
                output_tokens=self.engine.total_output_tokens,
            )
            self.structured_logger.close()
        except Exception:
            logger.debug("structured logger teardown failed", exc_info=True)


class ProviderResolutionError(Exception):
    """Raised by :meth:`SessionBuilder.build` when no provider is available."""

    def __init__(self, message: str, *, provider_name: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider_name = provider_name


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class SessionBuilder:
    """Assembles the shared Engine + Deps + tools + session-store pipeline.

    Usage::

        opts = SessionBuilderOptions(default_system_prompt=SYSTEM_PROMPT, ...)
        builder = SessionBuilder(args, opts, cwd=os.getcwd())
        build = await builder.build()
        # use build.engine, build.deps, ...
        await build.teardown_mcp()
    """

    def __init__(
        self,
        args: argparse.Namespace,
        options: SessionBuilderOptions,
        *,
        cwd: str | None = None,
        debug: bool | None = None,
        patch_targets: _BuilderPatchTargets | None = None,
    ) -> None:
        self.args = args
        self.options = options
        self.cwd = cwd if cwd is not None else os.getcwd()
        self.debug = debug if debug is not None else getattr(args, "debug", False)
        self._patch = patch_targets or _BuilderPatchTargets()

    # -- Patch-target accessors (legacy unit-test compat) ---------------

    def _engine_cls(self) -> Any:
        return self._patch.engine_cls or Engine

    def _engine_config_cls(self) -> Any:
        return self._patch.engine_config_cls or EngineConfig

    def _deps_cls(self) -> Any:
        return self._patch.deps_cls or Deps

    def _native_executor_cls(self) -> Any:
        return self._patch.native_executor_cls or NativeExecutor

    def _get_all_tools(self, **kwargs: Any) -> Any:
        fn = self._patch.get_all_tools_fn or get_all_tools
        return fn(**kwargs)

    # -- Provider --------------------------------------------------------

    def _check_ollama(self) -> bool:
        try:
            import httpx
            r = httpx.get("http://localhost:11434/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def _build_provider(
        self,
        provider_factories: dict[str, Callable[[str], Any]] | None = None,
    ) -> tuple[str | None, str | None, Any, str | None]:
        """Resolve provider and build backend.

        Returns ``(provider_name, model, call_model, error)``.  On failure
        ``error`` is populated and the other fields may be None.
        """
        resolve_fn = self._patch.resolve_provider_name_fn or resolve_provider_name
        provider_name = resolve_fn(
            explicit_provider=getattr(self.args, "provider", None),
            model=getattr(self.args, "model", None),
            check_ollama=self._check_ollama,
        )
        if not provider_name:
            return None, None, None, (
                "No provider available.\n"
                "  Option 1: export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  Option 2: export OPENAI_API_KEY=sk-...\n"
                "  Option 3: start Ollama (ollama serve)\n"
                "  Option 4: duh --provider ollama --model qwen2.5-coder:1.5b\n"
            )

        build_fn = self._patch.build_model_backend_fn or build_model_backend
        # Call build_fn with positional args + optional provider_factories.
        # The REPL's legacy shim uses a 2-arg signature, so pass kwargs
        # only when provider_factories is given.
        if provider_factories is None:
            backend = build_fn(provider_name, getattr(self.args, "model", None))
        else:
            backend = build_fn(
                provider_name,
                getattr(self.args, "model", None),
                provider_factories=provider_factories,
            )
        if not backend.ok:
            return provider_name, None, None, backend.error
        return provider_name, backend.model, backend.call_model, None

    # -- PathPolicy + skills + plugins + tools ---------------------------

    def _build_path_policy(self) -> Any:
        from duh.config import _find_git_root
        from duh.security.path_policy import PathPolicy

        project_root = _find_git_root(self.cwd) or self.cwd
        return PathPolicy(str(project_root))

    def _load_skills(self) -> list[Any]:
        if not self.options.include_skills_in_tools:
            return []
        from duh.kernel.skill import load_all_skills
        return load_all_skills(self.cwd)

    def _discover_deferred_tools(self) -> list[Any]:
        if not self.options.include_deferred_tools:
            return []
        from duh.plugins import discover_plugins, PluginRegistry
        from duh.tools.tool_search import DeferredTool

        specs = discover_plugins()
        registry = PluginRegistry()
        for spec in specs:
            registry.load(spec)

        deferred: list[Any] = []
        for pt in registry.plugin_tools:
            if hasattr(pt, "input_schema") and hasattr(pt, "name"):
                deferred.append(
                    DeferredTool(
                        name=pt.name,
                        description=getattr(pt, "description", ""),
                        input_schema=getattr(pt, "input_schema", {}),
                        source="plugin",
                    )
                )
        return deferred

    def _build_tools(
        self,
        path_policy: Any,
        skills: list[Any],
        deferred_tools: list[Any],
    ) -> list[Any]:
        tools = list(
            self._get_all_tools(
                skills=skills or None,
                deferred_tools=deferred_tools or None,
                path_policy=path_policy,
            )
        )
        if self.options.honour_tool_filters:
            tools = self._apply_tool_filters(tools)
        return tools

    def _apply_tool_filters(self, tools: list[Any]) -> list[Any]:
        allowed = getattr(self.args, "allowedTools", None)
        disallowed = getattr(self.args, "disallowedTools", None)
        if allowed:
            allow = {t.strip() for t in allowed.split(",")}
            tools = [t for t in tools if getattr(t, "name", "") in allow]
        if disallowed:
            deny = {t.strip() for t in disallowed.split(",")}
            tools = [t for t in tools if getattr(t, "name", "") not in deny]
        return tools

    # -- Config + MCP ----------------------------------------------------

    def _load_config(self) -> tuple[Any, HookRegistry, Any]:
        """Load config, build hook registry, build MCP executor (not connected).

        Returns ``(app_config, hook_registry, mcp_executor)``.  Any failure
        is swallowed in debug mode (runner and REPL both do this).
        """
        from duh.config import load_config

        app_config: Any = None
        hook_registry = HookRegistry()
        mcp_executor: Any = None

        try:
            app_config = load_config(cwd=self.cwd)

            cli_mcp = getattr(self.args, "mcp_config", None)
            if cli_mcp:
                import json as _json
                try:
                    if cli_mcp.strip().startswith("{"):
                        mcp_data = _json.loads(cli_mcp)
                    else:
                        mcp_data = _json.loads(open(cli_mcp).read())
                    app_config.mcp_servers = mcp_data
                except Exception:
                    logger.debug("Failed to parse --mcp-config", exc_info=True)

            if app_config.mcp_servers:
                from duh.adapters.mcp_executor import MCPExecutor
                mcp_executor = MCPExecutor.from_config(app_config.mcp_servers)
            if app_config.hooks:
                hook_registry = HookRegistry.from_config(app_config.hooks)
        except Exception:
            logger.debug("Config loading failed, using defaults", exc_info=True)

        return app_config, hook_registry, mcp_executor

    async def _connect_mcp(self, mcp_executor: Any, tools: list[Any]) -> None:
        """Connect to MCP servers and append wrapped tools to the tool list."""
        if mcp_executor is None:
            return
        try:
            discovered = await mcp_executor.connect_all()
            from duh.tools.mcp_tool import MCPToolWrapper
            for _server_name, mcp_tools in discovered.items():
                for info in mcp_tools:
                    wrapper = MCPToolWrapper(info=info, executor=mcp_executor)
                    tools.append(wrapper)
                    if self.debug:
                        logger.debug("MCP tool registered: %s", wrapper.name)
            total = sum(len(t) for t in discovered.values())
            if total:
                logger.info(
                    "Loaded %d MCP tools from %d servers", total, len(discovered)
                )
        except Exception:
            logger.debug("MCP connection failed, continuing without MCP", exc_info=True)

    # -- System prompt ---------------------------------------------------

    def _build_system_prompt(
        self,
        loaded_skills: list[Any],
        deferred_tools: list[Any],
    ) -> tuple[str, list[Any]]:
        """Assemble the system prompt. Returns ``(prompt, loaded_templates)``."""
        from duh.config import load_instructions
        from duh.kernel.git_context import get_git_context, get_git_warnings

        opts = self.options
        args = self.args

        base_prompt = getattr(args, "system_prompt", None) or opts.default_system_prompt
        if not getattr(args, "system_prompt", None) and getattr(
            args, "system_prompt_file", None
        ):
            try:
                base_prompt = open(args.system_prompt_file, encoding="utf-8").read()
            except Exception as e:
                sys.stderr.write(f"Warning: Could not read system prompt file: {e}\n")

        parts: list[str] = [base_prompt]

        if getattr(args, "coordinator", False):
            from duh.kernel.coordinator import COORDINATOR_SYSTEM_PROMPT
            parts.insert(0, COORDINATOR_SYSTEM_PROMPT)

        if getattr(args, "brief", False) and opts.brief_instruction:
            parts.append(opts.brief_instruction)

        # Project / user instructions (runner-style)
        if opts.include_memory_prompt or opts.include_env_block:
            instruction_list = load_instructions(self.cwd)
            if instruction_list:
                if isinstance(instruction_list, list):
                    parts.extend(instruction_list)
                else:
                    parts.append(instruction_list)

        # Memory prompt (runner-style)
        if opts.include_memory_prompt:
            try:
                from duh.adapters.memory_store import FileMemoryStore
                from duh.kernel.memory import build_memory_prompt
                mem_prompt = build_memory_prompt(FileMemoryStore(cwd=self.cwd))
                if mem_prompt:
                    parts.append(mem_prompt)
            except Exception:
                logger.debug("memory prompt build failed", exc_info=True)

        # Environment block (runner-style)
        if opts.include_env_block:
            import platform as _platform
            shell = os.environ.get("SHELL", "unknown").rsplit("/", 1)[-1]
            parts.append(
                "<environment>\n"
                f"cwd: {self.cwd}\n"
                f"platform: {_platform.system().lower()}\n"
                f"shell: {shell}\n"
                f"python: {_platform.python_version()}\n"
                "</environment>"
            )

        # Git context + warnings
        git_ctx = get_git_context(self.cwd)
        if git_ctx:
            parts.append(git_ctx)
        for warning in get_git_warnings(self.cwd):
            sys.stderr.write(f"\033[33mWARNING: {warning}\033[0m\n")

        # Skills hint
        if loaded_skills:
            skill_lines = ["\nAvailable skills (invoke via the Skill tool):"]
            for s in loaded_skills:
                hint = f" ({s.argument_hint})" if s.argument_hint else ""
                skill_lines.append(f"- {s.name}: {s.description}{hint}")
            parts.append("\n".join(skill_lines))

        # Templates hint (runner-style)
        loaded_templates: list[Any] = []
        if opts.include_templates_hint:
            try:
                from duh.kernel.templates import load_all_templates
                loaded_templates = load_all_templates(self.cwd)
            except Exception:
                logger.debug("template loading failed", exc_info=True)
            if loaded_templates:
                tmpl_lines = ["\nAvailable prompt templates (invoke via /template):"]
                for t in loaded_templates:
                    tmpl_lines.append(f"- {t.name}: {t.description}")
                parts.append("\n".join(tmpl_lines))

        # Deferred tools
        if deferred_tools and opts.include_deferred_tools:
            dt_lines = [
                "\n<deferred-tools>",
                "The following tools are available but their schemas are not yet loaded.",
                "Use the ToolSearch tool to load a tool's full schema before calling it.",
                "",
            ]
            for dt in deferred_tools:
                dt_lines.append(f"- {dt.name}: {dt.description}")
            dt_lines.append("</deferred-tools>")
            parts.append("\n".join(dt_lines))

        # REPL-style: model context block
        if opts.include_model_context_block:
            from duh.kernel.model_caps import model_context_block
            parts.append(model_context_block(getattr(self.args, "model", "") or ""))

        return "\n\n".join(parts), loaded_templates

    # -- Approver --------------------------------------------------------

    def _build_approver(self) -> Any:
        mode = self.options.approver_mode
        args = self.args
        cache = SessionPermissionCache()

        if mode == "auto":
            return AutoApprover()

        skip_perms = getattr(args, "dangerously_skip_permissions", False)

        if mode == "print_mode":
            perm_mode = getattr(args, "permission_mode", None)
            effective = skip_perms or perm_mode in ("bypassPermissions", "dontAsk")
            if effective:
                if self.options.log_skip_perms_warning:
                    logger.warning(
                        "Permission prompts disabled (--dangerously-skip-permissions "
                        "or automation permission_mode). All tool invocations will "
                        "be auto-approved for this session."
                    )
                return AutoApprover()
            return InteractiveApprover(permission_cache=cache)

        # REPL mode
        approval_mode_str = getattr(args, "approval_mode", None)
        if approval_mode_str:
            return TieredApprover(mode=ApprovalMode(approval_mode_str), cwd=self.cwd)
        if skip_perms:
            if self.options.log_skip_perms_warning:
                logger.warning(
                    "REPL started with --dangerously-skip-permissions: tool "
                    "invocations will be auto-approved without interactive prompts."
                )
            return AutoApprover()
        return InteractiveApprover(permission_cache=cache)

    # -- Deps + engine ---------------------------------------------------

    def _resolve_max_cost(self) -> float | None:
        mc = getattr(self.args, "max_cost", None)
        if mc is not None:
            return mc
        env = os.environ.get("DUH_MAX_COST")
        if env is not None:
            try:
                return float(env)
            except (ValueError, TypeError):
                return None
        return None

    def _build_engine_config(
        self,
        *,
        model: str,
        system_prompt: str,
        tools: list[Any],
        app_config: Any,
    ) -> Any:
        args = self.args
        opts = self.options

        thinking = None
        if opts.honour_thinking:
            mtt = getattr(args, "max_thinking_tokens", None)
            if mtt is not None:
                thinking = (
                    {"type": "enabled", "budget_tokens": mtt}
                    if mtt > 0
                    else {"type": "disabled"}
                )

        trifecta_ack = getattr(args, "i_understand_the_lethal_trifecta", False)
        if not trifecta_ack and app_config is not None:
            try:
                trifecta_ack = app_config.trifecta_acknowledged
            except AttributeError:
                pass

        kwargs: dict[str, Any] = dict(
            model=model,
            fallback_model=getattr(args, "fallback_model", None),
            system_prompt=system_prompt,
            tools=tools,
            max_turns=getattr(args, "max_turns", 100),
            max_cost=self._resolve_max_cost(),
            trifecta_acknowledged=trifecta_ack,
        )
        if opts.honour_tool_choice:
            kwargs["tool_choice"] = getattr(args, "tool_choice", None)
        if thinking is not None:
            kwargs["thinking"] = thinking
        return self._engine_config_cls()(**kwargs)

    def _build_deps(
        self,
        *,
        call_model: Any,
        executor: Any,
        approver: Any,
        compactor: SimpleCompactor,
        hook_registry: HookRegistry,
    ) -> Any:
        kwargs: dict[str, Any] = dict(
            call_model=call_model,
            run_tool=executor.run,
            approve=approver.check,
            compact=compactor.compact,
        )
        if self.options.wire_hook_registry_in_deps:
            kwargs["hook_registry"] = hook_registry
        if self.options.wire_audit_logger_in_deps:
            from duh.security.audit import AuditLogger
            kwargs["audit_logger"] = AuditLogger()
        return self._deps_cls()(**kwargs)

    def _patch_child_agent_tools(
        self,
        tools: list[Any],
        deps: Any,
        parent_model: Any = "",
    ) -> None:
        """Inject parent deps + tool list into AgentTool/SwarmTool instances.

        ``parent_model`` may be a string (static snapshot) or a callable
        getter — the latter lets ``/model`` switches at runtime flow through
        to tier resolution (``small`` / ``medium`` / ``large``).
        """
        for t in tools:
            if getattr(t, "name", "") in ("Agent", "Swarm"):
                t._parent_deps = deps
                t._parent_tools = tools
                t._parent_model = parent_model

    def _build_structured_logger(self) -> Any:
        if getattr(self.args, "log_json", False) or os.environ.get(
            "DUH_LOG_JSON", ""
        ) == "1":
            from duh.adapters.structured_logging import StructuredLogger
            return StructuredLogger()
        return None

    # -- Session resume --------------------------------------------------

    async def _maybe_resume_session(
        self,
        *,
        engine: Any,
        store: Any,
        compactor: SimpleCompactor,
        deps: Any,
    ) -> None:
        args = self.args
        session_id = (
            getattr(args, "session_id", None)
            if self.options.allow_session_id_override
            else None
        )
        should_resume = (
            getattr(args, "continue_session", False)
            or getattr(args, "resume", None)
            or session_id
        )
        if not should_resume:
            return

        try:
            resume_id = getattr(args, "resume", None) or session_id
            if resume_id:
                prev = await store.load(resume_id)
            else:
                sessions = await store.list_sessions()
                if sessions:
                    latest = sorted(
                        sessions, key=lambda s: s.get("modified", ""), reverse=True
                    )[0]
                    prev = await store.load(
                        latest.get("session_id") or latest.get("id", "")
                    )
                else:
                    prev = None

            if prev:
                for m in prev:
                    role = (
                        m.get("role", "user")
                        if isinstance(m, dict)
                        else getattr(m, "role", "user")
                    )
                    content = (
                        m.get("content", "")
                        if isinstance(m, dict)
                        else getattr(m, "content", "")
                    )
                    meta = (
                        m.get("metadata", {})
                        if isinstance(m, dict)
                        else getattr(m, "metadata", {})
                    )
                    engine._messages.append(
                        Message(role=role, content=content, metadata=meta or {})
                    )
                if self.debug:
                    logger.debug("resumed %d messages", len(prev))

                # Restore coordinator mode (ADR-063)
                if (
                    engine._messages
                    and engine._messages[0].metadata.get("coordinator_mode")
                ):
                    from duh.kernel.coordinator import COORDINATOR_SYSTEM_PROMPT
                    current = engine._config.system_prompt
                    if isinstance(current, str) and not current.startswith(
                        COORDINATOR_SYSTEM_PROMPT
                    ):
                        engine._config.system_prompt = (
                            COORDINATOR_SYSTEM_PROMPT + "\n\n" + current
                        )

                # --summarize compacts on resume (ADR-058)
                if getattr(args, "summarize", False) and engine._messages:
                    if deps.compact:
                        before = len(engine._messages)
                        engine._messages = await deps.compact(
                            engine._messages,
                            token_limit=compactor.default_limit // 2,
                        )
                        after = len(engine._messages)
                        if self.debug:
                            logger.debug(
                                "summarize: compacted %d -> %d messages", before, after
                            )
            elif self.debug:
                logger.debug("no session to resume")
        except Exception as e:
            import traceback
            logger.debug("resume failed: %s", e)
            if self.debug:
                traceback.print_exc(file=sys.stderr)

    # -- task_manager helper --------------------------------------------

    @staticmethod
    def _find_task_manager(tools: list[Any]) -> Any:
        for t in tools:
            if getattr(t, "name", None) == "Task" and hasattr(t, "task_manager"):
                return t.task_manager
        return None

    # -- Main orchestration ---------------------------------------------

    async def build(
        self,
        *,
        provider_factories: dict[str, Callable[[str], Any]] | None = None,
    ) -> SessionBuild:
        """Run the full shared setup sequence and return a SessionBuild.

        Raises :class:`ProviderResolutionError` when no provider can be
        resolved — callers translate this into CLI exit codes + stderr.
        """
        provider_name, model, call_model, err = self._build_provider(
            provider_factories=provider_factories
        )
        if err is not None:
            raise ProviderResolutionError(err, provider_name=provider_name)

        if self.debug:
            sys.stderr.write(f"[DEBUG] provider={provider_name} model={model}\n")

        # Tools + path policy + skills + deferred tools
        path_policy = self._build_path_policy()
        loaded_skills = self._load_skills()
        deferred_tools = self._discover_deferred_tools()
        tools = self._build_tools(path_policy, loaded_skills, deferred_tools)

        # Config + hooks + MCP
        app_config, hook_registry, mcp_executor = self._load_config()
        await self._connect_mcp(mcp_executor, tools)

        # System prompt
        system_prompt, loaded_templates = self._build_system_prompt(
            loaded_skills, deferred_tools
        )

        # Executor + approver + compactor + store
        # FileStore is imported lazily so legacy unit tests that patch
        # ``duh.adapters.file_store.FileStore`` still intercept construction.
        from duh.adapters.file_store import FileStore
        executor = self._native_executor_cls()(tools=tools, cwd=self.cwd)
        approver = self._build_approver()
        compactor = SimpleCompactor()
        store = FileStore(cwd=self.cwd)

        # Deps — then patch child agent tools
        deps = self._build_deps(
            call_model=call_model,
            executor=executor,
            approver=approver,
            compactor=compactor,
            hook_registry=hook_registry,
        )
        # Initial patch with a static model snapshot; we re-patch below with
        # a live getter once the Engine exists so /model switches propagate
        # to tier resolution (small/medium/large).
        self._patch_child_agent_tools(tools, deps, parent_model=model)

        # Engine
        engine_config = self._build_engine_config(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            app_config=app_config,
        )
        structured_logger = self._build_structured_logger()
        engine = self._engine_cls()(
            deps=deps,
            config=engine_config,
            session_store=store,
            structured_logger=structured_logger,
        )
        # Surface the current model to tools (e.g. ReadTool size guard) via
        # ToolContext. Using a lambda so /model switches are picked up live.
        if hasattr(executor, "get_current_model"):
            executor.get_current_model = lambda: engine._config.model
        # Re-patch agent tools with a live getter so tier resolution
        # (small/medium/large) tracks /model switches mid-session.
        for t in tools:
            if getattr(t, "name", "") in ("Agent", "Swarm"):
                t._parent_model = lambda: engine._config.model

        # Optional session_id override (print-mode)
        session_id = (
            getattr(self.args, "session_id", None)
            if self.options.allow_session_id_override
            else None
        )
        if session_id:
            engine._session_id = session_id

        deps.session_id = engine.session_id

        # Resume
        await self._maybe_resume_session(
            engine=engine, store=store, compactor=compactor, deps=deps
        )

        return SessionBuild(
            engine=engine,
            deps=deps,
            tools=tools,
            executor=executor,
            provider_name=provider_name,
            model=model,
            call_model=call_model,
            approver=approver,
            compactor=compactor,
            store=store,
            mcp_executor=mcp_executor,
            hook_registry=hook_registry,
            structured_logger=structured_logger,
            task_manager=self._find_task_manager(tools),
            loaded_templates=loaded_templates,
            loaded_skills=loaded_skills,
            app_config=app_config,
        )
