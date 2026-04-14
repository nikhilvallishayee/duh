"""Integration tests driving run_repl end-to-end to cover loop branches.

Uses the DUH_STUB_PROVIDER env var + mocked input() to drive the REPL
loop through its many code paths without connecting to real providers.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli import repl as repl_mod


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _make_args(**kw) -> argparse.Namespace:
    defaults = dict(
        debug=False,
        provider="anthropic",
        model="claude-sonnet-4-6",
        system_prompt=None,
        brief=False,
        approval_mode=None,
        dangerously_skip_permissions=True,
        max_turns=8,
        max_cost=None,
        fallback_model=None,
        log_json=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class _FakeProvider:
    """Stub provider — yields nothing."""

    def __init__(self, *args, **kwargs):
        pass

    async def stream(self, **kwargs):
        if False:  # pragma: no cover
            yield {}


def _patch_repl_infra(monkeypatch, events=None):
    """Patch the heavy infra that run_repl pulls in."""
    from duh import config as config_mod
    from duh.cli import prewarm as prewarm_mod
    from duh.kernel import templates as templates_mod

    monkeypatch.setattr(repl_mod, "AnthropicProvider", _FakeProvider)
    monkeypatch.setattr(repl_mod, "get_all_tools", lambda: [])
    monkeypatch.setattr(repl_mod, "_load_history", lambda: None)
    monkeypatch.setattr(repl_mod, "_setup_completion", lambda: None)
    monkeypatch.setattr(repl_mod, "_save_history", lambda: None)

    async def _no_prewarm(call_model):
        return None

    monkeypatch.setattr(prewarm_mod, "prewarm_connection", _no_prewarm)
    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda cwd=".": SimpleNamespace(mcp_servers={}),
    )
    monkeypatch.setattr(templates_mod, "load_all_templates", lambda cwd=".": [])


# ----------------------------------------------------------------------------
# Provider errors
# ----------------------------------------------------------------------------


class TestRunReplProviderErrors:
    @pytest.mark.asyncio
    async def test_no_provider_available(self, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
        monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(Exception("no")))

        args = _make_args(provider=None, model=None)
        code = await repl_mod.run_repl(args)
        assert code == 1
        captured = capsys.readouterr()
        assert "No provider available" in captured.err

    @pytest.mark.asyncio
    async def test_backend_build_failure(self, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
        monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(Exception("no")))

        fake_bad = SimpleNamespace(ok=False, error="no key", model=None, call_model=None)
        monkeypatch.setattr(repl_mod, "build_model_backend", lambda p, m: fake_bad)

        args = _make_args(provider="anthropic", model=None)
        code = await repl_mod.run_repl(args)
        assert code == 1
        captured = capsys.readouterr()
        assert "no key" in captured.err


# ----------------------------------------------------------------------------
# Basic loop: slash commands, empty input, blank line, EOF
# ----------------------------------------------------------------------------


class TestRunReplLoop:
    @pytest.mark.asyncio
    async def test_empty_line_skipped_then_exit(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0

    @pytest.mark.asyncio
    async def test_bare_slash_shortcut(self, monkeypatch, capsys):
        """'status' (no leading /) should be translated to '/status'."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["status", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0
        captured = capsys.readouterr()
        assert "Session" in captured.out

    @pytest.mark.asyncio
    async def test_exit_slash_breaks(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0

    @pytest.mark.asyncio
    async def test_eof_at_prompt(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        def _eof(_prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        code = await repl_mod.run_repl(_make_args())
        assert code == 0


# ----------------------------------------------------------------------------
# Brief mode + approval mode + structured logger
# ----------------------------------------------------------------------------


class TestRunReplConfig:
    @pytest.mark.asyncio
    async def test_brief_mode(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        args = _make_args(brief=True)
        code = await repl_mod.run_repl(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_approval_mode_suggest(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        args = _make_args(
            approval_mode="suggest", dangerously_skip_permissions=False
        )
        code = await repl_mod.run_repl(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_interactive_approver(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        args = _make_args(dangerously_skip_permissions=False)
        code = await repl_mod.run_repl(args)
        assert code == 0

    @pytest.mark.asyncio
    async def test_structured_logger_enabled(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        mock_logger = MagicMock()
        with patch(
            "duh.adapters.structured_logging.StructuredLogger",
            return_value=mock_logger,
        ):
            args = _make_args(log_json=True)
            code = await repl_mod.run_repl(args)
        assert code == 0
        mock_logger.session_end.assert_called_once()

    @pytest.mark.asyncio
    async def test_max_cost_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("DUH_MAX_COST", "1.5")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0

    @pytest.mark.asyncio
    async def test_max_cost_invalid_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("DUH_MAX_COST", "not-a-number")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0

    @pytest.mark.asyncio
    async def test_debug_mode(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        args = _make_args(debug=True)
        code = await repl_mod.run_repl(args)
        assert code == 0


# ----------------------------------------------------------------------------
# Engine run: text_delta, errors, prompt → response path
# ----------------------------------------------------------------------------


class TestRunReplEnginePath:
    @pytest.mark.asyncio
    async def test_normal_prompt_to_text_delta(self, monkeypatch, capsys):
        """A regular prompt should stream text_delta events through the loop."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        events = [
            {"type": "text_delta", "text": "hello"},
            {"type": "text_delta", "text": " world"},
        ]

        async def fake_run(prompt, **kw):
            for e in events:
                yield e

        def patched_engine_init(cls_orig):
            original_run = repl_mod.Engine.run

            def run_override(self, prompt, **kw):
                return fake_run(prompt, **kw)

            repl_mod.Engine.run = run_override
            return original_run

        original = repl_mod.Engine.run
        try:
            async def run_override(self, prompt, **kw):
                for e in events:
                    yield e

            repl_mod.Engine.run = run_override

            inputs = iter(["hello there", "/exit"])
            monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

            code = await repl_mod.run_repl(_make_args())
        finally:
            repl_mod.Engine.run = original

        assert code == 0

    @pytest.mark.asyncio
    async def test_prompt_yields_tool_events(self, monkeypatch, capsys):
        """tool_use and tool_result events should flow through the renderer."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        events = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
            {"type": "tool_result", "output": "ok", "is_error": False},
            {"type": "thinking_delta", "text": "hmm"},
            {"type": "error", "error": "something broke"},
            {"type": "budget_warning", "message": "75% of budget"},
            {"type": "budget_exceeded", "message": "OUT of budget"},
        ]

        async def run_override(self, prompt, **kw):
            for e in events:
                yield e

        original = repl_mod.Engine.run
        try:
            repl_mod.Engine.run = run_override
            inputs = iter(["do stuff", "/exit"])
            monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

            code = await repl_mod.run_repl(_make_args(debug=True))
        finally:
            repl_mod.Engine.run = original
        assert code == 0

    @pytest.mark.asyncio
    async def test_prompt_yields_assistant_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        from duh.kernel.messages import Message
        err_msg = Message(
            role="assistant",
            content="rate_limit exceeded",
            metadata={"is_error": True},
        )
        events = [{"type": "assistant", "message": err_msg}]

        async def run_override(self, prompt, **kw):
            for e in events:
                yield e

        original = repl_mod.Engine.run
        try:
            repl_mod.Engine.run = run_override
            inputs = iter(["go", "/exit"])
            monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))
            code = await repl_mod.run_repl(_make_args())
        finally:
            repl_mod.Engine.run = original
        assert code == 0
