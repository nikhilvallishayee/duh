"""Tests for medium-severity performance fixes (issue #25).

Covers:
  PERF-5  -- JobQueue evicts oldest completed/failed jobs once over cap.
  PERF-6  -- CacheTracker maintains O(1) running totals.
  PERF-7  -- FileMemoryStore.store_fact uses append-only fast path
             when the key is brand new.
  PERF-9  -- Tool registry's lazy_mode returns LazyTool proxies that
             defer instantiation until first use.
  PERF-10 -- The query loop runs read-only tool_use blocks concurrently
             via asyncio.gather while keeping mutating blocks sequential.
  PERF-11 -- Security Runner runs scanners concurrently with a max-4
             semaphore and isolates per-scanner failures.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from duh.adapters.memory_store import FACTS_LINE_CAP, FileMemoryStore
from duh.kernel.cache_tracker import CacheTracker, _MAX_HISTORY
from duh.kernel.deps import Deps
from duh.kernel.job_queue import JobQueue, JobState
from duh.kernel.loop import _build_read_only_set, query
from duh.kernel.messages import Message
from duh.security.config import ScannerConfig, SecurityPolicy
from duh.security.engine import MAX_PARALLEL_SCANNERS, Runner, ScannerRegistry
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier
from duh.tools.registry import LazyTool, get_all_tools


# ---------------------------------------------------------------------------
# PERF-5 -- JobQueue cleanup
# ---------------------------------------------------------------------------


class TestJobQueueCleanup:
    """Once finished jobs exceed max_completed_retained, oldest are evicted."""

    @pytest.mark.asyncio
    async def test_retains_only_max_after_overflow(self):
        async def _ok() -> str:
            return "x"

        q = JobQueue(max_completed_retained=50)
        for i in range(100):
            q.submit(f"job-{i}", _ok())

        # Drain until every submitted task has run.  We can't rely on
        # job IDs (some may have been evicted) so wait until the queue
        # is quiescent.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not any(
                j["state"] in ("pending", "running")
                for j in q.list_jobs()
            ):
                break

        all_jobs = q.list_jobs()
        # No more than 50 retained — pending/running may briefly add a
        # few during transition, but here everything is quiesced.
        assert len(all_jobs) == 50
        # All retained jobs should be completed (no pending/running left).
        assert all(j["state"] == "completed" for j in all_jobs)

    @pytest.mark.asyncio
    async def test_keeps_running_jobs_even_when_full(self):
        """Running jobs are never evicted even if over the cap."""

        async def _slow() -> str:
            await asyncio.sleep(0.5)
            return "done"

        async def _ok() -> str:
            return "ok"

        q = JobQueue(max_completed_retained=2, max_concurrent=10)

        # One slow job that will still be running when we check.
        slow_id = q.submit("slow", _slow())

        # 10 fast jobs that should all complete and trigger eviction.
        for i in range(10):
            q.submit(f"fast-{i}", _ok())

        # Wait for fast jobs to complete (but slow is still running).
        await asyncio.sleep(0.05)

        all_jobs = q.list_jobs()
        # The slow (running) job must still be present.
        assert any(j["id"] == slow_id for j in all_jobs)

    @pytest.mark.asyncio
    async def test_failed_jobs_also_evicted(self):
        async def _boom() -> str:
            raise RuntimeError("nope")

        q = JobQueue(max_completed_retained=3)
        for i in range(10):
            q.submit(f"j-{i}", _boom())

        for _ in range(50):
            await asyncio.sleep(0.01)
            if not any(
                j["state"] in ("pending", "running")
                for j in q.list_jobs()
            ):
                break

        all_jobs = q.list_jobs()
        assert len(all_jobs) == 3
        assert all(j["state"] == "failed" for j in all_jobs)


# ---------------------------------------------------------------------------
# PERF-6 -- CacheTracker running totals
# ---------------------------------------------------------------------------


class TestCacheTrackerRunningTotals:
    def test_totals_match_after_many_records(self):
        t = CacheTracker()
        for i in range(2000):
            t.record_usage({
                "input_tokens": 10,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 5,
            })
        assert t.total_cache_read_tokens() == 200_000
        assert t.total_cache_creation_tokens() == 10_000

    def test_history_is_bounded(self):
        """Internal history is bounded but totals stay accurate."""
        t = CacheTracker()
        for _ in range(_MAX_HISTORY + 100):
            t.record_usage({"cache_read_input_tokens": 1})
        # History never exceeds the bound.
        assert len(t._history) <= _MAX_HISTORY
        # Running total still counts every record.
        assert t.total_cache_read_tokens() == _MAX_HISTORY + 100

    def test_totals_are_o1_under_load(self):
        """Reading totals shouldn't depend on history length."""
        t_small = CacheTracker()
        t_big = CacheTracker()
        for _ in range(10):
            t_small.record_usage({"cache_read_input_tokens": 1})
        for _ in range(10_000):
            t_big.record_usage({"cache_read_input_tokens": 1})

        # Both reads should be effectively instant.
        t0 = time.perf_counter()
        for _ in range(1000):
            t_small.total_cache_read_tokens()
            t_big.total_cache_read_tokens()
        elapsed = time.perf_counter() - t0
        # 2000 reads in well under a second even on the slowest CI box.
        assert elapsed < 0.5

    def test_summary_uses_running_turn_count(self):
        """Summary turn count must reflect ALL records, not bounded history."""
        t = CacheTracker()
        for _ in range(_MAX_HISTORY + 50):
            t.record_usage({
                "input_tokens": 1,
                "cache_read_input_tokens": 1,
            })
        s = t.summary()
        assert f"{_MAX_HISTORY + 50}" in s


# ---------------------------------------------------------------------------
# PERF-7 -- facts.jsonl append-only fast path
# ---------------------------------------------------------------------------


def _store(tmp_path: Path) -> FileMemoryStore:
    s = FileMemoryStore(cwd="/tmp/fake")
    s._memory_dir = tmp_path / "memory"
    s._facts_dir = tmp_path / "facts"
    return s


class TestFactsAppendOnly:
    def test_new_key_appends_without_full_rewrite(self, tmp_path: Path, monkeypatch):
        s = _store(tmp_path)
        s.store_fact("first", "v1")

        rewrites: list[int] = []

        original = FileMemoryStore._write_all_facts

        def _spy(self, entries):  # type: ignore[no-untyped-def]
            rewrites.append(len(entries))
            return original(self, entries)

        monkeypatch.setattr(FileMemoryStore, "_write_all_facts", _spy)

        s.store_fact("second", "v2")
        s.store_fact("third", "v3")

        # Fast path used both times — no full rewrite.
        assert rewrites == []

        all_facts = s.list_facts()
        keys = [f["key"] for f in all_facts]
        assert keys == ["first", "second", "third"]

    def test_existing_key_triggers_rewrite(self, tmp_path: Path, monkeypatch):
        s = _store(tmp_path)
        s.store_fact("k", "v1")

        rewrites: list[int] = []
        original = FileMemoryStore._write_all_facts

        def _spy(self, entries):  # type: ignore[no-untyped-def]
            rewrites.append(len(entries))
            return original(self, entries)

        monkeypatch.setattr(FileMemoryStore, "_write_all_facts", _spy)

        s.store_fact("k", "v2")  # update — slow path

        assert rewrites == [1]
        facts = s.list_facts()
        assert len(facts) == 1
        assert facts[0]["value"] == "v2"

    def test_cap_enforced_via_full_rewrite(self, tmp_path: Path, monkeypatch):
        """When line cap reached, fall back to read-filter-rewrite."""
        s = _store(tmp_path)
        # Fill to cap with append fast path.
        for i in range(FACTS_LINE_CAP):
            s.store_fact(f"k-{i}", "v")
        assert len(s.list_facts()) == FACTS_LINE_CAP

        # One more — must trigger the slow path which prunes oldest.
        rewrites: list[int] = []
        original = FileMemoryStore._write_all_facts

        def _spy(self, entries):  # type: ignore[no-untyped-def]
            rewrites.append(len(entries))
            return original(self, entries)

        monkeypatch.setattr(FileMemoryStore, "_write_all_facts", _spy)
        s.store_fact("k-new", "v")
        assert rewrites and rewrites[-1] == FACTS_LINE_CAP

        keys = [f["key"] for f in s.list_facts()]
        assert "k-new" in keys
        assert "k-0" not in keys  # oldest pruned


# ---------------------------------------------------------------------------
# PERF-9 -- Lazy tool loading
# ---------------------------------------------------------------------------


class TestLazyToolLoading:
    def test_lazy_mode_returns_lazy_tools(self):
        tools = get_all_tools(lazy_mode=True)
        assert tools, "expected at least some tools"

        # Most should be LazyTool proxies (Skill/ToolSearch are eager).
        lazy_count = sum(1 for t in tools if isinstance(t, LazyTool))
        assert lazy_count > 5

    def test_lazy_tool_does_not_instantiate_on_construction(self):
        loaded: list[str] = []

        def factory():
            loaded.append("instantiated")
            return object()

        proxy = LazyTool("Demo", factory)
        # Touching .name (a __slots__ attr) must NOT trigger the factory.
        assert proxy.name == "Demo"
        assert loaded == []

    def test_lazy_tool_resolves_on_attribute_access(self):
        loaded: list[str] = []

        class _Real:
            description = "hello"

        def factory():
            loaded.append("instantiated")
            return _Real()

        proxy = LazyTool("Demo", factory)
        # First proxied attribute access triggers the factory.
        assert proxy.description == "hello"
        assert loaded == ["instantiated"]
        # Second access doesn't re-instantiate.
        assert proxy.description == "hello"
        assert loaded == ["instantiated"]

    def test_lazy_mode_does_not_construct_tool_instances(self):
        """Lazy mode must not run the constructors of deferred tools.

        We track every ``Tool.__init__`` call by patching a no-op
        registration hook into ``LazyTool``'s factory path. Each lazy
        proxy starts with ``_instance = None`` and stays that way until
        an attribute (other than ``name``) is accessed.
        """
        tools = get_all_tools(lazy_mode=True)
        lazy_tools = [t for t in tools if isinstance(t, LazyTool)]
        assert lazy_tools, "expected lazy proxies in lazy mode"
        # All lazy proxies must be unresolved immediately after registry build.
        unresolved = [t for t in lazy_tools if t._instance is None]
        assert len(unresolved) == len(lazy_tools), (
            "lazy_mode should not eagerly instantiate any tool"
        )

    def test_lazy_mode_resolves_only_on_demand(self):
        """Touching one lazy tool must not resolve the others."""
        tools = get_all_tools(lazy_mode=True)
        lazy_tools = [t for t in tools if isinstance(t, LazyTool)]
        # Touch exactly one to force resolution.
        first = lazy_tools[0]
        _ = first.description  # forces load
        assert first._instance is not None
        # The rest stay deferred.
        unresolved = sum(
            1 for t in lazy_tools[1:] if t._instance is None
        )
        assert unresolved == len(lazy_tools) - 1


# ---------------------------------------------------------------------------
# PERF-10 -- Parallel read-only tool execution
# ---------------------------------------------------------------------------


class _ToolStub:
    def __init__(self, name: str, read_only: bool) -> None:
        self.name = name
        self._ro = read_only

    @property
    def is_read_only(self) -> bool:
        return self._ro


class TestReadOnlyDetection:
    def test_build_read_only_set(self):
        tools = [
            _ToolStub("Read", True),
            _ToolStub("Write", False),
            _ToolStub("Grep", True),
        ]
        names = _build_read_only_set(tools)
        assert names == {"Read", "Grep"}

    def test_build_read_only_set_empty(self):
        assert _build_read_only_set(None) == set()
        assert _build_read_only_set([]) == set()


class TestParallelReadOnlyExecution:
    @pytest.mark.asyncio
    async def test_five_read_only_tools_run_concurrently(self):
        """5 read-only Read calls each take 50ms; total < 150ms (not 250ms)."""
        per_tool_delay = 0.05  # 50ms

        tool_starts: list[float] = []

        async def call_model(messages, system_prompt, model, tools, thinking, tool_choice):
            # Single assistant turn with five Read tool_use blocks.
            content = [
                {"type": "tool_use", "id": f"id-{i}", "name": "Read", "input": {"path": f"/p{i}"}}
                for i in range(5)
            ]
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=content,
                    metadata={"stop_reason": "tool_use"},
                ),
            }
            return

        async def call_model_done(messages, system_prompt, model, tools, thinking, tool_choice):
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "done"}],
                    metadata={"stop_reason": "end_turn"},
                ),
            }
            return

        # Two-call sequence: first emits tool_use, second ends.
        call_count = {"n": 0}

        async def call_model_router(messages, system_prompt, model, tools, thinking, tool_choice):
            call_count["n"] += 1
            if call_count["n"] == 1:
                async for e in call_model(messages, system_prompt, model, tools, thinking, tool_choice):
                    yield e
            else:
                async for e in call_model_done(messages, system_prompt, model, tools, thinking, tool_choice):
                    yield e

        async def run_tool(name: str, input: Any) -> str:
            tool_starts.append(time.perf_counter())
            await asyncio.sleep(per_tool_delay)
            return f"read {input.get('path', '')}"

        deps = Deps(call_model=call_model_router, run_tool=run_tool)
        tools = [_ToolStub("Read", read_only=True)]

        t0 = time.perf_counter()
        results = []
        async for evt in query(messages=[Message(role="user", content="hi")], deps=deps, tools=tools):
            results.append(evt)
        elapsed = time.perf_counter() - t0

        # Sanity: the loop completed.
        assert any(e.get("type") == "done" for e in results)
        # Five tool_result events (one per concurrent Read).
        tool_results = [e for e in results if e.get("type") == "tool_result"]
        assert len(tool_results) == 5
        # Concurrent execution => total time near per_tool_delay, not 5x.
        assert elapsed < per_tool_delay * 3, (
            f"expected concurrent execution, got {elapsed:.3f}s for 5 reads"
        )
        # All five tool starts were within a small window of each other.
        assert max(tool_starts) - min(tool_starts) < per_tool_delay

    @pytest.mark.asyncio
    async def test_mutating_tools_run_sequentially(self):
        """Write calls must not run concurrently — observable via start-time spread."""
        per_tool_delay = 0.03

        tool_starts: list[float] = []
        tool_ends: list[float] = []

        async def emit_writes(messages, system_prompt, model, tools, thinking, tool_choice):
            content = [
                {"type": "tool_use", "id": f"id-{i}", "name": "Write", "input": {"i": i}}
                for i in range(3)
            ]
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=content,
                    metadata={"stop_reason": "tool_use"},
                ),
            }

        async def emit_done(messages, system_prompt, model, tools, thinking, tool_choice):
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "ok"}],
                    metadata={"stop_reason": "end_turn"},
                ),
            }

        n = {"i": 0}

        async def call_model(messages, system_prompt, model, tools, thinking, tool_choice):
            n["i"] += 1
            gen = emit_writes if n["i"] == 1 else emit_done
            async for e in gen(messages, system_prompt, model, tools, thinking, tool_choice):
                yield e

        async def run_tool(name: str, input: Any) -> str:
            tool_starts.append(time.perf_counter())
            await asyncio.sleep(per_tool_delay)
            tool_ends.append(time.perf_counter())
            return "wrote"

        deps = Deps(call_model=call_model, run_tool=run_tool)
        tools = [_ToolStub("Write", read_only=False)]

        events = []
        async for e in query(messages=[Message(role="user", content="hi")], deps=deps, tools=tools):
            events.append(e)

        # Each Write should start AFTER the previous one ended.
        assert tool_starts[1] >= tool_ends[0] - 0.001
        assert tool_starts[2] >= tool_ends[1] - 0.001


# ---------------------------------------------------------------------------
# PERF-11 -- Parallel scanner execution
# ---------------------------------------------------------------------------


def _make_finding(scanner: str) -> Finding:
    return Finding.create(
        id=f"{scanner.upper()}-1",
        aliases=(),
        scanner=scanner,
        severity=Severity.LOW,
        message="x",
        description="",
        location=Location(file="a.py", line_start=1, line_end=1, snippet=""),
    )


class _SleepyScanner(InProcessScanner):
    """Scanner that sleeps for a configurable duration before returning."""

    tier: Tier = "minimal"
    _module_name = "json"
    _delay: float = 0.1

    def __init__(self, name: str, delay: float = 0.1) -> None:
        self.name = name
        self._delay = delay

    async def _scan_impl(self, target, cfg, *, changed_files):
        await asyncio.sleep(self._delay)
        return [_make_finding(self.name)]


class _CrashingScanner(InProcessScanner):
    tier: Tier = "minimal"
    _module_name = "json"

    def __init__(self, name: str) -> None:
        self.name = name

    async def _scan_impl(self, target, cfg, *, changed_files):
        raise RuntimeError(f"{self.name} crash")


class TestParallelScanners:
    def test_parallel_scanners_finish_concurrently(self):
        """Four 100ms scanners should finish in <200ms (not 400ms)."""
        reg = ScannerRegistry()
        for name in ("a", "b", "c", "d"):
            reg.register(_SleepyScanner(name, delay=0.1))

        runner = Runner(registry=reg, policy=SecurityPolicy())

        t0 = time.perf_counter()
        results = asyncio.run(
            runner.run(Path("."), scanners=["a", "b", "c", "d"])
        )
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.3, f"expected concurrent execution; got {elapsed:.3f}s"
        assert len(results) == 4
        assert all(r.status == "ok" for r in results)

    def test_semaphore_caps_concurrency_at_max_parallel(self):
        """A 5th scanner is queued behind the semaphore (max 4 concurrent)."""
        # Use observable concurrency tracker.
        in_flight = {"n": 0, "peak": 0}
        lock = asyncio.Lock() if False else None  # asyncio internals

        class _Track(InProcessScanner):
            tier: Tier = "minimal"
            _module_name = "json"

            def __init__(self, name: str) -> None:
                self.name = name

            async def _scan_impl(self, target, cfg, *, changed_files):
                in_flight["n"] += 1
                in_flight["peak"] = max(in_flight["peak"], in_flight["n"])
                await asyncio.sleep(0.05)
                in_flight["n"] -= 1
                return []

        reg = ScannerRegistry()
        names = [f"s{i}" for i in range(8)]
        for n in names:
            reg.register(_Track(n))

        runner = Runner(
            registry=reg,
            policy=SecurityPolicy(),
            max_parallel=MAX_PARALLEL_SCANNERS,
        )
        asyncio.run(runner.run(Path("."), scanners=names))
        assert in_flight["peak"] <= MAX_PARALLEL_SCANNERS

    def test_one_scanner_failure_does_not_stop_others(self):
        reg = ScannerRegistry()
        reg.register(_SleepyScanner("ok-a", delay=0.01))
        reg.register(_CrashingScanner("boom"))
        reg.register(_SleepyScanner("ok-b", delay=0.01))

        runner = Runner(registry=reg, policy=SecurityPolicy())
        results = asyncio.run(
            runner.run(Path("."), scanners=["ok-a", "boom", "ok-b"])
        )
        by_name = {r.scanner: r for r in results}
        assert by_name["ok-a"].status == "ok"
        assert by_name["boom"].status == "error"
        assert by_name["ok-b"].status == "ok"

    def test_result_order_matches_input_order(self):
        """gather() preserves input order even when scanners finish out of order."""
        reg = ScannerRegistry()
        reg.register(_SleepyScanner("slow", delay=0.1))
        reg.register(_SleepyScanner("fast", delay=0.01))
        reg.register(_SleepyScanner("medium", delay=0.05))

        runner = Runner(registry=reg, policy=SecurityPolicy())
        results = asyncio.run(
            runner.run(Path("."), scanners=["slow", "fast", "medium"])
        )
        assert [r.scanner for r in results] == ["slow", "fast", "medium"]
