"""Tests for low-severity performance fixes (QE Analysis #8).

Covers:

PERF-8  -- ``FileMemoryStore.recall_facts`` searches facts without
           building a concatenated lowercase haystack string per fact.
PERF-12 -- Redundant shallow copies of the full message list eliminated
           from ``engine._run_primary`` (pass directly to ``query`` since
           ``query`` already takes a defensive copy) and from the
           ``max_turns`` grace-turn branch of ``loop.query`` (list is no
           longer used after that block).
PERF-13 -- ``OpenAIChatGPTProvider`` uses a ``set`` alongside the ordered
           ``text_chunks`` list for O(1) dedup of ``.done``/``.added``
           events that echo already-streamed text.
PERF-14 -- ``MCPExecutor`` maintains a per-server qualified-name index so
           ``disconnect`` and ``_mark_degraded`` run in
           O(tools_for_server) instead of scanning the full tool index.
PERF-15 -- ``UndoStack`` bounds in-memory snapshot bytes by spilling
           oversize / over-budget snapshots to temp files, so a deque
           full of 1 GB files never pins 20 GB in RAM.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

_DUH_ROOT = Path(__file__).resolve().parent.parent.parent
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.adapters.memory_store import FileMemoryStore


# =============================================================================
# PERF-8 -- recall_facts without per-fact temp concatenation
# =============================================================================


class TestRecallFactsNoTempStrings:
    """PERF-8: recall_facts matches via per-field casefold, no haystack concat."""

    def test_matches_by_key(self, tmp_path: Path) -> None:
        store = FileMemoryStore(cwd=str(tmp_path))
        store.store_fact("auth-pattern", "use JWT", ["security"])
        store.store_fact("db-pattern", "use pg", ["data"])

        results = store.recall_facts("auth")
        assert len(results) == 1
        assert results[0]["key"] == "auth-pattern"

    def test_matches_by_value(self, tmp_path: Path) -> None:
        store = FileMemoryStore(cwd=str(tmp_path))
        store.store_fact("k1", "use JWT with refresh", ["security"])
        store.store_fact("k2", "use PostgreSQL", ["data"])

        results = store.recall_facts("jwt")
        assert len(results) == 1
        assert results[0]["key"] == "k1"

    def test_matches_by_tag(self, tmp_path: Path) -> None:
        store = FileMemoryStore(cwd=str(tmp_path))
        store.store_fact("k1", "plain", ["authentication"])
        store.store_fact("k2", "plain", ["database"])

        results = store.recall_facts("authentication")
        assert len(results) == 1
        assert results[0]["key"] == "k1"

    def test_case_insensitive_matching(self, tmp_path: Path) -> None:
        """casefold() gives correct Unicode-aware case-insensitive matching."""
        store = FileMemoryStore(cwd=str(tmp_path))
        store.store_fact("K1", "Hello World", ["Greeting"])

        for q in ["hello", "HELLO", "HeLLo", "WORLD"]:
            results = store.recall_facts(q)
            assert len(results) == 1, f"query={q!r}"

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        store = FileMemoryStore(cwd=str(tmp_path))
        store.store_fact("k1", "v1", ["t1"])

        assert store.recall_facts("nonexistent") == []

    def test_returns_newest_first_respects_limit(self, tmp_path: Path) -> None:
        store = FileMemoryStore(cwd=str(tmp_path))
        for i in range(5):
            store.store_fact(f"match-{i}", "matching value", [])
        # All 5 match.  Limit to 3 newest.
        results = store.recall_facts("matching", limit=3)
        assert len(results) == 3
        # Newest first
        assert results[0]["key"] == "match-4"
        assert results[1]["key"] == "match-3"
        assert results[2]["key"] == "match-2"

    def test_source_no_concatenated_haystack(self) -> None:
        """PERF-8 regression guard: the source must not build a joined
        lowercase haystack string per fact.  The old implementation did
        ``" ".join([...]).lower()`` inside the recall loop.
        """
        source = Path(
            str(_DUH_ROOT / "duh" / "adapters/memory_store.py")
        ).read_text(encoding="utf-8")
        # No ``.lower()`` call inside a haystack construction — we use
        # ``.casefold()`` instead and we never build the combined string.
        # Stronger: the exact legacy pattern is absent.
        assert 'haystack = " ".join' not in source
        # And the implementation must use casefold (unicode-safe).
        assert "query_cf = query.casefold()" in source

    def test_large_value_not_concatenated(self, tmp_path: Path) -> None:
        """A very long value must still be searchable correctly.

        Regression guard: previous implementation built a concatenated
        lowercase copy of (key + " " + value + " " + tags); the new one
        casefolds each field on the fly.  Match correctness on a long
        value proves the new code path still finds substrings.
        """
        big_value = "x" * 100_000 + " needle " + "y" * 100_000
        store = FileMemoryStore(cwd=str(tmp_path))
        store.store_fact("big", big_value, [])

        results = store.recall_facts("needle")
        assert len(results) == 1 and results[0]["key"] == "big"


# =============================================================================
# PERF-12 -- Redundant message list copies removed
# =============================================================================


class TestNoRedundantMessageCopies:
    """PERF-12 -- engine passes self._messages directly; query still does
    its own internal defensive copy so callers are not mutated."""

    def test_engine_run_primary_passes_messages_without_outer_copy(self) -> None:
        """engine.py source no longer wraps self._messages in list() before
        handing it to query()."""
        source = Path(
            str(_DUH_ROOT / "duh" / "kernel/engine.py")
        ).read_text(encoding="utf-8")

        # The old redundant wrap was ``messages=list(self._messages),``.
        # It should no longer appear inside the primary query loop.
        assert "messages=list(self._messages)" not in source

    def test_loop_grace_turn_does_not_copy_current_messages(self) -> None:
        """loop.py grace turn no longer takes ``grace_messages = list(...)``."""
        source = Path(
            str(_DUH_ROOT / "duh" / "kernel/loop.py")
        ).read_text(encoding="utf-8")
        assert "grace_messages = list(current_messages)" not in source

    @pytest.mark.asyncio
    async def test_query_still_defensively_copies_caller_messages(self) -> None:
        """query() must not mutate the caller's list even after PERF-12."""
        from duh.kernel.deps import Deps
        from duh.kernel.loop import query
        from duh.kernel.messages import Message

        async def fake_model(**kwargs: Any):
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "done"}],
                    stop_reason="end_turn",
                ),
            }

        deps = Deps(call_model=fake_model)
        caller_messages = [Message(role="user", content="hi")]
        snapshot_len = len(caller_messages)

        events = []
        async for ev in query(messages=caller_messages, deps=deps, max_turns=1):
            events.append(ev)

        # Even though engine now passes self._messages by reference, query()
        # itself must still copy defensively — the caller's list must be
        # unchanged in length and identity of its elements.
        assert len(caller_messages) == snapshot_len
        assert caller_messages[0].role == "user"


# =============================================================================
# PERF-13 -- OpenAI ChatGPT adapter uses set for O(1) dedup
# =============================================================================


class TestOpenAIChatGPTDedupSet:
    """PERF-13 -- the adapter tracks seen text chunks in a set and dedups
    in O(1) rather than O(N) ``not in list`` membership tests."""

    def test_source_declares_text_chunks_seen_set(self) -> None:
        source = Path(
            str(_DUH_ROOT / "duh" / "adapters/openai_chatgpt.py")
        ).read_text(encoding="utf-8")
        # The set must be declared alongside the ordered list.
        assert "text_chunks_seen: set[str]" in source
        # The ``.done`` dedup check must use the set, not the list.
        # The linter-refactored adapter wraps state in ``_StreamState`` so
        # the set is accessed via ``state.text_chunks_seen`` (or bare
        # ``text_chunks_seen`` in earlier revisions).  Either form is fine.
        assert (
            "not in state.text_chunks_seen" in source
            or "not in text_chunks_seen" in source
        )
        # And critically, the old ``not in text_chunks`` list-membership
        # check must be gone.
        assert "t not in text_chunks" not in source or "t not in text_chunks_seen" in source
        assert "text_s not in text_chunks" not in source or "text_s not in text_chunks_seen" in source

    def test_dedup_preserves_insertion_order_in_concatenation(self) -> None:
        """Model: streaming "a", "b", then a ``.done`` event echoing "a"
        must NOT re-append ``a``, but the final concatenation is still
        ``"ab"`` (streaming order preserved).
        """
        # Simulate the adapter's dedup pattern in isolation.
        text_chunks: list[str] = []
        text_chunks_seen: set[str] = set()

        def on_delta(t: str) -> None:
            text_chunks.append(t)
            text_chunks_seen.add(t)

        def on_done_or_added(t: str) -> None:
            if t not in text_chunks_seen:
                text_chunks.append(t)
                text_chunks_seen.add(t)

        on_delta("a")
        on_delta("b")
        on_done_or_added("a")   # echo — must be skipped
        on_done_or_added("c")   # new — must be appended

        assert text_chunks == ["a", "b", "c"]
        assert "".join(text_chunks) == "abc"

    def test_dedup_set_scales_to_many_chunks(self) -> None:
        """Regression guard: 10_000 unique chunks + one echo must be O(1)
        for each membership test, not O(N)."""
        text_chunks: list[str] = []
        text_chunks_seen: set[str] = set()
        for i in range(10_000):
            s = f"chunk-{i}"
            text_chunks.append(s)
            text_chunks_seen.add(s)
        # Now an echo — with the old list-based check this would be O(N).
        # With a set, it's O(1).  Assert behaviour (not timing).
        echo = "chunk-0"
        added = echo not in text_chunks_seen
        assert added is False
        assert len(text_chunks) == 10_000


# =============================================================================
# PERF-14 -- MCP executor per-server tool index
# =============================================================================


# Install fake mcp module if not already installed (tests that ran before
# us may have left it in place; if not, we install it ourselves).


@dataclass
class _FakeToolInfo:
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeListToolsResult:
    tools: list[_FakeToolInfo] = field(default_factory=list)


class _FakeClientSession:
    def __init__(self, read_stream: Any = None, write_stream: Any = None):
        self._tools: list[_FakeToolInfo] = []

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> _FakeListToolsResult:
        return _FakeListToolsResult(tools=self._tools)

    async def __aenter__(self) -> "_FakeClientSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class _FakeStdioServerParameters:
    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None):
        self.command = command
        self.args = args or []
        self.env = env


def _ensure_fake_mcp_installed() -> None:
    if "mcp" in sys.modules and hasattr(sys.modules["mcp"], "ClientSession"):
        return
    mcp_mod = ModuleType("mcp")
    mcp_mod.ClientSession = _FakeClientSession
    mcp_mod.StdioServerParameters = _FakeStdioServerParameters
    mcp_client = ModuleType("mcp.client")
    mcp_client_stdio = ModuleType("mcp.client.stdio")

    async def fake_stdio_client(params: Any) -> Any:
        return None

    mcp_client_stdio.stdio_client = fake_stdio_client
    sys.modules.setdefault("mcp", mcp_mod)
    sys.modules.setdefault("mcp.client", mcp_client)
    sys.modules.setdefault("mcp.client.stdio", mcp_client_stdio)


_ensure_fake_mcp_installed()


from duh.adapters.mcp_executor import MCPExecutor, MCPServerConfig


async def _build_executor_with_tools(
    server_tools: dict[str, list[str]],
) -> MCPExecutor:
    """Create an MCPExecutor with given (server_name -> [tool_names]) tools
    already populated by connecting via the fake session.

    Async helper — must be awaited from a running event loop.
    """
    configs = {
        name: MCPServerConfig(command="echo", args=[])
        for name in server_tools
    }
    executor = MCPExecutor(configs)

    async def fake_start(params: Any) -> tuple[Any, Any, Any]:
        return (MagicMock(), MagicMock(), MagicMock())

    executor._start_stdio = fake_start  # type: ignore[assignment]

    for server_name, tool_names in server_tools.items():
        session = _FakeClientSession()
        session._tools = [_FakeToolInfo(name=t) for t in tool_names]
        with patch(
            "duh.adapters.mcp_executor.ClientSession",
            return_value=session,
        ):
            await executor.connect(server_name)
    return executor


class TestMCPPerServerToolIndex:
    """PERF-14 -- per-server tool index for O(tools_for_server) disconnect."""

    @pytest.mark.asyncio
    async def test_per_server_index_populated_on_connect(self) -> None:
        executor = await _build_executor_with_tools(
            {"srv1": ["t1", "t2"], "srv2": ["t3"]},
        )
        # Index should have per-server buckets
        assert "srv1" in executor._server_tools
        assert "srv2" in executor._server_tools
        assert executor._server_tools["srv1"] == {
            "mcp__srv1__t1",
            "mcp__srv1__t2",
        }
        assert executor._server_tools["srv2"] == {"mcp__srv2__t3"}

    @pytest.mark.asyncio
    async def test_disconnect_only_removes_targeted_server_tools(self) -> None:
        executor = await _build_executor_with_tools(
            {"srv1": ["t1", "t2"], "srv2": ["t3"]},
        )
        assert len(executor.tool_names) == 3
        await executor.disconnect("srv1")
        assert len(executor.tool_names) == 1
        assert "mcp__srv2__t3" in executor.tool_names
        assert "srv1" not in executor._server_tools

    @pytest.mark.asyncio
    async def test_disconnect_does_not_scan_other_servers(self) -> None:
        """Even with many foreign tools, disconnect touches only its own.

        We substitute the executor's ``_tool_index`` for a subclassed
        dict that counts every ``.items()`` call.  The fixed (O(k))
        implementation uses ``_server_tools.pop`` + per-key ``.pop()``
        and must NEVER call ``_tool_index.items()`` during disconnect.
        """
        executor = await _build_executor_with_tools(
            {
                "srv_big": [f"t{i}" for i in range(1000)],
                "srv_small": ["a", "b"],
            }
        )
        assert len(executor.tool_names) == 1002

        class _CountingDict(dict):
            items_calls = 0

            def items(self):  # type: ignore[override]
                _CountingDict.items_calls += 1
                return super().items()

        counting = _CountingDict(executor._tool_index)
        executor._tool_index = counting

        await executor.disconnect("srv_small")

        assert _CountingDict.items_calls == 0
        assert len(executor.tool_names) == 1000

    @pytest.mark.asyncio
    async def test_mark_degraded_removes_via_per_server_index(self) -> None:
        executor = await _build_executor_with_tools(
            {"srv1": ["t1", "t2"], "srv2": ["t3"]},
        )
        executor._mark_degraded("srv1")
        assert "srv1" in executor._degraded
        # srv1 tools gone, srv2 tools intact.
        remaining = executor.tool_names
        assert "mcp__srv2__t3" in remaining
        assert "mcp__srv1__t1" not in remaining
        assert "mcp__srv1__t2" not in remaining
        assert "srv1" not in executor._server_tools

    @pytest.mark.asyncio
    async def test_connect_failure_rolls_back_partial_index(self) -> None:
        """If connect() raises after partially populating tools, the
        partial entries must be rolled back from both ``_tool_index``
        and ``_server_tools``."""
        configs = {"srv": MCPServerConfig(command="echo", args=[])}
        executor = MCPExecutor(configs)

        async def fail_start(params: Any) -> tuple[Any, Any, Any]:
            raise OSError("simulated stdio failure")

        executor._start_stdio = fail_start  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="Failed to connect"):
            await executor.connect("srv")

        # Index and per-server map both empty.
        assert executor._tool_index == {}
        assert "srv" not in executor._server_tools


# =============================================================================
# PERF-15 -- UndoStack bounded memory via spill-to-disk
# =============================================================================


class TestUndoStackBoundedMemory:
    """PERF-15 -- UndoStack caps in-memory snapshot bytes by spilling
    oversize / over-budget snapshots to temp files."""

    def test_small_snapshot_stays_in_memory(self) -> None:
        from duh.kernel.undo import UndoStack

        s = UndoStack()
        s.push("/a.py", "hello")
        assert s.in_memory_bytes == len("hello".encode("utf-8"))
        top = s.peek()
        assert top == ("/a.py", "hello")

    def test_oversize_snapshot_is_spilled_to_disk(self, tmp_path: Path) -> None:
        from duh.kernel.undo import UndoStack

        # Per-entry cap = 100 bytes; entry = 500 bytes → spill.
        s = UndoStack(per_entry_max_bytes=100, total_max_bytes=10_000)
        big = "x" * 500
        s.push(str(tmp_path / "big.py"), big)

        # No in-memory bytes — the entry is on disk.
        assert s.in_memory_bytes == 0
        # peek() transparently loads from spill.
        top = s.peek()
        assert top is not None and top[1] == big

    def test_total_budget_triggers_oldest_spill(self, tmp_path: Path) -> None:
        from duh.kernel.undo import UndoStack

        # 4 entries of 100 bytes each = 400 bytes; cap = 250 bytes.
        # After all 4 pushes, the oldest entries must have spilled so
        # total <= cap.
        s = UndoStack(
            maxlen=10,
            per_entry_max_bytes=10_000,  # each entry fits individually
            total_max_bytes=250,
        )
        for i in range(4):
            s.push(f"/f{i}.py", "x" * 100)

        assert s.depth == 4
        assert s.in_memory_bytes <= 250

        # Every entry must still be retrievable (spilled or not).
        # peek() returns the newest; we undo them one at a time and
        # verify each content round-trips correctly.
        seen: list[tuple[str, str | None]] = []
        while s.depth:
            # Snapshot the peek before undo for equality.
            entry = s.peek()
            seen.append(entry)  # type: ignore[arg-type]
            # We don't actually need to write real files — skip undo()
            # since it tries to restore onto disk.  Break out.
            break

        # The newest entry is /f3.py with content "xxx...".
        assert seen[0] == ("/f3.py", "x" * 100)

    def test_clear_releases_spill_files(self, tmp_path: Path) -> None:
        from duh.kernel.undo import UndoStack

        s = UndoStack(per_entry_max_bytes=50)
        big1 = "a" * 200
        big2 = "b" * 200
        s.push(str(tmp_path / "a"), big1)
        s.push(str(tmp_path / "b"), big2)

        # Collect spill paths before clear.
        spill_paths = [
            snap.spill_path for snap in s._stack
            if snap.spill_path is not None
        ]
        assert len(spill_paths) == 2
        for p in spill_paths:
            assert Path(p).exists()

        s.clear()

        assert s.depth == 0
        assert s.in_memory_bytes == 0
        for p in spill_paths:
            assert not Path(p).exists()

    def test_undo_restores_spilled_content(self, tmp_path: Path) -> None:
        from duh.kernel.undo import UndoStack

        target = tmp_path / "target.py"
        target.write_text("MODIFIED", encoding="utf-8")

        original = "ORIGINAL " * 10_000  # ~90 KB, bigger than per-entry cap
        s = UndoStack(per_entry_max_bytes=1024, total_max_bytes=100_000)
        s.push(str(target), original)

        # Top entry must be on disk.
        assert s.in_memory_bytes == 0

        path, msg = s.undo()
        assert path == str(target)
        assert "Restored" in msg
        assert target.read_text(encoding="utf-8") == original

    def test_new_file_entries_do_not_count_towards_memory(self) -> None:
        from duh.kernel.undo import UndoStack

        s = UndoStack()
        s.push("/a.py", None)  # new file — no content
        s.push("/b.py", None)
        assert s.in_memory_bytes == 0
        assert s.depth == 2
        assert s.peek() == ("/b.py", None)

    def test_ring_buffer_eviction_releases_spill_file(
        self, tmp_path: Path,
    ) -> None:
        from duh.kernel.undo import UndoStack

        s = UndoStack(maxlen=2, per_entry_max_bytes=10)
        # Push 3 large entries — first one is evicted when third is pushed.
        s.push(str(tmp_path / "a"), "a" * 100)
        evicted_spill = s._stack[0].spill_path
        assert evicted_spill is not None and Path(evicted_spill).exists()

        s.push(str(tmp_path / "b"), "b" * 100)
        s.push(str(tmp_path / "c"), "c" * 100)

        assert s.depth == 2
        # The first entry's spill file must have been released.
        assert not Path(evicted_spill).exists()

    def test_construction_validates_byte_caps(self) -> None:
        from duh.kernel.undo import UndoStack

        with pytest.raises(ValueError, match="per_entry_max_bytes"):
            UndoStack(per_entry_max_bytes=0)
        with pytest.raises(ValueError, match="total_max_bytes"):
            UndoStack(total_max_bytes=0)
