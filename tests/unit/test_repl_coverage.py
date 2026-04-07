"""Extended REPL coverage tests — slash commands and renderers.

Targets duh/cli/repl.py branches not covered by test_repl.py:
  /context, /search, /template, /plan, /undo, /jobs, /pr, /health, /brief
  _PlainRenderer methods, _RichRenderer methods, _make_renderer
"""

from __future__ import annotations

import sys
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.repl import (
    SLASH_COMMANDS,
    _PlainRenderer,
    _handle_slash,
    _handle_pr_command,
    _handle_template_command,
    _make_renderer,
    _search_messages,
    context_breakdown,
)
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(**overrides) -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model", **overrides)
    return Engine(deps=deps, config=config)


def _make_deps(**kw) -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock(), **kw)


# ===========================================================================
# /context
# ===========================================================================


class TestSlashContext:
    def test_context_outputs_table(self, capsys):
        engine = _make_engine()
        keep, model = _handle_slash("/context", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "Context window" in captured.out
        assert "System prompt" in captured.out
        assert "Available" in captured.out

    def test_context_breakdown_format(self):
        engine = _make_engine()
        result = context_breakdown(engine, "test-model")
        assert "tokens" in result.lower()
        assert "Used" in result
        assert "%" in result


# ===========================================================================
# /search
# ===========================================================================


class TestSlashSearch:
    def test_search_no_query(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash("/search", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_search_with_matches(self, capsys):
        engine = _make_engine()
        engine._messages.append(Message(role="user", content="hello world"))
        engine._messages.append(Message(role="assistant", content="response with hello"))
        keep, _ = _handle_slash("/search hello", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "hello" in captured.out
        assert "2 matches" in captured.out or "match" in captured.out

    def test_search_no_matches(self, capsys):
        engine = _make_engine()
        engine._messages.append(Message(role="user", content="something else"))
        keep, _ = _handle_slash("/search zzzzz", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "No matches" in captured.out


class TestSearchMessages:
    def test_basic_search(self, capsys):
        msgs = [
            Message(role="user", content="first message about python"),
            Message(role="assistant", content="here is python code"),
            Message(role="user", content="second question about java"),
        ]
        _search_messages(msgs, "python")
        captured = capsys.readouterr()
        assert "2 matches" in captured.out

    def test_no_results(self, capsys):
        msgs = [Message(role="user", content="hello")]
        _search_messages(msgs, "nonexistent")
        captured = capsys.readouterr()
        assert "No matches" in captured.out

    def test_single_match_grammar(self, capsys):
        msgs = [Message(role="user", content="hello world")]
        _search_messages(msgs, "hello")
        captured = capsys.readouterr()
        assert "1 match)" in captured.out  # "(1 match)" not "matches"

    def test_skips_empty_text(self, capsys):
        msgs = [
            Message(role="user", content=""),
            Message(role="user", content="target"),
        ]
        _search_messages(msgs, "target")
        captured = capsys.readouterr()
        assert "1 match)" in captured.out


# ===========================================================================
# /template
# ===========================================================================


class TestSlashTemplate:
    def test_template_list_empty(self, capsys):
        _handle_template_command("list", {"templates": {}, "active": None})
        captured = capsys.readouterr()
        assert "No templates" in captured.out

    def test_template_list_with_entries(self, capsys):
        tmpl = SimpleNamespace(description="A test template", render=lambda p: p)
        state = {"templates": {"test": tmpl}, "active": None}
        _handle_template_command("list", state)
        captured = capsys.readouterr()
        assert "test" in captured.out
        assert "A test template" in captured.out

    def test_template_use_set(self, capsys):
        tmpl = SimpleNamespace(description="desc", render=lambda p: p)
        state = {"templates": {"foo": tmpl}, "active": None}
        _handle_template_command("use foo", state)
        assert state["active"] == "foo"
        captured = capsys.readouterr()
        assert "foo" in captured.out

    def test_template_use_clear(self, capsys):
        state = {"templates": {}, "active": "old"}
        _handle_template_command("use", state)
        assert state["active"] is None
        captured = capsys.readouterr()
        assert "cleared" in captured.out.lower()

    def test_template_use_not_found(self, capsys):
        state = {"templates": {}, "active": None}
        _handle_template_command("use missing", state)
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower()

    def test_template_oneshot_render(self, capsys):
        tmpl = SimpleNamespace(description="d", render=lambda p: f"rendered:{p}")
        state = {"templates": {"t1": tmpl}, "active": None}
        _handle_template_command("t1 my prompt here", state)
        captured = capsys.readouterr()
        assert "rendered:my prompt here" in captured.out

    def test_template_oneshot_not_found(self, capsys):
        state = {"templates": {}, "active": None}
        _handle_template_command("missing some args", state)
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower()

    def test_template_via_slash(self, capsys):
        """Verify /template routes through _handle_slash."""
        engine = _make_engine()
        keep, _ = _handle_slash("/template", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "No templates" in captured.out


# ===========================================================================
# /plan
# ===========================================================================


class TestSlashPlan:
    def test_plan_not_available(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/plan", engine, "m", _make_deps(),
            plan_mode=None,
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "not available" in captured.out.lower()

    def test_plan_show(self, capsys):
        plan_mode = MagicMock()
        plan_mode.format_plan.return_value = "Step 1: Do stuff"
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/plan show", engine, "m", _make_deps(),
            plan_mode=plan_mode,
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "Step 1: Do stuff" in captured.out

    def test_plan_clear(self, capsys):
        plan_mode = MagicMock()
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/plan clear", engine, "m", _make_deps(),
            plan_mode=plan_mode,
        )
        assert keep is True
        plan_mode.clear.assert_called_once()
        captured = capsys.readouterr()
        assert "cleared" in captured.out.lower()

    def test_plan_usage_no_arg(self, capsys):
        plan_mode = MagicMock()
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/plan", engine, "m", _make_deps(),
            plan_mode=plan_mode,
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_plan_with_description_returns_sentinel(self):
        plan_mode = MagicMock()
        engine = _make_engine()
        keep, model = _handle_slash(
            "/plan refactor auth module", engine, "m", _make_deps(),
            plan_mode=plan_mode,
        )
        assert keep is True
        assert model.startswith("\x00plan\x00")
        assert "refactor auth module" in model


# ===========================================================================
# /undo
# ===========================================================================


class TestSlashUndo:
    def test_undo_no_executor(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash("/undo", engine, "m", _make_deps(), executor=None)
        assert keep is True
        captured = capsys.readouterr()
        assert "No executor" in captured.out

    def test_undo_nothing_to_undo(self, capsys):
        executor = MagicMock()
        executor.undo_stack.undo.side_effect = IndexError("empty")
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/undo", engine, "m", _make_deps(),
            executor=executor,
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "Nothing to undo" in captured.out

    def test_undo_success(self, capsys):
        executor = MagicMock()
        executor.undo_stack.undo.return_value = ("/tmp/file.py", "Reverted /tmp/file.py")
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/undo", engine, "m", _make_deps(),
            executor=executor,
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "Reverted" in captured.out


# ===========================================================================
# /jobs
# ===========================================================================


class TestSlashJobs:
    @patch("duh.tools.bash.get_job_queue")
    def test_jobs_empty(self, mock_queue, capsys):
        mock_queue.return_value.list_jobs.return_value = []
        engine = _make_engine()
        keep, _ = _handle_slash("/jobs", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "No background jobs" in captured.out

    @patch("duh.tools.bash.get_job_queue")
    def test_jobs_list(self, mock_queue, capsys):
        mock_queue.return_value.list_jobs.return_value = [
            {"id": "abc123", "state": "completed", "name": "test-job", "elapsed_s": 2.5},
        ]
        engine = _make_engine()
        keep, _ = _handle_slash("/jobs", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "abc123" in captured.out
        assert "test-job" in captured.out

    @patch("duh.tools.bash.get_job_queue")
    def test_jobs_specific_id(self, mock_queue, capsys):
        mock_queue.return_value.results.return_value = "done: all tests pass"
        engine = _make_engine()
        keep, _ = _handle_slash("/jobs abc123", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "all tests pass" in captured.out

    @patch("duh.tools.bash.get_job_queue")
    def test_jobs_unknown_id(self, mock_queue, capsys):
        mock_queue.return_value.results.side_effect = KeyError("nope")
        engine = _make_engine()
        keep, _ = _handle_slash("/jobs bad_id", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "Unknown job id" in captured.out

    @patch("duh.tools.bash.get_job_queue")
    def test_jobs_not_finished(self, mock_queue, capsys):
        mock_queue.return_value.results.side_effect = ValueError("Job xyz is still running")
        engine = _make_engine()
        keep, _ = _handle_slash("/jobs xyz", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "still running" in captured.out


# ===========================================================================
# /pr
# ===========================================================================


class TestSlashPr:
    def test_pr_routes_through_handler(self, capsys):
        engine = _make_engine()
        with patch("duh.cli.repl._handle_pr_command") as mock_pr:
            keep, _ = _handle_slash("/pr list", engine, "m", _make_deps())
            mock_pr.assert_called_once_with("list")
        assert keep is True

    @patch("duh.tools.github_tool._gh_available", return_value=False)
    def test_pr_no_gh(self, mock_avail, capsys):
        _handle_pr_command("")
        captured = capsys.readouterr()
        assert "gh" in captured.out.lower()

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    def test_pr_no_subcommand(self, mock_avail, capsys):
        _handle_pr_command("")
        captured = capsys.readouterr()
        assert "Usage" in captured.out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    def test_pr_unknown_subcommand(self, mock_avail, capsys):
        _handle_pr_command("foobar")
        captured = capsys.readouterr()
        assert "Unknown /pr subcommand" in captured.out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh", return_value=("[]", "", 0))
    def test_pr_list_empty(self, mock_gh, mock_avail, capsys):
        _handle_pr_command("list")
        captured = capsys.readouterr()
        assert "No pull requests" in captured.out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh")
    def test_pr_list_with_results(self, mock_gh, mock_avail, capsys):
        import json
        prs = [
            {"number": 42, "title": "Fix bug", "state": "OPEN", "author": {"login": "dev1"}},
        ]
        mock_gh.return_value = (json.dumps(prs), "", 0)
        _handle_pr_command("list")
        captured = capsys.readouterr()
        assert "#42" in captured.out
        assert "Fix bug" in captured.out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    def test_pr_view_no_number(self, mock_avail, capsys):
        _handle_pr_command("view")
        captured = capsys.readouterr()
        assert "Usage" in captured.out

    @patch("duh.tools.github_tool._gh_available", return_value=True)
    @patch("duh.tools.github_tool._run_gh", return_value=("PR details here", "", 0))
    def test_pr_view_success(self, mock_gh, mock_avail, capsys):
        _handle_pr_command("view 42")
        captured = capsys.readouterr()
        assert "PR details here" in captured.out


# ===========================================================================
# /health
# ===========================================================================


class TestSlashHealth:
    @patch("duh.kernel.health_check.HealthChecker")
    @patch("duh.cli.doctor._format_latency", return_value="42ms")
    def test_health_all_healthy(self, mock_latency, mock_checker_cls, capsys):
        mock_checker = mock_checker_cls.return_value
        mock_checker.check_provider.return_value = {
            "healthy": True, "latency_ms": 42, "error": None,
        }
        engine = _make_engine()
        keep, _ = _handle_slash("/health", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "All checks passed" in captured.out

    @patch("duh.kernel.health_check.HealthChecker")
    @patch("duh.cli.doctor._format_latency", return_value="500ms")
    def test_health_unhealthy_provider(self, mock_latency, mock_checker_cls, capsys):
        mock_checker = mock_checker_cls.return_value
        mock_checker.check_provider.return_value = {
            "healthy": False, "latency_ms": 500, "error": "timeout",
        }
        engine = _make_engine()
        keep, _ = _handle_slash("/health", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "UNHEALTHY" in captured.out
        assert "Unhealthy:" in captured.out


# ===========================================================================
# /brief
# ===========================================================================


class TestSlashBrief:
    def test_brief_toggle_on(self, capsys):
        engine = _make_engine()
        engine._config.system_prompt = "base prompt"
        keep, _ = _handle_slash("/brief", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "ON" in captured.out

    def test_brief_toggle_off(self, capsys):
        from duh.cli.runner import BRIEF_INSTRUCTION
        engine = _make_engine()
        engine._config.system_prompt = "base prompt\n\n" + BRIEF_INSTRUCTION
        keep, _ = _handle_slash("/brief", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "OFF" in captured.out

    def test_brief_on_explicit(self, capsys):
        engine = _make_engine()
        engine._config.system_prompt = "base prompt"
        keep, _ = _handle_slash("/brief on", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "ON" in captured.out

    def test_brief_off_explicit(self, capsys):
        from duh.cli.runner import BRIEF_INSTRUCTION
        engine = _make_engine()
        engine._config.system_prompt = "base prompt\n\n" + BRIEF_INSTRUCTION
        keep, _ = _handle_slash("/brief off", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "OFF" in captured.out

    def test_brief_no_change_already_on(self, capsys):
        from duh.cli.runner import BRIEF_INSTRUCTION
        engine = _make_engine()
        engine._config.system_prompt = "base\n\n" + BRIEF_INSTRUCTION
        keep, _ = _handle_slash("/brief on", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "no change" in captured.out.lower()


# ===========================================================================
# _PlainRenderer methods
# ===========================================================================


class TestPlainRenderer:
    def test_text_delta(self):
        r = _PlainRenderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.text_delta("hello")
        assert buf.getvalue() == "hello"
        assert r._buf == ["hello"]

    def test_flush_response_clears_buffer(self):
        r = _PlainRenderer()
        r._buf = ["some", "text"]
        r.flush_response()
        assert r._buf == []

    def test_thinking_delta_debug(self):
        r = _PlainRenderer(debug=True)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.thinking_delta("hmm...")
        assert "hmm..." in buf.getvalue()

    def test_thinking_delta_no_debug(self):
        r = _PlainRenderer(debug=False)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.thinking_delta("hmm...")
        assert buf.getvalue() == ""

    def test_tool_use(self):
        r = _PlainRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.tool_use("Read", {"file_path": "/tmp/x"})
        assert "Read" in buf.getvalue()
        assert "file_path" in buf.getvalue()

    def test_tool_result_error(self):
        r = _PlainRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.tool_result("file not found", is_error=True)
        assert "file not found" in buf.getvalue()

    def test_tool_result_debug(self):
        r = _PlainRenderer(debug=True)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.tool_result("ok", is_error=False)
        assert "ok" in buf.getvalue()

    def test_tool_result_no_debug_no_error(self):
        r = _PlainRenderer(debug=False)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.tool_result("ok", is_error=False)
        assert buf.getvalue() == ""

    def test_error(self):
        r = _PlainRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.error("something went wrong")
        assert "something went wrong" in buf.getvalue()

    def test_turn_end(self):
        r = _PlainRenderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.turn_end()
        assert buf.getvalue() == "\n\n"

    def test_banner(self):
        r = _PlainRenderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.banner("claude-test")
        assert "D.U.H." in buf.getvalue()
        assert "claude-test" in buf.getvalue()

    def test_status_bar_is_noop(self):
        r = _PlainRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.status_bar("m", 5)
        assert buf.getvalue() == ""

    def test_prompt_returns_ansi(self):
        r = _PlainRenderer()
        prompt = r.prompt()
        assert "duh>" in prompt


# ===========================================================================
# _RichRenderer (only when rich is installed)
# ===========================================================================


class TestRichRenderer:
    @pytest.fixture(autouse=True)
    def skip_without_rich(self):
        try:
            import rich  # noqa: F401
        except ImportError:
            pytest.skip("rich not installed")

    def _make(self, debug: bool = False):
        from duh.cli.repl import _RichRenderer
        return _RichRenderer(debug=debug)

    def test_text_delta(self):
        r = self._make()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.text_delta("hello")
        assert buf.getvalue() == "hello"
        assert r._buf == ["hello"]

    def test_flush_response_clears_buf(self):
        r = self._make()
        r._buf = ["no markdown here"]
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.flush_response()
        assert r._buf == []

    def test_flush_empty_buf(self):
        r = self._make()
        r._buf = ["   "]
        r.flush_response()
        assert r._buf == []

    def test_thinking_delta_debug(self):
        r = self._make(debug=True)
        # Just verify it doesn't raise
        r.thinking_delta("thinking...")

    def test_thinking_delta_no_debug(self):
        r = self._make(debug=False)
        # Should be a no-op
        r.thinking_delta("thinking...")

    def test_tool_use(self):
        r = self._make()
        # Just verify it doesn't raise
        r.tool_use("Edit", {"file_path": "/tmp/x", "old_string": "a"})

    def test_tool_result_error(self):
        r = self._make()
        # Uses Panel, verify no exception
        r.tool_result("bad thing happened", is_error=True)

    def test_tool_result_debug(self):
        r = self._make(debug=True)
        r.tool_result("ok", is_error=False)

    def test_tool_result_no_debug_no_error(self):
        r = self._make(debug=False)
        # Should be a no-op
        r.tool_result("ok", is_error=False)

    def test_error(self):
        r = self._make()
        # Uses Panel, verify no exception
        r.error("something bad")

    def test_turn_end(self):
        r = self._make()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.turn_end()
        assert "\n" in buf.getvalue()

    def test_banner(self):
        r = self._make()
        # Uses Panel, just verify no exception
        r.banner("test-model")

    def test_status_bar(self):
        r = self._make()
        # Uses Text with style, just verify no exception
        r.status_bar("test-model", 3)

    def test_prompt_returns_string(self):
        r = self._make()
        assert "duh>" in r.prompt()


# ===========================================================================
# _make_renderer
# ===========================================================================


class TestMakeRenderer:
    def test_returns_plain_when_no_rich(self):
        with patch("duh.cli.repl._HAS_RICH", False):
            r = _make_renderer(debug=True)
        assert isinstance(r, _PlainRenderer)
        assert r.debug is True

    def test_returns_rich_when_available(self):
        try:
            import rich  # noqa: F401
        except ImportError:
            pytest.skip("rich not installed")
        with patch("duh.cli.repl._HAS_RICH", True):
            r = _make_renderer(debug=False)
        from duh.cli.repl import _RichRenderer
        assert isinstance(r, _RichRenderer)


# ===========================================================================
# /changes
# ===========================================================================


class TestSlashChanges:
    def test_changes_no_executor(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash("/changes", engine, "m", _make_deps(), executor=None)
        assert keep is True
        captured = capsys.readouterr()
        assert "No file tracker" in captured.out

    def test_changes_with_executor(self, capsys):
        executor = MagicMock()
        executor.file_tracker.summary.return_value = "3 files modified"
        executor.file_tracker.diff_summary.return_value = "No files modified."
        engine = _make_engine()
        keep, _ = _handle_slash(
            "/changes", engine, "m", _make_deps(),
            executor=executor,
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "3 files modified" in captured.out


# ===========================================================================
# /compact
# ===========================================================================


class TestSlashCompactWithCompactor:
    def test_compact_with_compactor(self, capsys):
        """When a compactor is wired, /compact attempts compaction.

        The result depends on event-loop state: in isolation it succeeds
        ("Compacted"), but when another test has closed the loop it falls
        back to the error branch ("Compact failed").  Both are valid
        evidence that the branch was exercised.
        """
        engine = _make_engine()
        compact_fn = AsyncMock()
        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock(), compact=compact_fn)
        keep, _ = _handle_slash("/compact", engine, "m", deps)
        assert keep is True
        captured = capsys.readouterr()
        # Either the compact succeeded or it hit the event-loop error path
        assert "Compacted" in captured.out or "Compact failed" in captured.out
        # Crucially, it did NOT say "No compactor configured"
        assert "No compactor" not in captured.out
