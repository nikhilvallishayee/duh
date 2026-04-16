"""Final push for repl.py coverage — covers the remaining run_repl branches.

Targets:
  - MCP servers path in run_repl (lines 1080-1091)
  - Template loading exception (1116-1117)
  - Task manager lookup (1122-1124)
  - Plan mode approve/reject/modify (1226-1296)
  - Active template applied to user input (1304)
  - QueryGuard failures (1328-1330, 1335-1336)
  - Engine run KeyboardInterrupt mid-query (1381-1385)
  - MCP disconnect failure at shutdown (1408-1411)
  - /connect openai successful backend switch (554-556)
  - /connect KeyboardInterrupt mid-choice
  - /models openai chatgpt subscription label (644)
  - _check_ollama success (1041)
  - /pr list with extra args (926)
  - /template use clear when nothing active (1001)
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli import repl as repl_mod
from duh.cli.repl import _handle_slash, _handle_pr_command, _handle_template_command
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig


# ----------------------------------------------------------------------------
# Helpers (duplicated to stay self-contained)
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


def _make_engine() -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model")
    return Engine(deps=deps, config=config)


def _make_deps() -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock())


class _FakeProvider:
    def __init__(self, *args, **kwargs):
        pass

    async def stream(self, **kwargs):
        if False:  # pragma: no cover
            yield {}


def _patch_repl_infra(monkeypatch):
    """Patch the heavy infra that run_repl needs."""
    from duh import config as config_mod
    from duh.cli import prewarm as prewarm_mod
    from duh.kernel import templates as templates_mod

    monkeypatch.setattr(repl_mod, "AnthropicProvider", _FakeProvider)
    monkeypatch.setattr(repl_mod, "get_all_tools", lambda **kw: [])
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


# ============================================================================
# /connect openai — successful backend switch via chatgpt
# ============================================================================


class TestSlashConnectSwitchSuccess:
    def test_model_command_successful_switch(self, capsys, monkeypatch):
        """When build_model_backend succeeds, _switch_backend_for_model
        populates deps.call_model (covers 554-556)."""
        from duh.cli import repl as repl_mod

        # fake backend success
        fake_backend = SimpleNamespace(
            ok=True,
            error="",
            call_model=lambda **k: None,
            model="claude-opus-4-6",
            provider="anthropic",
        )
        monkeypatch.setattr(
            repl_mod, "build_model_backend", lambda p, m: fake_backend
        )

        engine = _make_engine()
        deps = _make_deps()
        keep, model = _handle_slash(
            "/model claude-opus-4-6", engine, "m", deps,
            provider_name="anthropic",
        )
        assert keep is True
        assert model == "claude-opus-4-6"
        out = capsys.readouterr().out
        assert "Model changed to" in out
        assert "anthropic" in out


# ============================================================================
# /connect openai — KeyboardInterrupt during interactive choice
# ============================================================================


class TestConnectKeyboardInterrupt:
    def test_openai_keyboard_interrupt(self, capsys, monkeypatch):
        def _raise_ki():
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", lambda: _raise_ki())
        engine = _make_engine()
        keep, _ = _handle_slash("/connect openai", engine, "m", _make_deps())
        assert keep is True


# ============================================================================
# /models — ChatGPT subscription label (line 644)
# ============================================================================


class TestModelsChatGptLabel:
    def test_chatgpt_subscription_label(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod
        from duh.providers import registry as reg

        monkeypatch.setattr(repl_mod, "has_anthropic_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_chatgpt_oauth", lambda: True)
        monkeypatch.setattr(reg, "get_openai_api_key", lambda: "")
        monkeypatch.setattr(repl_mod, "resolve_openai_auth_mode", lambda m: "chatgpt")
        monkeypatch.setattr(
            repl_mod,
            "available_models_for_provider",
            lambda p, current_model=None: ["gpt-5.2-codex"],
        )
        monkeypatch.setattr(
            "httpx.get",
            lambda *a, **k: (_ for _ in ()).throw(Exception("no")),
        )

        engine = _make_engine()
        keep, _ = _handle_slash(
            "/models", engine, "gpt-5.2-codex", _make_deps(), provider_name="openai",
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "ChatGPT subscription" in out


# ============================================================================
# /pr list with extra arguments (line 926)
# ============================================================================


class TestPrListWithArgs:
    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh", return_value=("[]", "", 0))
    def test_pr_list_with_state_flag(self, mock_gh, mock_avail, capsys):
        _handle_pr_command("list --state open")
        # The extra args should be passed to _run_gh
        call_args = mock_gh.call_args[0][0]
        assert "--state" in call_args
        assert "open" in call_args


# ============================================================================
# /template use clear with nothing active (line 1001)
# ============================================================================


class TestTemplateUseClearNoActive:
    def test_use_clear_when_no_active(self, capsys):
        state = {"templates": {}, "active": None}
        _handle_template_command("use", state)
        out = capsys.readouterr().out
        assert "No active template" in out


# ============================================================================
# run_repl — _check_ollama success (line 1041)
# ============================================================================


class TestCheckOllama:
    @pytest.mark.asyncio
    async def test_ollama_detected_from_probe(self, monkeypatch):
        """When ollama is the resolved provider and probe succeeds, line 1041 fires."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        # Fake an httpx response with 200 for ollama probe
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        monkeypatch.setattr("httpx.get", lambda *a, **k: fake_resp)

        # Patch Ollama provider
        fake_ollama = MagicMock()
        fake_ollama.stream = MagicMock()
        monkeypatch.setattr(
            "duh.adapters.ollama.OllamaProvider", lambda model: fake_ollama
        )

        _patch_repl_infra(monkeypatch)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        args = _make_args(provider=None, model="qwen2.5-coder:1.5b")
        code = await repl_mod.run_repl(args)
        assert code == 0


# ============================================================================
# run_repl — MCP servers path (lines 1080-1091)
# ============================================================================


class TestReplMcpServers:
    @pytest.mark.asyncio
    async def test_mcp_servers_load(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        fake_tool_info = SimpleNamespace(
            name="mcp1",
            description="",
            server_name="s1",
            tool_name="t1",
            input_schema={"type": "object"},
        )

        mock_executor = MagicMock()
        mock_executor.connect_all = AsyncMock(return_value={"s1": [fake_tool_info]})
        mock_executor.disconnect_all = AsyncMock()

        from duh import config as config_mod
        from duh.cli import prewarm as prewarm_mod
        from duh.kernel import templates as templates_mod

        monkeypatch.setattr(repl_mod, "AnthropicProvider", _FakeProvider)
        monkeypatch.setattr(repl_mod, "get_all_tools", lambda **kw: [])
        monkeypatch.setattr(repl_mod, "_load_history", lambda: None)
        monkeypatch.setattr(repl_mod, "_setup_completion", lambda: None)
        monkeypatch.setattr(repl_mod, "_save_history", lambda: None)

        async def _no_prewarm(call_model):
            return None

        monkeypatch.setattr(prewarm_mod, "prewarm_connection", _no_prewarm)
        monkeypatch.setattr(
            config_mod,
            "load_config",
            lambda cwd=".": SimpleNamespace(
                mcp_servers={"mcpServers": {"s1": {"command": "foo"}}}
            ),
        )
        monkeypatch.setattr(templates_mod, "load_all_templates", lambda cwd=".": [])

        with patch(
            "duh.adapters.mcp_executor.MCPExecutor.from_config",
            return_value=mock_executor,
        ):
            with patch("duh.tools.mcp_tool.MCPToolWrapper") as MockWrapper:
                MockWrapper.return_value = SimpleNamespace(name="mcp1")
                inputs = iter(["/exit"])
                monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))
                code = await repl_mod.run_repl(_make_args(debug=True))
        assert code == 0

    @pytest.mark.asyncio
    async def test_mcp_disconnect_failure(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_executor = MagicMock()
        mock_executor.connect_all = AsyncMock(return_value={})
        mock_executor.disconnect_all = AsyncMock(side_effect=RuntimeError("bad"))

        from duh import config as config_mod
        from duh.cli import prewarm as prewarm_mod
        from duh.kernel import templates as templates_mod

        monkeypatch.setattr(repl_mod, "AnthropicProvider", _FakeProvider)
        monkeypatch.setattr(repl_mod, "get_all_tools", lambda **kw: [])
        monkeypatch.setattr(repl_mod, "_load_history", lambda: None)
        monkeypatch.setattr(repl_mod, "_setup_completion", lambda: None)
        monkeypatch.setattr(repl_mod, "_save_history", lambda: None)

        async def _no_prewarm(call_model):
            return None

        monkeypatch.setattr(prewarm_mod, "prewarm_connection", _no_prewarm)
        monkeypatch.setattr(
            config_mod,
            "load_config",
            lambda cwd=".": SimpleNamespace(
                mcp_servers={"mcpServers": {"s1": {"command": "foo"}}}
            ),
        )
        monkeypatch.setattr(templates_mod, "load_all_templates", lambda cwd=".": [])

        with patch(
            "duh.adapters.mcp_executor.MCPExecutor.from_config",
            return_value=mock_executor,
        ):
            inputs = iter(["/exit"])
            monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))
            code = await repl_mod.run_repl(_make_args())
        assert code == 0


# ============================================================================
# run_repl — template loading exception
# ============================================================================


class TestReplTemplateLoading:
    @pytest.mark.asyncio
    async def test_template_load_exception_swallowed(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        from duh import config as config_mod
        from duh.cli import prewarm as prewarm_mod
        from duh.kernel import templates as templates_mod

        monkeypatch.setattr(repl_mod, "AnthropicProvider", _FakeProvider)
        monkeypatch.setattr(repl_mod, "get_all_tools", lambda **kw: [])
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

        def bad_templates(cwd="."):
            raise RuntimeError("template load failed")

        monkeypatch.setattr(templates_mod, "load_all_templates", bad_templates)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0


# ============================================================================
# run_repl — Task manager lookup (1122-1124)
# ============================================================================


class TestTaskManagerLookup:
    @pytest.mark.asyncio
    async def test_task_tool_detected(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        # Provide a Task-named tool with a task_manager attribute
        fake_task_tool = SimpleNamespace(
            name="Task",
            task_manager=MagicMock(),
        )
        monkeypatch.setattr(repl_mod, "get_all_tools", lambda **kw: [fake_task_tool])

        _patch_repl_infra(monkeypatch)
        # Re-apply get_all_tools patch (overridden by _patch_repl_infra)
        monkeypatch.setattr(repl_mod, "get_all_tools", lambda **kw: [fake_task_tool])

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0


# ============================================================================
# run_repl — Active template applied
# ============================================================================


class TestActiveTemplateApplied:
    @pytest.mark.asyncio
    async def test_active_template_applied(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        from duh import config as config_mod
        from duh.cli import prewarm as prewarm_mod
        from duh.kernel import templates as templates_mod

        fake_template = SimpleNamespace(
            name="pref",
            description="prefix",
            render=lambda p: f"PREFIX: {p}",
        )

        monkeypatch.setattr(repl_mod, "AnthropicProvider", _FakeProvider)
        monkeypatch.setattr(repl_mod, "get_all_tools", lambda **kw: [])
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
        monkeypatch.setattr(
            templates_mod, "load_all_templates", lambda cwd=".": [fake_template]
        )

        captured_prompts = []

        async def run_override(self, prompt, **kw):
            captured_prompts.append(prompt)
            if False:  # pragma: no cover
                yield {}
            return

        original = repl_mod.Engine.run
        try:
            repl_mod.Engine.run = run_override

            inputs = iter(["/template use pref", "hello", "/exit"])
            monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

            code = await repl_mod.run_repl(_make_args())
        finally:
            repl_mod.Engine.run = original

        assert code == 0
        # Template should have transformed "hello" → "PREFIX: hello"
        assert captured_prompts == ["PREFIX: hello"]


# ============================================================================
# run_repl — QueryGuard already running
# ============================================================================


class TestQueryGuardBranches:
    @pytest.mark.asyncio
    async def test_query_guard_reserve_failure(self, monkeypatch, capsys):
        """Force QueryGuard.reserve to raise RuntimeError."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        # Force query_guard.reserve to raise
        original_reserve = repl_mod.QueryGuard.reserve

        def bad_reserve(self):
            raise RuntimeError("already in progress")

        monkeypatch.setattr(repl_mod.QueryGuard, "reserve", bad_reserve)

        inputs = iter(["hello", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0
        captured = capsys.readouterr()
        assert "already in progress" in captured.err or "already in progress" in captured.out

    @pytest.mark.asyncio
    async def test_query_guard_try_start_stale(self, monkeypatch, capsys):
        """Force QueryGuard.try_start to return None → 'stale' branch."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        def stale_try_start(self, gen):
            return None

        monkeypatch.setattr(repl_mod.QueryGuard, "try_start", stale_try_start)

        inputs = iter(["hello", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0
        captured = capsys.readouterr()
        assert "stale" in captured.err or "stale" in captured.out


# ============================================================================
# run_repl — Engine run KeyboardInterrupt mid-query
# ============================================================================


class TestEngineRunKeyboardInterrupt:
    @pytest.mark.asyncio
    async def test_ki_mid_query(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        async def ki_run(self, prompt, **kw):
            raise KeyboardInterrupt
            if False:  # pragma: no cover
                yield {}

        original = repl_mod.Engine.run
        try:
            repl_mod.Engine.run = ki_run
            inputs = iter(["go", "/exit"])
            monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))
            code = await repl_mod.run_repl(_make_args())
        finally:
            repl_mod.Engine.run = original

        assert code == 0
        captured = capsys.readouterr()
        assert "aborted" in captured.out


# ============================================================================
# run_repl — Plan mode with successful plan (covers 1226-1296)
# ============================================================================


class TestContextLimitZero:
    def test_pct_with_zero_context_limit(self, monkeypatch):
        """Cover line 459 — context_breakdown with context_limit == 0."""
        from duh.cli.repl import context_breakdown

        engine = _make_engine()
        # Force context_limit to 0
        monkeypatch.setattr(
            "duh.kernel.tokens.get_context_limit",
            lambda model: 0,
        )
        out = context_breakdown(engine, "unknown-model")
        assert "0.0%" in out


class TestCompactSuccess:
    """Exercise the /compact sentinel path."""

    def test_compact_returns_sentinel(self, capsys):
        engine = _make_engine()

        async def noop(messages):
            return messages

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock(), compact=noop)
        keep, model = _handle_slash("/compact", engine, "m", deps)
        assert keep is True
        assert model == "\x00compact\x00"


class TestReplMcpExceptionPath:
    @pytest.mark.asyncio
    async def test_mcp_executor_build_fails(self, monkeypatch, capsys):
        """Cover lines 1090-1091 — exception during MCP setup is swallowed."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        from duh import config as config_mod
        from duh.cli import prewarm as prewarm_mod
        from duh.kernel import templates as templates_mod

        monkeypatch.setattr(repl_mod, "AnthropicProvider", _FakeProvider)
        monkeypatch.setattr(repl_mod, "get_all_tools", lambda **kw: [])
        monkeypatch.setattr(repl_mod, "_load_history", lambda: None)
        monkeypatch.setattr(repl_mod, "_setup_completion", lambda: None)
        monkeypatch.setattr(repl_mod, "_save_history", lambda: None)

        async def _no_prewarm(call_model):
            return None

        monkeypatch.setattr(prewarm_mod, "prewarm_connection", _no_prewarm)
        monkeypatch.setattr(
            config_mod,
            "load_config",
            lambda cwd=".": SimpleNamespace(
                mcp_servers={"mcpServers": {"s1": {"command": "foo"}}}
            ),
        )
        monkeypatch.setattr(templates_mod, "load_all_templates", lambda cwd=".": [])

        with patch(
            "duh.adapters.mcp_executor.MCPExecutor.from_config",
            side_effect=RuntimeError("mcp broke"),
        ):
            inputs = iter(["/exit"])
            monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))
            code = await repl_mod.run_repl(_make_args())
        assert code == 0


class TestRichImportFallback:
    """Cover lines 91-92 — fallback when rich is not installed."""

    def test_reimport_with_rich_blocked(self):
        import builtins
        import importlib
        import sys as _sys

        real_import = builtins.__import__

        def blocker(name, *args, **kwargs):
            if name == "rich" or name.startswith("rich."):
                raise ImportError("simulated: no rich")
            return real_import(name, *args, **kwargs)

        # Temporarily remove rich from sys.modules so re-import runs
        saved = {k: v for k, v in _sys.modules.items() if k == "rich" or k.startswith("rich.")}
        for k in list(saved.keys()):
            del _sys.modules[k]

        # Also drop duh.cli.repl so a fresh import runs the top-level try/except
        saved_repl = _sys.modules.pop("duh.cli.repl", None)

        try:
            with patch("builtins.__import__", side_effect=blocker):
                import duh.cli.repl  # noqa: F401
            # After the fresh import with rich blocked, _HAS_RICH must be False
            reloaded = _sys.modules["duh.cli.repl"]
            assert reloaded._HAS_RICH is False
        finally:
            # Restore original modules so other tests still see rich
            _sys.modules.update(saved)
            if saved_repl is not None:
                _sys.modules["duh.cli.repl"] = saved_repl
            else:
                importlib.reload(_sys.modules["duh.cli.repl"])


class TestRichRendererMarkdownFlush:
    """Cover lines 199-203 — Rich renderer markdown flush when markdown
    indicators are present in the buffer."""

    def test_flush_with_markdown(self, capsys):
        try:
            import rich  # noqa: F401
        except ImportError:
            pytest.skip("rich not installed")

        from duh.cli.repl import _RichRenderer

        r = _RichRenderer()
        # Content must contain one of these indicators:
        #   "```", "##", "**", "* ", "- ", "1. ", "> ", "| "
        r._buf = ["## Heading\n", "**bold** text\n"]
        # Should execute the markdown-flush branch (lines 199-203)
        r.flush_response()
        assert r._buf == []


class TestPlanModeInRepl:
    @pytest.mark.asyncio
    async def test_plan_approved(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        # Configure plan mode
        async def fake_plan(self, desc):
            yield {"type": "text_delta", "text": "planning..."}

        async def fake_execute(self):
            yield {"type": "text_delta", "text": "done"}

        monkeypatch.setattr(repl_mod.PlanMode, "plan", fake_plan)
        monkeypatch.setattr(repl_mod.PlanMode, "execute", fake_execute)

        def steps_prop(self):
            return ["step 1", "step 2"]
        monkeypatch.setattr(
            repl_mod.PlanMode, "steps", property(steps_prop)
        )
        monkeypatch.setattr(
            repl_mod.PlanMode, "format_plan", lambda self: "1. step 1\n2. step 2"
        )
        monkeypatch.setattr(repl_mod.PlanMode, "clear", lambda self: None)

        inputs = iter(["/plan refactor", "a", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0
        out = capsys.readouterr().out
        assert "Planning:" in out
        assert "Executing plan" in out

    @pytest.mark.asyncio
    async def test_plan_rejected(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        async def fake_plan(self, desc):
            yield {"type": "text_delta", "text": "planning..."}

        monkeypatch.setattr(repl_mod.PlanMode, "plan", fake_plan)
        monkeypatch.setattr(
            repl_mod.PlanMode, "steps", property(lambda self: ["step 1"])
        )
        monkeypatch.setattr(
            repl_mod.PlanMode, "format_plan", lambda self: "1. step 1"
        )

        cleared = {"count": 0}

        def clear_sink(self):
            cleared["count"] += 1

        monkeypatch.setattr(repl_mod.PlanMode, "clear", clear_sink)

        inputs = iter(["/plan refactor", "r", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0
        out = capsys.readouterr().out
        assert "Plan rejected" in out

    @pytest.mark.asyncio
    async def test_plan_modify(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        async def fake_plan(self, desc):
            yield {"type": "text_delta", "text": "planning..."}

        monkeypatch.setattr(repl_mod.PlanMode, "plan", fake_plan)
        monkeypatch.setattr(
            repl_mod.PlanMode, "steps", property(lambda self: ["step 1"])
        )
        monkeypatch.setattr(
            repl_mod.PlanMode, "format_plan", lambda self: "1. step 1"
        )
        monkeypatch.setattr(repl_mod.PlanMode, "clear", lambda self: None)

        inputs = iter(["/plan refactor", "m", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0
        out = capsys.readouterr().out
        assert "Edit the plan" in out

    @pytest.mark.asyncio
    async def test_plan_empty_steps(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        async def fake_plan(self, desc):
            yield {"type": "error", "error": "boom"}

        monkeypatch.setattr(repl_mod.PlanMode, "plan", fake_plan)
        monkeypatch.setattr(
            repl_mod.PlanMode, "steps", property(lambda self: [])
        )
        monkeypatch.setattr(repl_mod.PlanMode, "clear", lambda self: None)

        inputs = iter(["/plan refactor", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args())
        assert code == 0
        out = capsys.readouterr().out
        assert "Could not parse" in out

    @pytest.mark.asyncio
    async def test_plan_execute_with_tool_events(self, monkeypatch, capsys):
        """Cover lines 1264-1280 (plan execute with tool_use, tool_result,
        thinking_delta, error events)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        async def fake_plan(self, desc):
            yield {"type": "text_delta", "text": "plan"}

        async def fake_execute(self):
            yield {"type": "text_delta", "text": "text"}
            yield {"type": "thinking_delta", "text": "thinking"}
            yield {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}}
            yield {"type": "tool_result", "output": "ok", "is_error": False}
            yield {"type": "error", "error": "oops"}

        monkeypatch.setattr(repl_mod.PlanMode, "plan", fake_plan)
        monkeypatch.setattr(repl_mod.PlanMode, "execute", fake_execute)
        monkeypatch.setattr(
            repl_mod.PlanMode, "steps", property(lambda self: ["step 1"])
        )
        monkeypatch.setattr(
            repl_mod.PlanMode, "format_plan", lambda self: "1. step 1"
        )
        monkeypatch.setattr(repl_mod.PlanMode, "clear", lambda self: None)

        inputs = iter(["/plan refactor", "a", "/exit"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        code = await repl_mod.run_repl(_make_args(debug=True))
        assert code == 0

    @pytest.mark.asyncio
    async def test_plan_choice_eof(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _patch_repl_infra(monkeypatch)

        async def fake_plan(self, desc):
            yield {"type": "text_delta", "text": "planning..."}

        monkeypatch.setattr(repl_mod.PlanMode, "plan", fake_plan)
        monkeypatch.setattr(
            repl_mod.PlanMode, "steps", property(lambda self: ["step 1"])
        )
        monkeypatch.setattr(
            repl_mod.PlanMode, "format_plan", lambda self: "1. step 1"
        )
        monkeypatch.setattr(repl_mod.PlanMode, "clear", lambda self: None)

        call_count = {"n": 0}

        def fake_input(_p=""):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "/plan refactor"
            if call_count["n"] == 2:
                raise EOFError
            return "/exit"

        monkeypatch.setattr("builtins.input", fake_input)
        code = await repl_mod.run_repl(_make_args())
        assert code == 0
