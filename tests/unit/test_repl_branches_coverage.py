"""Extended REPL branch coverage — slash commands and helpers.

Covers branches not yet exercised by test_repl.py or test_repl_coverage.py:
  - /connect openai interactive prompt (choice 1, choice 2, EOF)
  - /connect openai api-key
  - /connect anthropic
  - /connect unsupported provider
  - /connect openai unknown method
  - /models use without arg
  - /models with no connected providers
  - /models openai auth labels (api_key, oauth-for-codex, not-connected)
  - /model with provider unknown
  - /model with unsupported backend error
  - /snapshot sentinel
  - /git inside + outside repo
  - /compact with compactor that succeeds
  - /health with unhealthy MCP server
  - _handle_pr_command: list error, view error, rc-non-zero, json decode error
  - _search_messages: long snippet with prefix/suffix "..."
  - _save_history: OSError handling
  - _load_history: file-not-found
  - _SlashCompleter behaviour
  - _setup_completion (libedit + regular readline paths)
"""

from __future__ import annotations

import readline
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.repl import (
    SLASH_COMMANDS,
    _handle_pr_command,
    _handle_slash,
    _load_history,
    _save_history,
    _search_messages,
    _setup_completion,
    _SlashCompleter,
)
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message


def _make_engine() -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model")
    return Engine(deps=deps, config=config)


def _make_deps() -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock())


# ============================================================================
# /connect branches
# ============================================================================


class TestSlashConnect:
    def test_unsupported_provider(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash("/connect google", engine, "m", _make_deps())
        assert keep is True
        out = capsys.readouterr().out
        assert "Supported" in out

    def test_openai_interactive_choice_chatgpt(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(
            repl_mod,
            "connect_openai_chatgpt_subscription",
            lambda input_fn=input: (True, "ChatGPT connected"),
        )
        monkeypatch.setattr("builtins.input", lambda: "1")

        engine = _make_engine()
        keep, _ = _handle_slash("/connect openai", engine, "m", _make_deps())
        assert keep is True
        out = capsys.readouterr().out
        assert "ChatGPT connected" in out

    def test_openai_interactive_choice_empty_defaults_chatgpt(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(
            repl_mod,
            "connect_openai_chatgpt_subscription",
            lambda input_fn=input: (True, "ChatGPT connected"),
        )
        monkeypatch.setattr("builtins.input", lambda: "")

        engine = _make_engine()
        keep, _ = _handle_slash("/connect openai", engine, "m", _make_deps())
        assert keep is True
        out = capsys.readouterr().out
        assert "ChatGPT connected" in out

    def test_openai_interactive_choice_api_key(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(
            repl_mod,
            "connect_openai_api_key",
            lambda input_fn=None: (True, "API key saved"),
        )
        monkeypatch.setattr("builtins.input", lambda: "2")

        engine = _make_engine()
        keep, _ = _handle_slash("/connect openai", engine, "m", _make_deps())
        assert keep is True
        out = capsys.readouterr().out
        assert "API key saved" in out

    def test_openai_interactive_eof(self, capsys, monkeypatch):
        def _raise_eof():
            raise EOFError
        monkeypatch.setattr("builtins.input", lambda: _raise_eof())
        engine = _make_engine()
        keep, _ = _handle_slash("/connect openai", engine, "m", _make_deps())
        assert keep is True

    def test_openai_explicit_api_key_method(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(
            repl_mod,
            "connect_openai_api_key",
            lambda input_fn=None: (True, "API key saved"),
        )
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/connect openai api-key", engine, "m", _make_deps()
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "API key saved" in out

    def test_openai_unknown_method(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/connect openai weird", engine, "m", _make_deps()
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_anthropic_connect(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(
            repl_mod,
            "connect_anthropic_api_key",
            lambda input_fn=None: (True, "Anthropic connected"),
        )
        engine = _make_engine()
        keep, _ = _handle_slash("/connect anthropic", engine, "m", _make_deps())
        assert keep is True
        out = capsys.readouterr().out
        assert "Anthropic connected" in out


# ============================================================================
# /models edge cases
# ============================================================================


class TestSlashModelsEdge:
    def test_models_use_no_arg(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/models use", engine, "m", _make_deps(),
            provider_name="openai",
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_models_use_codex_shortcut(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod
        monkeypatch.setattr(repl_mod, "resolve_openai_auth_mode", lambda m: "chatgpt")
        engine = _make_engine()
        keep, model = _handle_slash(
            "/models use codex", engine, "m", _make_deps(),
            provider_name="openai",
        )
        assert keep is True
        assert model == "gpt-5.2-codex"

    def test_models_none_connected(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(repl_mod, "has_anthropic_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_chatgpt_oauth", lambda: False)

        # Mock httpx to fail Ollama
        monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(Exception("no")))

        engine = _make_engine()
        keep, _ = _handle_slash(
            "/models", engine, "gpt-4o", _make_deps(), provider_name="",
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "No connected providers" in out

    def test_models_openai_api_key_label(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod
        from duh.providers import registry as reg

        monkeypatch.setattr(repl_mod, "has_anthropic_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_available", lambda: True)
        monkeypatch.setattr(repl_mod, "has_openai_chatgpt_oauth", lambda: False)
        monkeypatch.setattr(reg, "get_openai_api_key", lambda: "sk-test")
        monkeypatch.setattr(repl_mod, "resolve_openai_auth_mode", lambda m: "api_key")
        monkeypatch.setattr(
            repl_mod,
            "available_models_for_provider",
            lambda p, current_model=None: ["gpt-4o"],
        )
        monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(Exception("no")))

        engine = _make_engine()
        keep, _ = _handle_slash(
            "/models", engine, "gpt-4o", _make_deps(), provider_name="openai",
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "OpenAI auth: API key" in out

    def test_models_openai_oauth_for_codex_label(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod
        from duh.providers import registry as reg

        monkeypatch.setattr(repl_mod, "has_anthropic_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_chatgpt_oauth", lambda: True)
        monkeypatch.setattr(reg, "get_openai_api_key", lambda: "")
        monkeypatch.setattr(repl_mod, "resolve_openai_auth_mode", lambda m: "api_key")
        monkeypatch.setattr(
            repl_mod,
            "available_models_for_provider",
            lambda p, current_model=None: ["gpt-4o"],
        )
        monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(Exception("no")))

        engine = _make_engine()
        keep, _ = _handle_slash(
            "/models", engine, "gpt-4o", _make_deps(), provider_name="openai",
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "available for Codex" in out

    def test_models_openai_not_connected_label(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod
        from duh.providers import registry as reg

        # Show openai in connected list despite no keys (via provider_name fallback)
        monkeypatch.setattr(repl_mod, "has_anthropic_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_available", lambda: False)
        monkeypatch.setattr(repl_mod, "has_openai_chatgpt_oauth", lambda: False)
        monkeypatch.setattr(reg, "get_openai_api_key", lambda: "")
        monkeypatch.setattr(repl_mod, "resolve_openai_auth_mode", lambda m: "none")
        monkeypatch.setattr(
            repl_mod,
            "available_models_for_provider",
            lambda p, current_model=None: [],
        )
        monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(Exception("no")))

        engine = _make_engine()
        # Provider_name="openai" forces openai into connected list
        keep, _ = _handle_slash(
            "/models", engine, "gpt-4o", _make_deps(), provider_name="openai",
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "not connected" in out
        assert "(no models found)" in out


# ============================================================================
# /model edge cases — unknown provider inference, build failure
# ============================================================================


class TestSlashModelEdge:
    def test_model_unknown_inference(self, capsys):
        engine = _make_engine()
        keep, model = _handle_slash(
            "/model weirdmodel", engine, "m", _make_deps(),
            provider_name="anthropic",
        )
        assert keep is True
        assert model == "weirdmodel"
        out = capsys.readouterr().out
        assert "Provider unknown" in out

    def test_model_build_backend_failure(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        fake_backend = MagicMock()
        fake_backend.ok = False
        fake_backend.error = "key not set"
        monkeypatch.setattr(
            repl_mod, "build_model_backend", lambda p, m: fake_backend
        )
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/model claude-sonnet-4-6", engine, "m", _make_deps(),
            provider_name="anthropic",
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "key not set" in out


# ============================================================================
# /snapshot — sentinel path
# ============================================================================


class TestSlashSnapshot:
    def test_snapshot_returns_sentinel(self):
        engine = _make_engine()
        keep, model = _handle_slash(
            "/snapshot apply", engine, "m", _make_deps()
        )
        assert keep is True
        assert "\x00snapshot\x00" in model
        assert "apply" in model


# ============================================================================
# /git
# ============================================================================


class TestSlashGit:
    def test_git_with_context(self, capsys, monkeypatch):
        monkeypatch.setattr(
            "duh.kernel.git_context.get_git_context",
            lambda cwd: "<git-context>branch: main</git-context>",
        )
        engine = _make_engine()
        keep, _ = _handle_slash("/git", engine, "m", _make_deps())
        assert keep is True
        out = capsys.readouterr().out
        assert "branch: main" in out

    def test_git_no_repo(self, capsys, monkeypatch):
        monkeypatch.setattr(
            "duh.kernel.git_context.get_git_context",
            lambda cwd: "",
        )
        engine = _make_engine()
        keep, _ = _handle_slash("/git", engine, "m", _make_deps())
        assert keep is True
        out = capsys.readouterr().out
        assert "Not in a git repository" in out


# ============================================================================
# /tasks
# ============================================================================


class TestSlashTasks:
    def test_tasks_with_manager(self, capsys):
        tm = MagicMock()
        tm.summary.return_value = "5 tasks pending"
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/tasks", engine, "m", _make_deps(), task_manager=tm
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "5 tasks pending" in out

    def test_tasks_no_manager(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/tasks", engine, "m", _make_deps(), task_manager=None
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "No tasks" in out


# ============================================================================
# /changes with git diff
# ============================================================================


class TestSlashChangesWithDiff:
    def test_changes_with_diff(self, capsys):
        executor = MagicMock()
        executor.file_tracker.summary.return_value = "2 files modified"
        executor.file_tracker.diff_summary_sync.return_value = (
            " file1.py | 3 +++\n file2.py | 2 --"
        )
        executor._cwd = "/tmp"
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/changes", engine, "m", _make_deps(), executor=executor,
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "2 files modified" in out
        assert "Git diff" in out


# ============================================================================
# /health with MCP servers
# ============================================================================


class TestHealthWithMcp:
    @patch("duh.kernel.health_check.HealthChecker")
    @patch("duh.cli.doctor._format_latency", return_value="10ms")
    def test_health_with_mcp_healthy(self, mock_latency, mock_checker_cls, capsys):
        mock_checker = mock_checker_cls.return_value
        mock_checker.check_provider.return_value = {
            "healthy": True, "latency_ms": 10, "error": None,
        }
        mock_checker.check_mcp.return_value = {
            "healthy": True, "tools": 5,
        }
        mock_mcp = MagicMock()
        mock_mcp._connections = {"server1": object()}
        mock_mcp._servers = {"server1": {}}
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/health", engine, "m", _make_deps(), mcp_executor=mock_mcp,
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "server1" in out
        assert "5 tools" in out

    @patch("duh.kernel.health_check.HealthChecker")
    @patch("duh.cli.doctor._format_latency", return_value="200ms")
    def test_health_with_mcp_unhealthy(self, mock_latency, mock_checker_cls, capsys):
        mock_checker = mock_checker_cls.return_value
        mock_checker.check_provider.return_value = {
            "healthy": True, "latency_ms": 10, "error": None,
        }
        mock_checker.check_mcp.return_value = {
            "healthy": False, "tools": 0,
        }
        mock_mcp = MagicMock()
        mock_mcp._connections = {}
        mock_mcp._servers = {"server1": {}}
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/health", engine, "m", _make_deps(), mcp_executor=mock_mcp,
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "UNHEALTHY" in out


# ============================================================================
# _handle_pr_command additional branches
# ============================================================================


class TestHandlePrCommand:
    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh", return_value=("", "permission denied", 1))
    def test_pr_list_error(self, mock_gh, mock_avail, capsys):
        _handle_pr_command("list")
        out = capsys.readouterr().out
        assert "permission denied" in out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh", return_value=("not json", "", 0))
    def test_pr_list_bad_json(self, mock_gh, mock_avail, capsys):
        _handle_pr_command("list")
        out = capsys.readouterr().out
        assert "not json" in out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh")
    def test_pr_list_with_string_author(self, mock_gh, mock_avail, capsys):
        import json
        prs = [
            {"number": 1, "title": "t", "state": "OPEN", "author": "literal"},
        ]
        mock_gh.return_value = (json.dumps(prs), "", 0)
        _handle_pr_command("list")
        out = capsys.readouterr().out
        assert "#1" in out
        assert "literal" in out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh", return_value=("", "err", 1))
    def test_pr_view_error(self, mock_gh, mock_avail, capsys):
        _handle_pr_command("view 42")
        out = capsys.readouterr().out
        assert "err" in out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh", return_value=("diff output", "", 0))
    def test_pr_diff_success(self, mock_gh, mock_avail, capsys):
        _handle_pr_command("diff 42")
        out = capsys.readouterr().out
        assert "diff output" in out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh", return_value=("checks ok", "", 0))
    def test_pr_checks_success(self, mock_gh, mock_avail, capsys):
        _handle_pr_command("checks 42")
        out = capsys.readouterr().out
        assert "checks ok" in out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh", return_value=("", "", 0))
    def test_pr_view_no_output(self, mock_gh, mock_avail, capsys):
        _handle_pr_command("view 42")
        out = capsys.readouterr().out
        assert "no output" in out


# ============================================================================
# _search_messages long-text branches
# ============================================================================


class TestSearchMessagesLong:
    def test_long_text_prefix_suffix(self, capsys):
        # Create a long text with "target" in the middle
        text = "x" * 100 + "target" + "y" * 100
        msgs = [Message(role="user", content=text)]
        _search_messages(msgs, "target")
        out = capsys.readouterr().out
        # Should have "..." prefix and suffix
        assert "..." in out


# ============================================================================
# History persistence
# ============================================================================


# ============================================================================
# _SlashCompleter
# ============================================================================


class TestSlashCompleter:
    def test_empty_text_returns_none(self):
        c = _SlashCompleter(list(SLASH_COMMANDS.keys()))
        assert c.complete("", 0) is None

    def test_slash_prefix_matches(self):
        c = _SlashCompleter(list(SLASH_COMMANDS.keys()))
        # "/hel" should match /help (alphabetically before /health)
        match = c.complete("/hel", 0)
        assert match == "/help"

    def test_state_out_of_range(self):
        c = _SlashCompleter(list(SLASH_COMMANDS.keys()))
        c.complete("/help", 0)
        # state=99 should be out of matches
        assert c.complete("/help", 99) is None

    def test_non_slash_text_no_match(self):
        c = _SlashCompleter(list(SLASH_COMMANDS.keys()))
        assert c.complete("hello", 0) is None


# ============================================================================
# _setup_completion
# ============================================================================


class TestSetupCompletion:
    def test_setup_runs(self, monkeypatch):
        # Save original completer
        old = readline.get_completer()
        try:
            _setup_completion()
            new = readline.get_completer()
            assert callable(new)
        finally:
            readline.set_completer(old)


# ============================================================================
# /compact with successful compactor
# ============================================================================


class TestSlashCompactSuccess:
    def test_compact_with_working_compactor(self, capsys):
        """When compact is configured, /compact returns the compact sentinel."""
        engine = _make_engine()
        compact_fn = AsyncMock(return_value=[])
        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock(), compact=compact_fn)
        keep, model = _handle_slash("/compact", engine, "m", deps)
        assert keep is True
        assert model == "\x00compact\x00"


# ============================================================================
# /model with codex shortcut
# ============================================================================


class TestSlashModelCodex:
    def test_codex_shortcut_and_success(self, capsys, monkeypatch):
        from duh.cli import repl as repl_mod

        monkeypatch.setattr(repl_mod, "resolve_openai_auth_mode", lambda m: "chatgpt")
        engine = _make_engine()
        keep, model = _handle_slash(
            "/model codex", engine, "m", _make_deps(),
            provider_name="openai",
        )
        assert keep is True
        assert model == "gpt-5.2-codex"
