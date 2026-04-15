"""Tests for the interactive REPL."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from duh.cli.repl import SLASH_COMMANDS, _handle_slash
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig


def _make_engine() -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model")
    return Engine(deps=deps, config=config)


def _make_deps() -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock())


class TestSlashHelp:
    def test_help_returns_true(self, capsys):
        engine = _make_engine()
        keep, model = _handle_slash("/help", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "/help" in captured.out
        assert "/exit" in captured.out

    def test_all_commands_listed(self, capsys):
        engine = _make_engine()
        _handle_slash("/help", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        for cmd in SLASH_COMMANDS:
            assert cmd in captured.out


class TestSlashModel:
    def test_show_model(self, capsys):
        engine = _make_engine()
        keep, model = _handle_slash("/model", engine, "claude-sonnet-4-6", _make_deps())
        assert keep is True
        assert model == "claude-sonnet-4-6"
        captured = capsys.readouterr()
        assert "claude-sonnet-4-6" in captured.out

    def test_change_model(self, capsys):
        engine = _make_engine()
        keep, model = _handle_slash("/model claude-opus-4-6", engine, "old-model", _make_deps())
        assert keep is True
        assert model == "claude-opus-4-6"
        captured = capsys.readouterr()
        assert "claude-opus-4-6" in captured.out


class TestSlashStatus:
    def test_status_output(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash("/status", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "Session" in captured.out
        assert "Turns" in captured.out
        assert "Model" in captured.out


class TestSlashClear:
    def test_clear_messages(self, capsys):
        engine = _make_engine()
        # Add a fake message
        from duh.kernel.messages import Message
        engine._messages.append(Message(role="user", content="hi"))
        assert len(engine.messages) == 1

        keep, _ = _handle_slash("/clear", engine, "m", _make_deps())
        assert keep is True
        assert len(engine.messages) == 0
        captured = capsys.readouterr()
        assert "cleared" in captured.out.lower()


class TestSlashCost:
    def test_cost_output(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash("/cost", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "cost" in captured.out.lower()


class TestSlashExit:
    def test_exit_returns_false(self):
        engine = _make_engine()
        keep, _ = _handle_slash("/exit", engine, "m", _make_deps())
        assert keep is False


class TestSlashUnknown:
    def test_unknown_command(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash("/foobar", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "Unknown" in captured.out


class TestSlashCompact:
    def test_compact_no_compactor(self, capsys):
        engine = _make_engine()
        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        keep, _ = _handle_slash("/compact", engine, "m", deps)
        assert keep is True
        captured = capsys.readouterr()
        assert "No compactor" in captured.out


class TestSlashConnectAndModels:
    def test_models_lists_openai(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(repl_mod, "has_anthropic_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_available", lambda: True)
        monkeypatch.setattr(repl_mod, "has_openai_chatgpt_oauth", lambda: False)
        engine = _make_engine()
        keep, model = _handle_slash(
            "/models", engine, "gpt-4o", _make_deps(), provider_name="openai"
        )
        assert keep is True
        assert model == "gpt-4o"
        captured = capsys.readouterr()
        assert "Current provider: openai" in captured.out
        assert "[openai]" in captured.out
        assert "gpt-5.2-codex" in captured.out

    def test_models_use_switches_model(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(repl_mod, "resolve_openai_auth_mode", lambda _m: "chatgpt")
        engine = _make_engine()
        keep, model = _handle_slash(
            "/models use gpt-5.2-codex",
            engine,
            "gpt-4o",
            _make_deps(),
            provider_name="openai",
        )
        assert keep is True
        assert model == "gpt-5.2-codex"

    def test_model_command_switches_provider(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(repl_mod, "resolve_openai_auth_mode", lambda _m: "chatgpt")
        engine = _make_engine()
        deps = _make_deps()
        keep, model = _handle_slash(
            "/model codex", engine, "claude-sonnet-4-6", deps, provider_name="anthropic"
        )
        assert keep is True
        assert model == "gpt-5.2-codex"

    def test_models_lists_all_connected_providers(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(repl_mod, "has_openai_chatgpt_oauth", lambda: True)
        monkeypatch.setattr(
            repl_mod,
            "available_models_for_provider",
            lambda provider_name, current_model=None: (
                ["claude-sonnet-4-6"] if provider_name == "anthropic" else ["gpt-5.2-codex"]
            ),
        )

        class _FakeResponse:
            status_code = 200

        monkeypatch.setattr(
            "httpx.get",
            lambda *args, **kwargs: _FakeResponse(),
        )

        engine = _make_engine()
        keep, model = _handle_slash(
            "/models", engine, "gpt-5.2-codex", _make_deps(), provider_name="openai"
        )
        assert keep is True
        assert model == "gpt-5.2-codex"
        captured = capsys.readouterr()
        assert "[anthropic]" in captured.out
        assert "[openai]" in captured.out
        assert "[ollama]" in captured.out

    def test_connect_openai_chatgpt(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(
            repl_mod,
            "connect_openai_chatgpt_subscription",
            lambda input_fn=input: (True, "OpenAI ChatGPT subscription connected."),
        )
        engine = _make_engine()
        keep, model = _handle_slash(
            "/connect openai chatgpt", engine, "m", _make_deps()
        )
        assert keep is True
        assert model == "m"
        captured = capsys.readouterr()
        assert "connected" in captured.out.lower()


class TestMainReplRouting:
    def test_no_args_enters_repl(self, monkeypatch):
        """main() with no args should route to REPL, not print help."""
        from unittest.mock import patch
        with patch("duh.cli.repl.run_repl", new_callable=AsyncMock, return_value=0) as mock_repl:
            with patch("duh.cli.main.asyncio") as mock_asyncio:
                mock_asyncio.run = MagicMock(return_value=0)
                from duh.cli.main import main
                code = main([])
        assert mock_asyncio.run.called


class TestRunReplRegression:
    @pytest.mark.asyncio
    async def test_help_command_does_not_crash_on_template_state(self, monkeypatch, capsys):
        from duh.cli import repl as repl_mod
        from duh import config as config_mod
        from duh.cli import prewarm as prewarm_mod

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        class _FakeProvider:
            def __init__(self, *args, **kwargs):
                pass

            async def stream(self, **kwargs):
                if False:  # pragma: no cover
                    yield {}

        async def _no_prewarm(_call_model):
            return None

        monkeypatch.setattr(repl_mod, "AnthropicProvider", _FakeProvider)
        monkeypatch.setattr(repl_mod, "get_all_tools", lambda: [])
        monkeypatch.setattr(repl_mod, "_load_history", lambda: None)
        monkeypatch.setattr(repl_mod, "_setup_completion", lambda: None)
        monkeypatch.setattr(repl_mod, "_save_history", lambda: None)
        monkeypatch.setattr(prewarm_mod, "prewarm_connection", _no_prewarm)
        monkeypatch.setattr(
            config_mod,
            "load_config",
            lambda cwd=".": SimpleNamespace(mcp_servers={}),
        )

        inputs = iter(["/help", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        args = argparse.Namespace(
            debug=False,
            provider="anthropic",
            model="claude-sonnet-4-6",
            system_prompt=None,
            brief=False,
            approval_mode=None,
            dangerously_skip_permissions=False,
            max_turns=8,
            max_cost=None,
            fallback_model=None,
            log_json=False,
        )

        code = await repl_mod.run_repl(args)
        assert code == 0

        captured = capsys.readouterr()
        assert "/help" in captured.out

    @pytest.mark.asyncio
    async def test_template_list_command_works_in_run_repl(self, monkeypatch, capsys):
        from duh.cli import repl as repl_mod
        from duh import config as config_mod
        from duh.cli import prewarm as prewarm_mod
        from duh.kernel import templates as templates_mod

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        class _FakeProvider:
            def __init__(self, *args, **kwargs):
                pass

            async def stream(self, **kwargs):
                if False:  # pragma: no cover
                    yield {}

        async def _no_prewarm(_call_model):
            return None

        monkeypatch.setattr(repl_mod, "AnthropicProvider", _FakeProvider)
        monkeypatch.setattr(repl_mod, "get_all_tools", lambda: [])
        monkeypatch.setattr(repl_mod, "_load_history", lambda: None)
        monkeypatch.setattr(repl_mod, "_setup_completion", lambda: None)
        monkeypatch.setattr(repl_mod, "_save_history", lambda: None)
        monkeypatch.setattr(prewarm_mod, "prewarm_connection", _no_prewarm)
        monkeypatch.setattr(
            config_mod,
            "load_config",
            lambda cwd=".": SimpleNamespace(mcp_servers={}),
        )
        monkeypatch.setattr(templates_mod, "load_all_templates", lambda cwd=".": [])

        inputs = iter(["/template list", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        args = argparse.Namespace(
            debug=False,
            provider="anthropic",
            model="claude-sonnet-4-6",
            system_prompt=None,
            brief=False,
            approval_mode=None,
            dangerously_skip_permissions=False,
            max_turns=8,
            max_cost=None,
            fallback_model=None,
            log_json=False,
        )

        code = await repl_mod.run_repl(args)
        assert code == 0

        captured = capsys.readouterr()
        assert "No templates" in captured.out
