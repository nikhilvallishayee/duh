"""Tests for get_git_warnings() and git status metadata in tools."""

from __future__ import annotations

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from duh.kernel.git_context import get_git_warnings
from duh.kernel.tool import ToolContext


# ---------------------------------------------------------------------------
# Helper: build a mock _run_git dispatcher
# ---------------------------------------------------------------------------

def _make_mock_run(
    *,
    inside: str | None = "true",
    branch: str | None = "feature/xyz",
    status: str | None = "",
    main_branch_remote: str | None = "origin/main",
    main_branch_local: str | None = "  main",
):
    """Return a function that mimics _run_git for common cases."""

    def mock(cmd, cwd):
        joined = " ".join(cmd)
        if "is-inside-work-tree" in joined:
            return inside
        if "branch --show-current" in joined:
            return branch
        if "status --short" in joined:
            return status
        if "symbolic-ref" in joined:
            return main_branch_remote
        if "branch --list" in joined:
            return main_branch_local
        return None

    return mock


# ---------------------------------------------------------------------------
# get_git_warnings
# ---------------------------------------------------------------------------

class TestGetGitWarningsDetachedHead:
    """Detached HEAD detection."""

    def test_detached_head_empty_branch(self):
        mock = _make_mock_run(branch="")
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert any("Detached HEAD" in w for w in warnings)

    def test_detached_head_none_branch(self):
        mock = _make_mock_run(branch=None)
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert any("Detached HEAD" in w for w in warnings)

    def test_no_detached_head_on_normal_branch(self):
        mock = _make_mock_run(branch="feature/xyz")
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert not any("Detached HEAD" in w for w in warnings)


class TestGetGitWarningsDirtyTree:
    """Dirty working tree detection."""

    def test_dirty_when_status_has_output(self):
        mock = _make_mock_run(status=" M src/foo.py\n?? bar.txt")
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert any("Uncommitted changes" in w for w in warnings)

    def test_clean_when_status_empty(self):
        mock = _make_mock_run(status="")
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert not any("Uncommitted changes" in w for w in warnings)

    def test_clean_when_status_none(self):
        mock = _make_mock_run(status=None)
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert not any("Uncommitted changes" in w for w in warnings)


class TestGetGitWarningsMainBranch:
    """On main/master branch detection."""

    def test_warning_on_main(self):
        mock = _make_mock_run(branch="main", main_branch_remote="origin/main")
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert any("main branch" in w for w in warnings)

    def test_warning_on_master(self):
        mock = _make_mock_run(
            branch="master",
            main_branch_remote="origin/master",
        )
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert any("master branch" in w for w in warnings)

    def test_no_warning_on_feature_branch(self):
        mock = _make_mock_run(branch="feature/cool")
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert not any("branch" in w.lower() and "consider" in w.lower() for w in warnings)


class TestGetGitWarningsCleanState:
    """No warnings when everything is clean and on a feature branch."""

    def test_no_warnings_on_clean_feature(self):
        mock = _make_mock_run(branch="feature/work", status="")
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert warnings == []


class TestGetGitWarningsNotInRepo:
    """No warnings and no crash when not in a git repo."""

    def test_not_in_repo_returns_empty(self):
        with patch("duh.kernel.git_context._run_git", return_value=None):
            warnings = get_git_warnings("/tmp/not-a-repo")
        assert warnings == []

    def test_git_says_false(self):
        def mock(cmd, cwd):
            if "is-inside-work-tree" in " ".join(cmd):
                return "false"
            return None

        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/tmp")
        assert warnings == []

    def test_git_not_installed(self):
        with patch(
            "duh.kernel.git_context.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            warnings = get_git_warnings("/tmp")
        assert warnings == []


class TestGetGitWarningsMultiple:
    """Multiple warnings at once (detached + dirty)."""

    def test_detached_and_dirty(self):
        mock = _make_mock_run(branch="", status=" M foo.py")
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert len(warnings) == 2
        assert any("Detached HEAD" in w for w in warnings)
        assert any("Uncommitted changes" in w for w in warnings)

    def test_main_and_dirty(self):
        mock = _make_mock_run(branch="main", status=" M foo.py")
        with patch("duh.kernel.git_context._run_git", side_effect=mock):
            warnings = get_git_warnings("/fake")
        assert len(warnings) == 2
        assert any("Uncommitted changes" in w for w in warnings)
        assert any("main branch" in w for w in warnings)


# ---------------------------------------------------------------------------
# Tool metadata: git_dirty flag in WriteTool / EditTool results
# ---------------------------------------------------------------------------

class TestWriteToolGitDirty:
    @pytest.mark.asyncio
    async def test_write_includes_git_dirty_true(self, tmp_path):
        from duh.tools.write import WriteTool

        target = tmp_path / "out.txt"
        ctx = ToolContext(cwd=str(tmp_path))

        with patch("duh.tools.write._run_git_async", new_callable=AsyncMock, return_value=" M out.txt"):
            result = await WriteTool().call(
                {"file_path": str(target), "content": "hello"},
                ctx,
            )
        assert result.metadata["git_dirty"] is True

    @pytest.mark.asyncio
    async def test_write_includes_git_dirty_false(self, tmp_path):
        from duh.tools.write import WriteTool

        target = tmp_path / "out.txt"
        ctx = ToolContext(cwd=str(tmp_path))

        with patch("duh.tools.write._run_git_async", new_callable=AsyncMock, return_value=None):
            result = await WriteTool().call(
                {"file_path": str(target), "content": "hello"},
                ctx,
            )
        assert result.metadata["git_dirty"] is False


class TestEditToolGitDirty:
    @pytest.mark.asyncio
    async def test_edit_includes_git_dirty_true(self, tmp_path):
        from duh.tools.edit import EditTool

        target = tmp_path / "test.txt"
        target.write_text("old text here", encoding="utf-8")
        ctx = ToolContext(cwd=str(tmp_path))

        with patch("duh.tools.edit._run_git_async", new_callable=AsyncMock, return_value=" M test.txt"):
            result = await EditTool().call(
                {
                    "file_path": str(target),
                    "old_string": "old text",
                    "new_string": "new text",
                },
                ctx,
            )
        assert not result.is_error
        assert result.metadata["git_dirty"] is True

    @pytest.mark.asyncio
    async def test_edit_includes_git_dirty_false(self, tmp_path):
        from duh.tools.edit import EditTool

        target = tmp_path / "test.txt"
        target.write_text("old text here", encoding="utf-8")
        ctx = ToolContext(cwd=str(tmp_path))

        with patch("duh.tools.edit._run_git_async", new_callable=AsyncMock, return_value=None):
            result = await EditTool().call(
                {
                    "file_path": str(target),
                    "old_string": "old text",
                    "new_string": "new text",
                },
                ctx,
            )
        assert not result.is_error
        assert result.metadata["git_dirty"] is False
