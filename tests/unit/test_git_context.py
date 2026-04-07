"""Tests for duh.kernel.git_context."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from duh.kernel.git_context import _detect_main_branch, _run_git, get_git_context


# ---------------------------------------------------------------------------
# _run_git helper
# ---------------------------------------------------------------------------

class TestRunGit:
    def test_success(self):
        with patch("duh.kernel.git_context.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="  hello  \n")
            result = _run_git(["status"], "/tmp")
        assert result == "hello"
        mock_run.assert_called_once_with(
            ["git", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd="/tmp",
        )

    def test_nonzero_returncode(self):
        with patch("duh.kernel.git_context.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = _run_git(["status"], "/tmp")
        assert result is None

    def test_git_not_installed(self):
        with patch("duh.kernel.git_context.subprocess.run", side_effect=FileNotFoundError):
            result = _run_git(["status"], "/tmp")
        assert result is None

    def test_timeout(self):
        with patch(
            "duh.kernel.git_context.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
        ):
            result = _run_git(["status"], "/tmp")
        assert result is None

    def test_os_error(self):
        with patch("duh.kernel.git_context.subprocess.run", side_effect=OSError("boom")):
            result = _run_git(["status"], "/tmp")
        assert result is None


# ---------------------------------------------------------------------------
# _detect_main_branch
# ---------------------------------------------------------------------------

class TestDetectMainBranch:
    def test_detects_from_remote_head(self):
        with patch("duh.kernel.git_context._run_git") as mock:
            mock.return_value = "origin/main"
            assert _detect_main_branch("/tmp") == "main"

    def test_detects_master_from_remote_head(self):
        with patch("duh.kernel.git_context._run_git") as mock:
            mock.return_value = "origin/master"
            assert _detect_main_branch("/tmp") == "master"

    def test_falls_back_to_local_branches_main(self):
        def side_effect(cmd, cwd):
            if "symbolic-ref" in cmd:
                return None
            if "branch" in cmd:
                return "  main\n  master"
            return None

        with patch("duh.kernel.git_context._run_git", side_effect=side_effect):
            assert _detect_main_branch("/tmp") == "main"

    def test_falls_back_to_local_branches_master_only(self):
        def side_effect(cmd, cwd):
            if "symbolic-ref" in cmd:
                return None
            if "branch" in cmd:
                return "  master"
            return None

        with patch("duh.kernel.git_context._run_git", side_effect=side_effect):
            assert _detect_main_branch("/tmp") == "master"

    def test_defaults_to_main(self):
        with patch("duh.kernel.git_context._run_git", return_value=None):
            assert _detect_main_branch("/tmp") == "main"


# ---------------------------------------------------------------------------
# get_git_context — inside a git repo
# ---------------------------------------------------------------------------

class TestGetGitContextInRepo:
    """Tests with a mocked git repo present."""

    def _mock_run_git(self, cmd, cwd):
        """Simulate a typical git repo."""
        joined = " ".join(cmd)
        if "is-inside-work-tree" in joined:
            return "true"
        if "branch --show-current" in joined:
            return "feature/cool-thing"
        if "symbolic-ref" in joined:
            return "origin/main"
        if "branch --list" in joined:
            return "  main"
        if "log --oneline -5" in joined:
            return "abc1234 First commit\ndef5678 Second commit"
        if "status --short" in joined:
            return " M src/foo.py\n?? new_file.txt"
        return None

    def test_returns_context_block(self):
        with patch("duh.kernel.git_context._run_git", side_effect=self._mock_run_git):
            ctx = get_git_context("/fake/repo")

        assert ctx is not None
        assert "<git-context>" in ctx
        assert "</git-context>" in ctx
        assert "feature/cool-thing" in ctx
        assert "main" in ctx
        assert "abc1234 First commit" in ctx
        assert "M src/foo.py" in ctx
        assert "new_file.txt" in ctx

    def test_contains_section_headers(self):
        with patch("duh.kernel.git_context._run_git", side_effect=self._mock_run_git):
            ctx = get_git_context("/fake/repo")

        assert "Current branch:" in ctx
        assert "Main branch:" in ctx
        assert "Recent commits:" in ctx
        assert "Working tree status:" in ctx

    def test_detached_head(self):
        def mock_run(cmd, cwd):
            joined = " ".join(cmd)
            if "is-inside-work-tree" in joined:
                return "true"
            if "branch --show-current" in joined:
                return ""  # detached HEAD returns empty
            if "log --oneline" in joined:
                return "abc1234 commit"
            if "status --short" in joined:
                return ""
            return None

        with patch("duh.kernel.git_context._run_git", side_effect=mock_run):
            ctx = get_git_context("/fake/repo")

        assert ctx is not None
        assert "(detached HEAD)" in ctx

    def test_clean_working_tree(self):
        def mock_run(cmd, cwd):
            joined = " ".join(cmd)
            if "is-inside-work-tree" in joined:
                return "true"
            if "branch --show-current" in joined:
                return "main"
            if "log --oneline" in joined:
                return "abc1234 commit"
            if "status --short" in joined:
                return ""  # clean
            return None

        with patch("duh.kernel.git_context._run_git", side_effect=mock_run):
            ctx = get_git_context("/fake/repo")

        assert ctx is not None
        assert "(clean)" in ctx

    def test_status_truncated_at_20_lines(self):
        status_lines = [f" M file{i}.py" for i in range(30)]

        def mock_run(cmd, cwd):
            joined = " ".join(cmd)
            if "is-inside-work-tree" in joined:
                return "true"
            if "branch --show-current" in joined:
                return "main"
            if "log --oneline" in joined:
                return "abc commit"
            if "status --short" in joined:
                return "\n".join(status_lines)
            return None

        with patch("duh.kernel.git_context._run_git", side_effect=mock_run):
            ctx = get_git_context("/fake/repo")

        assert ctx is not None
        assert "... and 10 more" in ctx
        # First 20 should be present
        assert "file0.py" in ctx
        assert "file19.py" in ctx
        # file20 should NOT be in the visible lines
        assert "file20.py" not in ctx

    def test_no_commits_yet(self):
        def mock_run(cmd, cwd):
            joined = " ".join(cmd)
            if "is-inside-work-tree" in joined:
                return "true"
            if "branch --show-current" in joined:
                return "main"
            if "log --oneline" in joined:
                return None  # no commits
            if "status --short" in joined:
                return "?? README.md"
            return None

        with patch("duh.kernel.git_context._run_git", side_effect=mock_run):
            ctx = get_git_context("/fake/repo")

        assert ctx is not None
        assert "(no commits yet)" in ctx


# ---------------------------------------------------------------------------
# get_git_context — outside a git repo
# ---------------------------------------------------------------------------

class TestGetGitContextNoRepo:
    def test_returns_none_when_not_in_repo(self):
        with patch("duh.kernel.git_context._run_git", return_value=None):
            assert get_git_context("/tmp/not-a-repo") is None

    def test_returns_none_when_git_says_false(self):
        def mock_run(cmd, cwd):
            if "is-inside-work-tree" in " ".join(cmd):
                return "false"
            return None

        with patch("duh.kernel.git_context._run_git", side_effect=mock_run):
            assert get_git_context("/tmp/not-a-repo") is None

    def test_returns_none_when_git_not_installed(self):
        with patch(
            "duh.kernel.git_context.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert get_git_context("/tmp") is None


# ---------------------------------------------------------------------------
# REPL /git command
# ---------------------------------------------------------------------------

class TestSlashGitCommand:
    def test_git_command_in_repo(self, capsys):
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from unittest.mock import AsyncMock

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        fake_ctx = (
            "<git-context>\n"
            "Current branch: main\n"
            "Main branch: main\n"
            "\n"
            "Recent commits:\nabc1234 commit\n"
            "\n"
            "Working tree status:\n(clean)\n"
            "</git-context>"
        )
        with patch("duh.kernel.git_context.get_git_context", return_value=fake_ctx):
            keep, model = _handle_slash("/git", engine, "test-model", deps)

        assert keep is True
        captured = capsys.readouterr()
        assert "Current branch: main" in captured.out
        assert "abc1234 commit" in captured.out
        # XML tags should be stripped
        assert "<git-context>" not in captured.out

    def test_git_command_not_in_repo(self, capsys):
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from unittest.mock import AsyncMock

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        with patch("duh.kernel.git_context.get_git_context", return_value=None):
            keep, model = _handle_slash("/git", engine, "test-model", deps)

        assert keep is True
        captured = capsys.readouterr()
        assert "Not in a git repository" in captured.out


# ---------------------------------------------------------------------------
# REPL /changes with git diff
# ---------------------------------------------------------------------------

class TestSlashChangesWithGitDiff:
    def test_changes_includes_diff_stat(self, capsys):
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.file_tracker import FileTracker
        from unittest.mock import AsyncMock

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        executor = MagicMock(spec=NativeExecutor)
        tracker = FileTracker()
        tracker.track("/foo/bar.py", "edit")
        executor.file_tracker = tracker
        executor._cwd = "/fake"

        with patch.object(tracker, "diff_summary", return_value=" bar.py | 3 ++-"):
            keep, _ = _handle_slash("/changes", engine, "m", deps, executor=executor)

        assert keep is True
        captured = capsys.readouterr()
        assert "Edits" in captured.out
        assert "Git diff" in captured.out
        assert "bar.py | 3 ++-" in captured.out
