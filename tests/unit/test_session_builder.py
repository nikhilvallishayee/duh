"""Tests for duh.cli.session_builder.

The builder extracts the shared setup sequence between ``run_repl`` and
``run_print_mode`` (issue #18 / CQ-4).  Its job is to return a
:class:`SessionBuild` with every dependency wired together:

  * provider resolution -> call_model + model name
  * tools (+ plugin-discovered deferred tools, + skills, + MCP-wrapped)
  * PathPolicy from git root
  * system prompt (base + git context + optional extras)
  * NativeExecutor + approver + compactor + FileStore
  * Deps (with AgentTool/SwarmTool parent patching)
  * Engine + optional structured logger
  * session resume

These tests focus on the contract: a valid build comes out, the wiring is
correct, and optional behaviour (MCP merge, session resume, Agent-tool
patching) matches what the legacy runners did.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.session_builder import (
    ProviderResolutionError,
    SessionBuild,
    SessionBuilder,
    SessionBuilderOptions,
    _BuilderPatchTargets,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(**overrides) -> argparse.Namespace:
    """Build a Namespace mirroring the CLI parser defaults."""
    defaults = dict(
        prompt="",
        debug=False,
        provider="anthropic",
        model=None,
        fallback_model=None,
        max_turns=10,
        max_cost=None,
        dangerously_skip_permissions=True,  # skip interactive prompts in tests
        permission_mode=None,
        output_format="text",
        system_prompt=None,
        system_prompt_file=None,
        tool_choice=None,
        continue_session=False,
        resume=None,
        session_id=None,
        brief=False,
        log_json=False,
        allowedTools=None,
        disallowedTools=None,
        mcp_config=None,
        max_thinking_tokens=None,
        coordinator=False,
        summarize=False,
        approval_mode=None,
        i_understand_the_lethal_trifecta=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _default_options() -> SessionBuilderOptions:
    return SessionBuilderOptions(
        include_skills_in_tools=False,
        include_deferred_tools=False,
        include_memory_prompt=False,
        include_env_block=False,
        include_templates_hint=False,
        include_model_context_block=False,
        honour_tool_filters=True,
        approver_mode="print_mode",
        wire_hook_registry_in_deps=False,
        wire_audit_logger_in_deps=False,  # skip audit logger in tests
        honour_tool_choice=True,
        honour_thinking=True,
        allow_session_id_override=True,
        log_skip_perms_warning=False,
        default_system_prompt="base system prompt",
        brief_instruction="BRIEF",
    )


def _stub_backend(model: str = "stub-model"):
    async def fake_call(*a, **kw):
        yield {"type": "text_delta", "text": ""}

    return SimpleNamespace(
        ok=True, error=None, model=model, call_model=fake_call
    )


def _make_builder_with_stubs(
    *,
    args=None,
    options=None,
    tools=None,
    resolve_provider_fn=None,
    build_backend_fn=None,
    cwd="/tmp",
):
    """Construct a SessionBuilder wired to stubbed infrastructure."""
    args = args or _args()
    options = options or _default_options()
    tools_list = list(tools if tools is not None else [])

    resolve_fn = resolve_provider_fn or (lambda **kw: "anthropic")
    build_fn = build_backend_fn or (lambda *a, **kw: _stub_backend())

    patch_targets = _BuilderPatchTargets(
        get_all_tools_fn=lambda **kw: list(tools_list),
        resolve_provider_name_fn=resolve_fn,
        build_model_backend_fn=build_fn,
    )
    return SessionBuilder(
        args, options, cwd=cwd, debug=False, patch_targets=patch_targets,
    )


# ---------------------------------------------------------------------------
# 1. build() produces a valid SessionBuild with all components wired
# ---------------------------------------------------------------------------


class TestBuildProducesValidSessionBuild:
    @pytest.mark.asyncio
    async def test_build_returns_session_build_with_every_field_populated(
        self, monkeypatch
    ):
        builder = _make_builder_with_stubs()
        build = await builder.build()

        assert isinstance(build, SessionBuild)
        assert build.provider_name == "anthropic"
        assert build.model == "stub-model"
        assert build.call_model is not None
        assert build.engine is not None
        assert build.deps is not None
        assert build.executor is not None
        assert build.approver is not None
        assert build.compactor is not None
        assert build.store is not None
        # No MCP / no structured logger unless wired.
        assert build.mcp_executor is None
        assert build.structured_logger is None

    @pytest.mark.asyncio
    async def test_deps_wires_call_model_and_run_tool(self):
        builder = _make_builder_with_stubs()
        build = await builder.build()

        # call_model and run_tool must both be present on Deps.  Bound
        # methods compare equal when they share the same instance + func
        # but are not necessarily ``is`` identical, so use == here.
        assert build.deps.call_model is build.call_model
        assert build.deps.run_tool == build.executor.run
        # approve and compact must be wired through the approver / compactor.
        assert build.deps.approve == build.approver.check
        assert build.deps.compact == build.compactor.compact
        # session_id is synced to the engine's session_id.
        assert build.deps.session_id == build.engine.session_id

    @pytest.mark.asyncio
    async def test_engine_config_has_system_prompt_and_tools(self):
        tool = SimpleNamespace(name="Read", input_schema={}, description="")
        builder = _make_builder_with_stubs(tools=[tool])
        build = await builder.build()

        cfg = build.engine._config
        assert "base system prompt" in cfg.system_prompt
        assert cfg.tools is build.tools
        assert tool in build.tools

    @pytest.mark.asyncio
    async def test_no_provider_raises_provider_resolution_error(self):
        builder = _make_builder_with_stubs(
            resolve_provider_fn=lambda **kw: None,
        )
        with pytest.raises(ProviderResolutionError) as exc_info:
            await builder.build()
        assert exc_info.value.provider_name is None
        assert "No provider available" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_backend_failure_raises_with_provider_name(self):
        bad_backend = SimpleNamespace(
            ok=False, error="missing API key", model=None, call_model=None,
        )
        builder = _make_builder_with_stubs(
            build_backend_fn=lambda *a, **kw: bad_backend,
        )
        with pytest.raises(ProviderResolutionError) as exc_info:
            await builder.build()
        # provider_name is set (resolution succeeded); build failure is
        # carried in the message.
        assert exc_info.value.provider_name == "anthropic"
        assert exc_info.value.message == "missing API key"


# ---------------------------------------------------------------------------
# 2. AgentTool / SwarmTool parent deps+tools patching
# ---------------------------------------------------------------------------


class TestAgentAndSwarmToolPatching:
    @pytest.mark.asyncio
    async def test_agent_tool_gets_parent_deps_and_tools(self):
        # Build two stub tools: a regular tool and an Agent tool that
        # should receive parent injection.
        agent_tool = SimpleNamespace(name="Agent", input_schema={}, description="")
        read_tool = SimpleNamespace(name="Read", input_schema={}, description="")

        builder = _make_builder_with_stubs(tools=[agent_tool, read_tool])
        build = await builder.build()

        # Patching happens in place on the tool instance.
        assert agent_tool._parent_deps is build.deps
        assert agent_tool._parent_tools is build.tools
        # Non-agent tools must NOT be patched.
        assert not hasattr(read_tool, "_parent_deps")

    @pytest.mark.asyncio
    async def test_swarm_tool_gets_parent_deps_and_tools(self):
        swarm_tool = SimpleNamespace(name="Swarm", input_schema={}, description="")
        builder = _make_builder_with_stubs(tools=[swarm_tool])
        build = await builder.build()

        assert swarm_tool._parent_deps is build.deps
        assert swarm_tool._parent_tools is build.tools

    @pytest.mark.asyncio
    async def test_tools_without_name_are_ignored(self):
        # A defensive check: the builder iterates with getattr(..., "name", "")
        # so tools that have no name should not crash the patching step.
        weird_tool = SimpleNamespace(input_schema={}, description="")
        builder = _make_builder_with_stubs(tools=[weird_tool])
        build = await builder.build()
        # No exception raised; weird_tool still untouched.
        assert not hasattr(weird_tool, "_parent_deps")
        assert weird_tool in build.tools


# ---------------------------------------------------------------------------
# 3. PathPolicy is constructed from git root
# ---------------------------------------------------------------------------


class TestPathPolicyFromGitRoot:
    @pytest.mark.asyncio
    async def test_path_policy_uses_git_root_when_available(self, tmp_path):
        # Create a fake .git dir so _find_git_root finds this tmp_path.
        (tmp_path / ".git").mkdir()

        captured_root: dict[str, str] = {}

        def fake_path_policy(root: str):
            captured_root["root"] = root
            return MagicMock()

        with patch(
            "duh.security.path_policy.PathPolicy", side_effect=fake_path_policy
        ):
            builder = _make_builder_with_stubs(cwd=str(tmp_path))
            await builder.build()

        # The git root we just created should be the one passed to PathPolicy.
        assert captured_root["root"] == str(tmp_path.resolve())

    @pytest.mark.asyncio
    async def test_path_policy_falls_back_to_cwd_when_no_git_root(self, tmp_path):
        # No .git anywhere in tmp_path; cwd should be the policy root.
        captured_root: dict[str, str] = {}

        def fake_path_policy(root: str):
            captured_root["root"] = root
            return MagicMock()

        # Ensure the walk from tmp_path hits the filesystem root without
        # finding a .git (tmp_path is under /private/tmp or similar which
        # isn't a git repo).
        with patch(
            "duh.security.path_policy.PathPolicy", side_effect=fake_path_policy
        ):
            with patch(
                "duh.config._find_git_root", return_value=None
            ):
                builder = _make_builder_with_stubs(cwd=str(tmp_path))
                await builder.build()

        assert captured_root["root"] == str(tmp_path)


# ---------------------------------------------------------------------------
# 4. MCP tools are merged into the tool list
# ---------------------------------------------------------------------------


class TestMCPToolMerging:
    @pytest.mark.asyncio
    async def test_mcp_tools_appended_when_mcp_configured(self):
        # Stub app_config with an mcp_servers dict, a fake MCPExecutor that
        # returns discovered tools, and verify the builder appends them.
        mcp_executor = MagicMock()
        mcp_executor.connect_all = AsyncMock(
            return_value={
                "server-a": [
                    SimpleNamespace(
                        name="tool1",
                        description="first tool",
                        input_schema={},
                    )
                ]
            }
        )
        mcp_executor.disconnect_all = AsyncMock()

        fake_config = SimpleNamespace(
            mcp_servers={"server-a": {"command": "x"}},
            hooks=None,
            trifecta_acknowledged=False,
        )

        class FakeMCPTool:
            def __init__(self, info, executor):
                self.info = info
                self.executor = executor
                self.name = f"mcp_{info.name}"
                self.input_schema = info.input_schema

        with patch(
            "duh.config.load_config", return_value=fake_config
        ), patch(
            "duh.adapters.mcp_executor.MCPExecutor.from_config",
            return_value=mcp_executor,
        ), patch(
            "duh.tools.mcp_tool.MCPToolWrapper", side_effect=FakeMCPTool
        ):
            builder = _make_builder_with_stubs(tools=[])
            build = await builder.build()

        mcp_names = [getattr(t, "name", "") for t in build.tools]
        assert "mcp_tool1" in mcp_names
        assert build.mcp_executor is mcp_executor
        # Teardown should disconnect.
        await build.teardown_mcp()
        mcp_executor.disconnect_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mcp_absent_when_no_servers_configured(self):
        fake_config = SimpleNamespace(
            mcp_servers={}, hooks=None, trifecta_acknowledged=False,
        )
        with patch("duh.config.load_config", return_value=fake_config):
            builder = _make_builder_with_stubs(tools=[])
            build = await builder.build()

        assert build.mcp_executor is None


# ---------------------------------------------------------------------------
# 5. Session resume loads previous messages into the engine
# ---------------------------------------------------------------------------


class TestSessionResume:
    @pytest.mark.asyncio
    async def test_resume_by_id_loads_previous_messages(self):
        saved_messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        fake_store = MagicMock()

        async def fake_load(sid):
            return saved_messages

        fake_store.load = fake_load
        fake_store.list_sessions = AsyncMock(return_value=[])
        fake_store.save = AsyncMock()

        args = _args(resume="sid-123")

        with patch(
            "duh.adapters.file_store.FileStore", return_value=fake_store
        ):
            builder = _make_builder_with_stubs(args=args)
            build = await builder.build()

        assert len(build.engine._messages) == 2
        assert build.engine._messages[0].role == "user"
        assert build.engine._messages[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_continue_session_loads_latest(self):
        saved_messages = [{"role": "user", "content": "first"}]

        fake_store = MagicMock()

        async def fake_load(sid):
            return saved_messages

        fake_store.load = fake_load
        fake_store.list_sessions = AsyncMock(
            return_value=[
                {"session_id": "old", "modified": "2026-01-01"},
                {"session_id": "newest", "modified": "2026-04-01"},
            ]
        )
        fake_store.save = AsyncMock()

        args = _args(continue_session=True)

        with patch(
            "duh.adapters.file_store.FileStore", return_value=fake_store
        ):
            builder = _make_builder_with_stubs(args=args)
            build = await builder.build()

        assert len(build.engine._messages) == 1

    @pytest.mark.asyncio
    async def test_no_resume_when_flags_absent(self):
        fake_store = MagicMock()
        fake_store.load = AsyncMock(return_value=[{"role": "user", "content": "x"}])
        fake_store.list_sessions = AsyncMock(return_value=[])
        fake_store.save = AsyncMock()

        with patch(
            "duh.adapters.file_store.FileStore", return_value=fake_store
        ):
            builder = _make_builder_with_stubs()
            build = await builder.build()

        # Without --continue / --resume / --session-id, no messages loaded.
        assert build.engine._messages == []


# ---------------------------------------------------------------------------
# 6. Tool filtering honours --allowedTools / --disallowedTools
# ---------------------------------------------------------------------------


class TestToolFiltering:
    @pytest.mark.asyncio
    async def test_allowed_tools_filters_to_allowlist(self):
        t1 = SimpleNamespace(name="Read", input_schema={}, description="")
        t2 = SimpleNamespace(name="Edit", input_schema={}, description="")
        t3 = SimpleNamespace(name="Bash", input_schema={}, description="")

        args = _args(allowedTools="Read,Edit")
        builder = _make_builder_with_stubs(args=args, tools=[t1, t2, t3])
        build = await builder.build()

        names = {getattr(t, "name", "") for t in build.tools}
        assert "Read" in names
        assert "Edit" in names
        assert "Bash" not in names

    @pytest.mark.asyncio
    async def test_disallowed_tools_filters_blocklist(self):
        t1 = SimpleNamespace(name="Read", input_schema={}, description="")
        t2 = SimpleNamespace(name="Bash", input_schema={}, description="")

        args = _args(disallowedTools="Bash")
        builder = _make_builder_with_stubs(args=args, tools=[t1, t2])
        build = await builder.build()

        names = {getattr(t, "name", "") for t in build.tools}
        assert "Read" in names
        assert "Bash" not in names


# ---------------------------------------------------------------------------
# 7. Approver selection
# ---------------------------------------------------------------------------


class TestApproverSelection:
    @pytest.mark.asyncio
    async def test_print_mode_uses_auto_approver_with_skip_perms(self):
        from duh.adapters.approvers import AutoApprover
        args = _args(dangerously_skip_permissions=True)
        builder = _make_builder_with_stubs(args=args)
        build = await builder.build()
        assert isinstance(build.approver, AutoApprover)

    @pytest.mark.asyncio
    async def test_print_mode_uses_interactive_approver_by_default(self):
        from duh.adapters.approvers import InteractiveApprover
        args = _args(dangerously_skip_permissions=False, permission_mode=None)
        builder = _make_builder_with_stubs(args=args)
        build = await builder.build()
        assert isinstance(build.approver, InteractiveApprover)

    @pytest.mark.asyncio
    async def test_repl_mode_uses_tiered_approver_when_approval_mode_set(self):
        from duh.adapters.approvers import TieredApprover

        opts = _default_options()
        opts.approver_mode = "repl"
        args = _args(
            dangerously_skip_permissions=False, approval_mode="suggest",
        )
        builder = _make_builder_with_stubs(args=args, options=opts)
        build = await builder.build()
        assert isinstance(build.approver, TieredApprover)


# ---------------------------------------------------------------------------
# 8. Session-id override
# ---------------------------------------------------------------------------


class TestSessionIdOverride:
    @pytest.mark.asyncio
    async def test_session_id_overrides_engine_session_id(self):
        fake_store = MagicMock()
        fake_store.load = AsyncMock(return_value=[])
        fake_store.list_sessions = AsyncMock(return_value=[])
        fake_store.save = AsyncMock()

        args = _args(session_id="my-custom-id")

        with patch(
            "duh.adapters.file_store.FileStore", return_value=fake_store
        ):
            builder = _make_builder_with_stubs(args=args)
            build = await builder.build()

        assert build.engine._session_id == "my-custom-id"
        # Deps session_id is synced from engine.session_id after override.
        assert build.deps.session_id == build.engine.session_id

    @pytest.mark.asyncio
    async def test_session_id_ignored_when_override_disabled(self):
        fake_store = MagicMock()
        fake_store.load = AsyncMock(return_value=[])
        fake_store.list_sessions = AsyncMock(return_value=[])
        fake_store.save = AsyncMock()

        opts = _default_options()
        opts.allow_session_id_override = False
        args = _args(session_id="ignored")

        with patch(
            "duh.adapters.file_store.FileStore", return_value=fake_store
        ):
            builder = _make_builder_with_stubs(args=args, options=opts)
            build = await builder.build()

        # Engine kept its auto-generated session id.
        assert build.engine._session_id != "ignored"
