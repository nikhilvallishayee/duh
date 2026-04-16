"""Extended branch coverage for duh.cli.runner.

Targets the branches not covered by test_runner_coverage.py:
  - plugin loading + deferred tools building
  - --allowedTools / --disallowedTools filtering
  - --system-prompt-file loading (success & failure)
  - instruction_list injection
  - memory_prompt injection
  - skill descriptions injection
  - deferred tools system-prompt injection
  - --mcp-config flag (json-string and file path)
  - MCP connection + tool wrapping
  - --max-thinking-tokens (enabled/disabled)
  - structured_logger wiring
  - --session-id, --continue, --resume
  - session-start / session-end hooks (with failure)
  - structured_logger session_end
  - MCP disconnect (with failure)
"""

from __future__ import annotations

import argparse
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.runner import run_print_mode
from duh.kernel.messages import Message


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        prompt="test",
        debug=False,
        verbose=False,
        provider="anthropic",
        model=None,
        fallback_model=None,
        max_turns=10,
        max_cost=None,
        dangerously_skip_permissions=True,
        permission_mode=None,
        output_format="text",
        input_format="text",
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
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _fake_engine(events=None):
    events = events or [{"type": "done", "stop_reason": "end_turn"}]

    async def fake_run(prompt, **kwargs):
        for e in events:
            yield e

    m = MagicMock()
    m.run = fake_run
    m.session_id = "test-session"
    m.turn_count = 0
    m.total_input_tokens = 0
    m.total_output_tokens = 0
    m._messages = []
    return m


class _InfraPatch:
    """Patch all runner infra so tests don't need to spin up real providers."""

    def __init__(self, *, mock_engine, extras=None):
        self._extras = extras or {}
        self._mock_engine = mock_engine
        self._patches = []

    def __enter__(self):
        p_list = [
            patch("duh.cli.runner.Engine", return_value=self._mock_engine),
            patch("duh.cli.runner.AnthropicProvider"),
            patch("duh.cli.runner.NativeExecutor"),
            patch("duh.cli.runner.get_all_tools", return_value=[]),
        ]
        for p in p_list:
            p.__enter__()
            self._patches.append(p)
        for p in self._extras.values():
            p.__enter__()
            self._patches.append(p)
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(None, None, None)


# ----------------------------------------------------------------------------
# Plugin & deferred tools
# ----------------------------------------------------------------------------


class TestPluginsAndDeferred:
    @pytest.mark.asyncio
    async def test_plugin_loads_deferred_tool(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        fake_plugin_tool = SimpleNamespace(
            name="FakePluginTool",
            description="does fake things",
            input_schema={"type": "object", "properties": {}},
        )
        fake_plugin_registry = MagicMock()
        fake_plugin_registry.plugin_tools = [fake_plugin_tool]

        args = _make_args()

        with patch("duh.plugins.discover_plugins", return_value=[MagicMock()]):
            with patch(
                "duh.plugins.PluginRegistry", return_value=fake_plugin_registry
            ):
                with _InfraPatch(mock_engine=_fake_engine()):
                    code = await run_print_mode(args)
        assert code == 0


# ----------------------------------------------------------------------------
# --allowedTools / --disallowedTools
# ----------------------------------------------------------------------------


class TestToolFiltering:
    @pytest.mark.asyncio
    async def test_allowed_filters_tools(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        def fake_tools(skills=None, deferred_tools=None, path_policy=None):
            return [
                SimpleNamespace(name="Read"),
                SimpleNamespace(name="Write"),
                SimpleNamespace(name="Bash"),
            ]

        args = _make_args(allowedTools="Read,Write")

        with patch("duh.cli.runner.Engine", return_value=_fake_engine()):
            with patch("duh.cli.runner.AnthropicProvider"):
                with patch("duh.cli.runner.NativeExecutor") as mock_exec:
                    with patch("duh.cli.runner.get_all_tools", side_effect=fake_tools):
                        code = await run_print_mode(args)
        assert code == 0
        # The tools passed to NativeExecutor should be filtered
        call_args = mock_exec.call_args
        tools_passed = call_args.kwargs.get("tools") or call_args.args[0]
        names = {getattr(t, "name", "") for t in tools_passed}
        assert "Bash" not in names
        assert "Read" in names
        assert "Write" in names

    @pytest.mark.asyncio
    async def test_disallowed_filters_tools(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        def fake_tools(skills=None, deferred_tools=None, path_policy=None):
            return [
                SimpleNamespace(name="Read"),
                SimpleNamespace(name="Write"),
                SimpleNamespace(name="Bash"),
            ]

        args = _make_args(disallowedTools="Bash")

        with patch("duh.cli.runner.Engine", return_value=_fake_engine()):
            with patch("duh.cli.runner.AnthropicProvider"):
                with patch("duh.cli.runner.NativeExecutor") as mock_exec:
                    with patch("duh.cli.runner.get_all_tools", side_effect=fake_tools):
                        code = await run_print_mode(args)
        assert code == 0
        call_args = mock_exec.call_args
        tools_passed = call_args.kwargs.get("tools") or call_args.args[0]
        names = {getattr(t, "name", "") for t in tools_passed}
        assert "Bash" not in names
        assert "Read" in names


# ----------------------------------------------------------------------------
# --system-prompt-file
# ----------------------------------------------------------------------------


class TestSystemPromptFile:
    @pytest.mark.asyncio
    async def test_loads_from_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        spf = tmp_path / "prompt.txt"
        spf.write_text("my custom system prompt")

        args = _make_args(system_prompt_file=str(spf))

        with _InfraPatch(mock_engine=_fake_engine()):
            code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_file_read_failure(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        args = _make_args(system_prompt_file="/no/such/file.txt")

        with _InfraPatch(mock_engine=_fake_engine()):
            code = await run_print_mode(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "Could not read system prompt file" in captured.err


# ----------------------------------------------------------------------------
# Instruction list + memory + skills + deferred tools injected into prompt
# ----------------------------------------------------------------------------


class TestPromptParts:
    @pytest.mark.asyncio
    async def test_instruction_list_as_list(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        with patch("duh.config.load_instructions", return_value=["rule 1", "rule 2"]):
            with _InfraPatch(mock_engine=_fake_engine()):
                args = _make_args()
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_instruction_list_as_string(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        with patch("duh.config.load_instructions", return_value="single string"):
            with _InfraPatch(mock_engine=_fake_engine()):
                args = _make_args()
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_memory_prompt_appended(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        with patch("duh.kernel.memory.build_memory_prompt", return_value="<memory>hi</memory>"):
            with _InfraPatch(mock_engine=_fake_engine()):
                args = _make_args()
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_skills_injected_into_prompt(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        skill = SimpleNamespace(
            name="my-skill",
            description="does things",
            argument_hint="<arg>",
        )
        with patch("duh.kernel.skill.load_all_skills", return_value=[skill]):
            with _InfraPatch(mock_engine=_fake_engine()):
                args = _make_args()
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_skill_without_argument_hint(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        skill = SimpleNamespace(
            name="no-hint-skill",
            description="no arg hint",
            argument_hint="",
        )
        with patch("duh.kernel.skill.load_all_skills", return_value=[skill]):
            with _InfraPatch(mock_engine=_fake_engine()):
                args = _make_args()
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_templates_injected(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        tmpl = SimpleNamespace(name="t1", description="template 1")
        with patch("duh.kernel.templates.load_all_templates", return_value=[tmpl]):
            with _InfraPatch(mock_engine=_fake_engine()):
                args = _make_args()
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_deferred_tools_injected(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        fake_tool = SimpleNamespace(
            name="FakeDeferred",
            description="deferred",
            input_schema={"type": "object"},
        )
        fake_registry = MagicMock()
        fake_registry.plugin_tools = [fake_tool]

        with patch("duh.plugins.discover_plugins", return_value=[MagicMock()]):
            with patch("duh.plugins.PluginRegistry", return_value=fake_registry):
                with _InfraPatch(mock_engine=_fake_engine()):
                    args = _make_args()
                    code = await run_print_mode(args)
        assert code == 0


# ----------------------------------------------------------------------------
# MCP config
# ----------------------------------------------------------------------------


class TestMcpConfig:
    @pytest.mark.asyncio
    async def test_mcp_config_json_string(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mcp_data = {"mcpServers": {"foo": {"command": "bar"}}}
        args = _make_args(mcp_config=json.dumps(mcp_data))

        mock_mcp_executor = MagicMock()
        mock_mcp_executor.connect_all = AsyncMock(return_value={})
        mock_mcp_executor.disconnect_all = AsyncMock()

        with patch(
            "duh.adapters.mcp_executor.MCPExecutor.from_config",
            return_value=mock_mcp_executor,
        ):
            with _InfraPatch(mock_engine=_fake_engine()):
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_mcp_config_file_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        cfg_file = tmp_path / "mcp.json"
        cfg_file.write_text(json.dumps({"mcpServers": {"foo": {"command": "bar"}}}))

        args = _make_args(mcp_config=str(cfg_file))

        mock_mcp_executor = MagicMock()
        mock_mcp_executor.connect_all = AsyncMock(return_value={})
        mock_mcp_executor.disconnect_all = AsyncMock()

        with patch(
            "duh.adapters.mcp_executor.MCPExecutor.from_config",
            return_value=mock_mcp_executor,
        ):
            with _InfraPatch(mock_engine=_fake_engine()):
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_mcp_config_invalid_json(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        args = _make_args(mcp_config="not valid json {{{")

        with _InfraPatch(mock_engine=_fake_engine()):
            code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_mcp_connect_discovers_tools(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        fake_tool_info = SimpleNamespace(
            name="mcp_tool",
            description="tool",
            server_name="s1",
            tool_name="t",
            input_schema={"type": "object"},
        )

        mock_mcp_executor = MagicMock()
        mock_mcp_executor.connect_all = AsyncMock(
            return_value={"s1": [fake_tool_info]}
        )
        mock_mcp_executor.disconnect_all = AsyncMock()

        mock_config = MagicMock()
        mock_config.mcp_servers = {"mcpServers": {"s1": {"command": "foo"}}}
        mock_config.hooks = None

        # debug=True covers the "MCP tool registered: %s" debug log branch
        args = _make_args(debug=True)

        with patch("duh.config.load_config", return_value=mock_config):
            with patch(
                "duh.adapters.mcp_executor.MCPExecutor.from_config",
                return_value=mock_mcp_executor,
            ):
                with patch("duh.tools.mcp_tool.MCPToolWrapper") as MockWrapper:
                    MockWrapper.return_value = SimpleNamespace(name="mcp_tool")
                    with _InfraPatch(mock_engine=_fake_engine()):
                        code = await run_print_mode(args)
        assert code == 0
        mock_mcp_executor.connect_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mcp_connect_failure_swallowed(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_mcp_executor = MagicMock()
        mock_mcp_executor.connect_all = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        mock_mcp_executor.disconnect_all = AsyncMock()

        mock_config = MagicMock()
        mock_config.mcp_servers = {"mcpServers": {"s1": {"command": "foo"}}}
        mock_config.hooks = None

        args = _make_args(debug=True)

        with patch("duh.config.load_config", return_value=mock_config):
            with patch(
                "duh.adapters.mcp_executor.MCPExecutor.from_config",
                return_value=mock_mcp_executor,
            ):
                with _InfraPatch(mock_engine=_fake_engine()):
                    code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_mcp_disconnect_failure_swallowed(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_mcp_executor = MagicMock()
        mock_mcp_executor.connect_all = AsyncMock(return_value={})
        mock_mcp_executor.disconnect_all = AsyncMock(side_effect=RuntimeError("bad"))

        mock_config = MagicMock()
        mock_config.mcp_servers = {"mcpServers": {"s1": {"command": "foo"}}}
        mock_config.hooks = None

        args = _make_args()

        with patch("duh.config.load_config", return_value=mock_config):
            with patch(
                "duh.adapters.mcp_executor.MCPExecutor.from_config",
                return_value=mock_mcp_executor,
            ):
                with _InfraPatch(mock_engine=_fake_engine()):
                    code = await run_print_mode(args)
        assert code == 0


# ----------------------------------------------------------------------------
# Hooks
# ----------------------------------------------------------------------------


class TestHooks:
    @pytest.mark.asyncio
    async def test_hooks_from_config(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_config = MagicMock()
        mock_config.mcp_servers = None
        mock_config.hooks = {"PreToolUse": []}

        args = _make_args()

        with patch("duh.config.load_config", return_value=mock_config):
            with patch("duh.hooks.HookRegistry.from_config") as mock_from_config:
                mock_from_config.return_value = MagicMock()
                with _InfraPatch(mock_engine=_fake_engine()):
                    code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_config_load_failure(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        args = _make_args()

        with patch("duh.config.load_config", side_effect=RuntimeError("bad config")):
            with _InfraPatch(mock_engine=_fake_engine()):
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_session_start_hook_exception_swallowed(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        async def bad_exec(*a, **kw):
            raise RuntimeError("hook bad")

        args = _make_args()

        with patch("duh.hooks.execute_hooks", side_effect=bad_exec):
            with _InfraPatch(mock_engine=_fake_engine()):
                code = await run_print_mode(args)
        assert code == 0


# ----------------------------------------------------------------------------
# Thinking config
# ----------------------------------------------------------------------------


class TestThinking:
    @pytest.mark.asyncio
    async def test_thinking_enabled(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        captured = {}

        def capture_engine(*a, **kw):
            captured["config"] = kw.get("config")
            return _fake_engine()

        args = _make_args(max_thinking_tokens=1024)

        with patch("duh.cli.runner.Engine", side_effect=capture_engine):
            with patch("duh.cli.runner.AnthropicProvider"):
                with patch("duh.cli.runner.NativeExecutor"):
                    with patch("duh.cli.runner.get_all_tools", return_value=[]):
                        await run_print_mode(args)

        cfg = captured["config"]
        assert cfg.thinking == {"type": "enabled", "budget_tokens": 1024}

    @pytest.mark.asyncio
    async def test_thinking_disabled(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        captured = {}

        def capture_engine(*a, **kw):
            captured["config"] = kw.get("config")
            return _fake_engine()

        args = _make_args(max_thinking_tokens=0)

        with patch("duh.cli.runner.Engine", side_effect=capture_engine):
            with patch("duh.cli.runner.AnthropicProvider"):
                with patch("duh.cli.runner.NativeExecutor"):
                    with patch("duh.cli.runner.get_all_tools", return_value=[]):
                        await run_print_mode(args)

        cfg = captured["config"]
        assert cfg.thinking == {"type": "disabled"}


# ----------------------------------------------------------------------------
# Structured JSON logger
# ----------------------------------------------------------------------------


class TestStructuredLogger:
    @pytest.mark.asyncio
    async def test_structured_logger_wired(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_logger = MagicMock()

        args = _make_args(log_json=True)

        with patch(
            "duh.adapters.structured_logging.StructuredLogger",
            return_value=mock_logger,
        ):
            with _InfraPatch(mock_engine=_fake_engine()):
                code = await run_print_mode(args)
        assert code == 0
        mock_logger.session_end.assert_called_once()
        mock_logger.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_structured_logger_via_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("DUH_LOG_JSON", "1")

        mock_logger = MagicMock()
        args = _make_args(log_json=False)

        with patch(
            "duh.adapters.structured_logging.StructuredLogger",
            return_value=mock_logger,
        ):
            with _InfraPatch(mock_engine=_fake_engine()):
                code = await run_print_mode(args)
        assert code == 0


# ----------------------------------------------------------------------------
# Session resume
# ----------------------------------------------------------------------------


class TestSessionResume:
    @pytest.mark.asyncio
    async def test_session_id_override(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _fake_engine()

        async def fake_load(sid):
            return []

        mock_store = MagicMock()
        mock_store.load = fake_load
        mock_store.list_sessions = AsyncMock(return_value=[])

        args = _make_args(session_id="custom-id")

        with patch("duh.adapters.file_store.FileStore", return_value=mock_store):
            with _InfraPatch(mock_engine=mock_engine):
                code = await run_print_mode(args)
        assert code == 0
        assert mock_engine._session_id == "custom-id"

    @pytest.mark.asyncio
    async def test_resume_by_id_with_messages(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _fake_engine()

        async def fake_load(sid):
            return [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

        mock_store = MagicMock()
        mock_store.load = fake_load
        mock_store.list_sessions = AsyncMock(return_value=[])

        args = _make_args(resume="abc-123")

        with patch("duh.adapters.file_store.FileStore", return_value=mock_store):
            with _InfraPatch(mock_engine=mock_engine):
                code = await run_print_mode(args)
        assert code == 0
        assert len(mock_engine._messages) == 2

    @pytest.mark.asyncio
    async def test_continue_latest_session(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _fake_engine()

        async def fake_load(sid):
            return [{"role": "user", "content": "prev"}]

        mock_store = MagicMock()
        mock_store.load = fake_load
        mock_store.list_sessions = AsyncMock(return_value=[
            {"session_id": "s1", "modified": "2026-01-01"},
            {"session_id": "s2", "modified": "2026-02-01"},
        ])

        args = _make_args(continue_session=True, debug=True)

        with patch("duh.adapters.file_store.FileStore", return_value=mock_store):
            with _InfraPatch(mock_engine=mock_engine):
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_continue_no_sessions(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _fake_engine()
        mock_store = MagicMock()
        mock_store.load = AsyncMock(return_value=None)
        mock_store.list_sessions = AsyncMock(return_value=[])

        args = _make_args(continue_session=True, debug=True)

        with patch("duh.adapters.file_store.FileStore", return_value=mock_store):
            with _InfraPatch(mock_engine=mock_engine):
                code = await run_print_mode(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_resume_with_message_objects(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _fake_engine()

        # Return Message-like objects (not dicts)
        class _M:
            role = "user"
            content = "hey"

        async def fake_load(sid):
            return [_M(), _M()]

        mock_store = MagicMock()
        mock_store.load = fake_load
        mock_store.list_sessions = AsyncMock(return_value=[])

        args = _make_args(resume="abc")

        with patch("duh.adapters.file_store.FileStore", return_value=mock_store):
            with _InfraPatch(mock_engine=mock_engine):
                code = await run_print_mode(args)
        assert code == 0
        assert len(mock_engine._messages) == 2

    @pytest.mark.asyncio
    async def test_resume_failure_swallowed(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _fake_engine()
        mock_store = MagicMock()
        mock_store.load = AsyncMock(side_effect=RuntimeError("load broke"))
        mock_store.list_sessions = AsyncMock(return_value=[])

        args = _make_args(resume="abc", debug=True)

        with patch("duh.adapters.file_store.FileStore", return_value=mock_store):
            with _InfraPatch(mock_engine=mock_engine):
                code = await run_print_mode(args)
        assert code == 0
