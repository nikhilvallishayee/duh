"""Tests for duh.kernel.job_queue — async background job execution."""

from __future__ import annotations

import asyncio

import pytest

from duh.kernel.job_queue import JobQueue, JobState, MAX_CONCURRENT_JOBS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _succeed(value: str = "ok") -> str:
    return value


async def _fail(msg: str = "boom") -> str:
    raise RuntimeError(msg)


async def _slow(seconds: float = 0.2) -> str:
    await asyncio.sleep(seconds)
    return "done"


# ---------------------------------------------------------------------------
# submit / status / results basics
# ---------------------------------------------------------------------------


class TestSubmit:
    @pytest.mark.asyncio
    async def test_submit_returns_id(self):
        q = JobQueue()
        job_id = q.submit("echo", _succeed())
        assert isinstance(job_id, str)
        assert len(job_id) == 8  # uuid hex[:8]
        await asyncio.sleep(0.05)  # let it finish

    @pytest.mark.asyncio
    async def test_submit_unique_ids(self):
        q = JobQueue()
        ids = {q.submit(f"j{i}", _succeed(f"v{i}")) for i in range(10)}
        assert len(ids) == 10  # all unique
        await asyncio.sleep(0.1)


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_completed(self):
        q = JobQueue()
        jid = q.submit("fast", _succeed("hello"))
        await asyncio.sleep(0.05)
        info = q.status(jid)
        assert info["state"] == "completed"
        assert info["name"] == "fast"
        assert info["id"] == jid
        assert info["result"] == "hello"

    @pytest.mark.asyncio
    async def test_status_failed(self):
        q = JobQueue()
        jid = q.submit("bad", _fail("oops"))
        await asyncio.sleep(0.05)
        info = q.status(jid)
        assert info["state"] == "failed"
        assert "oops" in info["error"]

    @pytest.mark.asyncio
    async def test_status_unknown_raises(self):
        q = JobQueue()
        with pytest.raises(KeyError):
            q.status("nonexistent")

    @pytest.mark.asyncio
    async def test_status_has_elapsed(self):
        q = JobQueue()
        jid = q.submit("quick", _succeed())
        await asyncio.sleep(0.05)
        info = q.status(jid)
        assert info["elapsed_s"] is not None
        assert info["elapsed_s"] >= 0


class TestResults:
    @pytest.mark.asyncio
    async def test_results_completed(self):
        q = JobQueue()
        jid = q.submit("ok", _succeed("data"))
        await asyncio.sleep(0.05)
        assert q.results(jid) == "data"

    @pytest.mark.asyncio
    async def test_results_failed(self):
        q = JobQueue()
        jid = q.submit("bad", _fail("err"))
        await asyncio.sleep(0.05)
        result = q.results(jid)
        assert "FAILED" in result
        assert "err" in result

    @pytest.mark.asyncio
    async def test_results_pending_raises(self):
        """Calling results() on a running job raises ValueError."""
        q = JobQueue()
        jid = q.submit("slow", _slow(5.0))
        # Job should still be running
        with pytest.raises(ValueError, match="still"):
            q.results(jid)
        # Cancel to clean up
        q._jobs[jid]._task.cancel()
        try:
            await q._jobs[jid]._task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_results_unknown_raises(self):
        q = JobQueue()
        with pytest.raises(KeyError):
            q.results("nope")


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        q = JobQueue()
        assert q.list_jobs() == []

    @pytest.mark.asyncio
    async def test_list_multiple(self):
        q = JobQueue()
        q.submit("a", _succeed("1"))
        q.submit("b", _succeed("2"))
        await asyncio.sleep(0.05)
        jobs = q.list_jobs()
        assert len(jobs) == 2
        names = {j["name"] for j in jobs}
        assert names == {"a", "b"}

    @pytest.mark.asyncio
    async def test_list_newest_first(self):
        q = JobQueue()
        q.submit("first", _succeed())
        await asyncio.sleep(0.01)
        q.submit("second", _succeed())
        await asyncio.sleep(0.05)
        jobs = q.list_jobs()
        assert jobs[0]["name"] == "second"
        assert jobs[1]["name"] == "first"


# ---------------------------------------------------------------------------
# Concurrency limit
# ---------------------------------------------------------------------------


class TestConcurrencyLimit:
    @pytest.mark.asyncio
    async def test_max_concurrent_default(self):
        assert MAX_CONCURRENT_JOBS == 5

    @pytest.mark.asyncio
    async def test_concurrency_enforced(self):
        """Only max_concurrent jobs run simultaneously."""
        running_count = 0
        max_observed = 0
        lock = asyncio.Lock()

        async def _track() -> str:
            nonlocal running_count, max_observed
            async with lock:
                running_count += 1
                if running_count > max_observed:
                    max_observed = running_count
            await asyncio.sleep(0.05)
            async with lock:
                running_count -= 1
            return "done"

        q = JobQueue(max_concurrent=2)
        for i in range(6):
            q.submit(f"j{i}", _track())

        # Wait for all to complete
        await asyncio.sleep(0.5)
        assert max_observed <= 2


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestStateTransitions:
    @pytest.mark.asyncio
    async def test_transitions_to_completed(self):
        q = JobQueue()
        jid = q.submit("ok", _succeed())
        await asyncio.sleep(0.05)
        assert q.status(jid)["state"] == "completed"

    @pytest.mark.asyncio
    async def test_transitions_to_failed(self):
        q = JobQueue()
        jid = q.submit("bad", _fail())
        await asyncio.sleep(0.05)
        assert q.status(jid)["state"] == "failed"

    @pytest.mark.asyncio
    async def test_running_state_visible(self):
        """A slow job should be in 'running' state while executing."""
        q = JobQueue()
        jid = q.submit("slow", _slow(5.0))
        await asyncio.sleep(0.01)  # give it time to start
        info = q.status(jid)
        assert info["state"] in ("pending", "running")
        # Clean up
        q._jobs[jid]._task.cancel()
        try:
            await q._jobs[jid]._task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# None return value
# ---------------------------------------------------------------------------


class TestNoneResult:
    @pytest.mark.asyncio
    async def test_none_return_becomes_empty_string(self):
        async def _return_none() -> None:
            return None

        q = JobQueue()
        jid = q.submit("none", _return_none())
        await asyncio.sleep(0.05)
        assert q.results(jid) == ""


# ---------------------------------------------------------------------------
# bash.py bg: integration (unit-level, no subprocess)
# ---------------------------------------------------------------------------


class TestBashBgPrefix:
    @pytest.mark.asyncio
    async def test_bg_empty_command_errors(self):
        from duh.kernel.tool import ToolContext
        from duh.tools.bash import BashTool

        tool = BashTool()
        result = await tool.call({"command": "bg:"}, ToolContext())
        assert result.is_error
        assert "requires a command" in result.output

    @pytest.mark.asyncio
    async def test_bg_submit_returns_job_id(self):
        from duh.kernel.tool import ToolContext
        from duh.tools.bash import BashTool

        tool = BashTool()
        result = await tool.call(
            {"command": "bg: echo hello"},
            ToolContext(cwd="/tmp"),
        )
        assert not result.is_error
        assert "Background job submitted" in result.output
        assert result.metadata.get("background") is True
        job_id = result.metadata.get("job_id")
        assert job_id is not None
        # Wait for the background job to finish
        await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# REPL /jobs command
# ---------------------------------------------------------------------------


class TestReplJobsCommand:
    def test_jobs_in_slash_commands(self):
        from duh.cli.repl import SLASH_COMMANDS
        assert "/jobs" in SLASH_COMMANDS
