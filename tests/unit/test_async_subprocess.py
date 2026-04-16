"""Tests for async subprocess helpers (PERF-3/4).

Covers:
- _run_git_async in git_context.py: same semantics as sync _run_git
- Batched async diff_summary in FileTracker
- Timeout handling in the async git helper
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.kernel.file_tracker import FileTracker
from duh.kernel.git_context import _run_git, _run_git_async


# ---------------------------------------------------------------------------
# _run_git_async: equivalence with _run_git
# ---------------------------------------------------------------------------


class TestRunGitAsyncEquivalence:
    """Verify _run_git_async returns the same results as the sync _run_git."""

    async def test_success_returns_stripped_stdout(self):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"  main  \n", b""))

        with patch("duh.kernel.git_context.asyncio.create_subprocess_exec", return_value=proc):
            result = await _run_git_async(["branch", "--show-current"], "/tmp")
        assert result == "main"

    async def test_nonzero_returncode_returns_none(self):
        proc = AsyncMock()
        proc.returncode = 128
        proc.communicate = AsyncMock(return_value=(b"", b"fatal: not a git repo"))

        with patch("duh.kernel.git_context.asyncio.create_subprocess_exec", return_value=proc):
            result = await _run_git_async(["status"], "/tmp")
        assert result is None

    async def test_file_not_found_returns_none(self):
        with patch(
            "duh.kernel.git_context.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            result = await _run_git_async(["status"], "/tmp")
        assert result is None

    async def test_os_error_returns_none(self):
        with patch(
            "duh.kernel.git_context.asyncio.create_subprocess_exec",
            side_effect=OSError("mock os error"),
        ):
            result = await _run_git_async(["status"], "/tmp")
        assert result is None

    async def test_timeout_returns_none(self):
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.kill = MagicMock()

        with patch("duh.kernel.git_context.asyncio.create_subprocess_exec", return_value=proc):
            with patch(
                "duh.kernel.git_context.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                result = await _run_git_async(["status"], "/tmp", timeout=0.01)
        assert result is None

    async def test_timeout_kills_process(self):
        proc = AsyncMock()
        proc.kill = MagicMock()

        async def slow_communicate():
            raise asyncio.TimeoutError

        with patch("duh.kernel.git_context.asyncio.create_subprocess_exec", return_value=proc):
            with patch(
                "duh.kernel.git_context.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                await _run_git_async(["status"], "/tmp", timeout=0.01)
        proc.kill.assert_called_once()

    async def test_timeout_kill_process_lookup_error(self):
        """If the process is already dead when we try to kill it, don't crash."""
        proc = AsyncMock()
        proc.kill = MagicMock(side_effect=ProcessLookupError)

        with patch("duh.kernel.git_context.asyncio.create_subprocess_exec", return_value=proc):
            with patch(
                "duh.kernel.git_context.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                result = await _run_git_async(["status"], "/tmp", timeout=0.01)
        assert result is None

    async def test_empty_stdout_returns_empty_string(self):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("duh.kernel.git_context.asyncio.create_subprocess_exec", return_value=proc):
            result = await _run_git_async(["status", "--short"], "/tmp")
        assert result == ""

    async def test_matches_sync_for_success(self):
        """Confirm async helper produces same output as sync for a successful call."""
        stdout = b"abc123 Fix bug\ndef456 Add feature\n"
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(stdout, b""))

        with patch("duh.kernel.git_context.asyncio.create_subprocess_exec", return_value=proc):
            async_result = await _run_git_async(["log", "--oneline", "-2"], "/tmp")

        import subprocess
        fake = subprocess.CompletedProcess(
            args=["git", "log", "--oneline", "-2"],
            returncode=0,
            stdout=stdout.decode(),
        )
        with patch("duh.kernel.git_context.subprocess.run", return_value=fake):
            sync_result = _run_git(["log", "--oneline", "-2"], "/tmp")

        assert async_result == sync_result


# ---------------------------------------------------------------------------
# Batched diff_summary: multiple files in one call
# ---------------------------------------------------------------------------


def _make_proc(stdout: str = "", returncode: int = 0):
    """Create a mock async subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode("utf-8"), b"")
    )
    proc.kill = MagicMock()
    return proc


class TestBatchedDiffSummary:
    """Verify diff_summary batches all files into a single git diff call."""

    async def test_single_file(self):
        ft = FileTracker()
        ft.track("/a.py", "write")

        proc = _make_proc(" /a.py | 3 +++\n")
        with patch("duh.kernel.file_tracker.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await ft.diff_summary(cwd="/repo")
            mock_exec.assert_called_once_with(
                "git", "diff", "--stat", "--", "/a.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/repo",
            )
        assert "/a.py" in result

    async def test_multiple_files_single_call(self):
        ft = FileTracker()
        ft.track("/a.py", "write")
        ft.track("/b.py", "edit")
        ft.track("/c.py", "write")

        proc = _make_proc(
            " /a.py | 3 +++\n /b.py | 1 +\n /c.py | 2 ++\n"
        )
        with patch("duh.kernel.file_tracker.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await ft.diff_summary(cwd="/repo")
            # Only ONE subprocess call for all 3 files
            mock_exec.assert_called_once()
            call_args = mock_exec.call_args
            assert call_args[0] == ("git", "diff", "--stat", "--", "/a.py", "/b.py", "/c.py")

        assert "/a.py" in result
        assert "/b.py" in result
        assert "/c.py" in result

    async def test_twenty_files_still_single_call(self):
        """Regression: even 20 files should produce 1 subprocess, not 20."""
        ft = FileTracker()
        for i in range(20):
            ft.track(f"/file{i}.py", "edit")

        proc = _make_proc("lots of output")
        with patch("duh.kernel.file_tracker.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await ft.diff_summary()
            mock_exec.assert_called_once()

    async def test_deduplication(self):
        ft = FileTracker()
        ft.track("/a.py", "write")
        ft.track("/a.py", "edit")
        ft.track("/a.py", "write")

        proc = _make_proc(" /a.py | 1 +\n")
        with patch("duh.kernel.file_tracker.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await ft.diff_summary()
            call_args = mock_exec.call_args[0]
            # /a.py should appear only once, not three times
            assert call_args.count("/a.py") == 1

    async def test_all_untracked(self):
        ft = FileTracker()
        ft.track("/new1.py", "write")
        ft.track("/new2.py", "write")

        proc = _make_proc("")  # empty output = all new/untracked
        with patch("duh.kernel.file_tracker.asyncio.create_subprocess_exec", return_value=proc):
            result = await ft.diff_summary()
        assert "new/untracked" in result
        assert "/new1.py" in result
        assert "/new2.py" in result


# ---------------------------------------------------------------------------
# Timeout handling in diff_summary
# ---------------------------------------------------------------------------


class TestDiffSummaryTimeout:
    async def test_timeout_returns_timed_out_message(self):
        ft = FileTracker()
        ft.track("/a.py", "write")
        ft.track("/b.py", "edit")

        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.kill = MagicMock()

        with patch("duh.kernel.file_tracker.asyncio.create_subprocess_exec", return_value=proc):
            with patch(
                "duh.kernel.file_tracker.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                result = await ft.diff_summary()

        assert "git timed out" in result
        assert "/a.py" in result
        assert "/b.py" in result

    async def test_timeout_kills_process(self):
        ft = FileTracker()
        ft.track("/a.py", "write")

        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.kill = MagicMock()

        with patch("duh.kernel.file_tracker.asyncio.create_subprocess_exec", return_value=proc):
            with patch(
                "duh.kernel.file_tracker.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                await ft.diff_summary()

        proc.kill.assert_called_once()

    async def test_timeout_kill_process_already_dead(self):
        """ProcessLookupError during kill should not crash."""
        ft = FileTracker()
        ft.track("/a.py", "write")

        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.kill = MagicMock(side_effect=ProcessLookupError)

        with patch("duh.kernel.file_tracker.asyncio.create_subprocess_exec", return_value=proc):
            with patch(
                "duh.kernel.file_tracker.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                result = await ft.diff_summary()

        assert "git timed out" in result
