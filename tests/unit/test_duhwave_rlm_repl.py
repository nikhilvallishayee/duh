"""Tests for duh.duhwave.rlm.repl — RLMRepl host-side controller.

Each test spins up a real sandboxed REPL subprocess (``python3 -I`` running
``_bootstrap.py``) — the whole point of the RLM substrate is the
out-of-process boundary, so mocks would defeat the test.

Cleanup is mandatory: every fixture awaits ``repl.shutdown()`` so leaking a
subprocess across tests is impossible.
"""

from __future__ import annotations

import asyncio

import pytest

from duh.duhwave.rlm import RLMRepl, RLMReplError


@pytest.fixture
async def repl():
    """Started REPL, shut down on teardown."""
    r = RLMRepl()
    await r.start()
    try:
        yield r
    finally:
        await r.shutdown()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_start_and_shutdown(self):
        r = RLMRepl()
        await r.start()
        assert r._proc is not None
        await r.shutdown()
        assert r._proc is None

    async def test_double_start_raises(self):
        r = RLMRepl()
        await r.start()
        try:
            with pytest.raises(RLMReplError, match="already started"):
                await r.start()
        finally:
            await r.shutdown()

    async def test_shutdown_idempotent(self):
        r = RLMRepl()
        await r.start()
        await r.shutdown()
        # Second shutdown is a no-op.
        await r.shutdown()
        assert r._proc is None

    async def test_shutdown_without_start_is_noop(self):
        r = RLMRepl()
        await r.shutdown()
        assert r._proc is None


# ---------------------------------------------------------------------------
# bind / Handle metadata
# ---------------------------------------------------------------------------


class TestBind:
    async def test_bind_str_metadata(self, repl):
        text = "hello\nworld\n"
        h = await repl.bind("greeting", text)
        assert h.name == "greeting"
        assert h.kind == "str"
        assert h.total_chars == len(text)
        assert h.total_lines == 2
        # sha256 of the utf-8 encoded text.
        import hashlib
        assert h.sha256 == hashlib.sha256(text.encode()).hexdigest()
        # Stored in handle store.
        assert repl.handles.get("greeting") is h

    async def test_bind_str_no_trailing_newline_counts_partial_line(self, repl):
        h = await repl.bind("note", "abc")
        # No newline → still counts as 1 line.
        assert h.total_lines == 1

    async def test_bind_empty_str(self, repl):
        h = await repl.bind("blank", "")
        assert h.total_chars == 0
        assert h.total_lines == 0

    async def test_bind_then_listed(self, repl):
        await repl.bind("a", "alpha")
        await repl.bind("b", "beta")
        names = [h.name for h in repl.handles.list()]
        assert names == ["a", "b"]

    async def test_bind_duplicate_name_raises(self, repl):
        await repl.bind("dup", "first")
        with pytest.raises(ValueError, match="already bound"):
            await repl.bind("dup", "second")


# ---------------------------------------------------------------------------
# peek
# ---------------------------------------------------------------------------


class TestPeek:
    async def test_peek_chars_default(self, repl):
        await repl.bind("doc", "abcdefghij")
        out = await repl.peek("doc")
        assert out == "abcdefghij"

    async def test_peek_chars_slice(self, repl):
        await repl.bind("doc", "abcdefghij")
        out = await repl.peek("doc", start=2, end=5)
        assert out == "cde"

    async def test_peek_clamps_end_beyond_length(self, repl):
        await repl.bind("doc", "abc")
        # Python slice semantics — end past length silently clamps.
        out = await repl.peek("doc", start=0, end=999)
        assert out == "abc"

    async def test_peek_clamps_start_beyond_length(self, repl):
        await repl.bind("doc", "abc")
        out = await repl.peek("doc", start=999, end=1000)
        assert out == ""

    async def test_peek_lines_mode(self, repl):
        await repl.bind("doc", "line0\nline1\nline2\nline3")
        out = await repl.peek("doc", start=1, end=3, mode="lines")
        assert out == "line1\nline2"

    async def test_peek_unknown_handle_raises(self, repl):
        with pytest.raises(RLMReplError, match="unknown handle"):
            await repl.peek("ghost")

    async def test_peek_unknown_mode_raises(self, repl):
        await repl.bind("doc", "x")
        with pytest.raises(RLMReplError, match="unknown mode"):
            await repl.peek("doc", mode="paragraphs")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_search_returns_line_col_snippet(self, repl):
        text = "alpha\nbeta gamma\ndelta"
        await repl.bind("doc", text)
        hits = await repl.search("doc", r"gamma")
        assert len(hits) == 1
        h = hits[0]
        assert h["line"] == 2
        assert h["col"] == 5  # "beta " is 5 chars before "gamma"
        assert "gamma" in h["snippet"]
        assert h["span"] == [11, 16]

    async def test_search_max_hits_enforced(self, repl):
        await repl.bind("doc", "x" * 100)
        hits = await repl.search("doc", r"x", max_hits=5)
        assert len(hits) == 5

    async def test_search_no_match_empty_list(self, repl):
        await repl.bind("doc", "alpha beta")
        hits = await repl.search("doc", r"zzz")
        assert hits == []

    async def test_search_bad_regex_clean_error(self, repl):
        await repl.bind("doc", "x")
        with pytest.raises(RLMReplError, match="bad regex"):
            await repl.search("doc", r"(unclosed")

    async def test_search_unknown_handle_raises(self, repl):
        with pytest.raises(RLMReplError, match="unknown handle"):
            await repl.search("ghost", "anything")


# ---------------------------------------------------------------------------
# slice
# ---------------------------------------------------------------------------


class TestSlice:
    async def test_slice_creates_new_handle(self, repl):
        await repl.bind("src", "abcdefghij")
        new = await repl.slice("src", 2, 5, "sub")
        assert new.name == "sub"
        assert new.bound_by == "slice:src"
        # Both handles exist independently.
        assert repl.handles.get("src") is not None
        assert repl.handles.get("sub") is not None

    async def test_slice_does_not_mutate_source(self, repl):
        original = await repl.bind("src", "abcdefghij")
        await repl.slice("src", 0, 3, "head")
        # Source handle metadata is unchanged.
        assert repl.handles.get("src") is original
        # And its content is still readable in full.
        assert await repl.peek("src") == "abcdefghij"

    async def test_slice_content_correct(self, repl):
        await repl.bind("src", "abcdefghij")
        await repl.slice("src", 3, 7, "mid")
        # The new variable in the REPL holds the sliced bytes.
        assert await repl.peek("mid") == "defg"

    async def test_slice_unknown_source_raises(self, repl):
        with pytest.raises(RLMReplError, match="unknown handle"):
            await repl.slice("ghost", 0, 1, "out")


# ---------------------------------------------------------------------------
# exec_code
# ---------------------------------------------------------------------------


class TestExecCode:
    async def test_exec_captures_stdout(self, repl):
        out = await repl.exec_code("print('hi from repl')")
        assert out.strip() == "hi from repl"

    async def test_exec_can_define_vars(self, repl):
        await repl.exec_code("x = 41 + 1")
        out = await repl.exec_code("print(x)")
        assert out.strip() == "42"

    async def test_exec_error_raises(self, repl):
        with pytest.raises(RLMReplError):
            await repl.exec_code("raise RuntimeError('nope')")


# ---------------------------------------------------------------------------
# Snapshot / restore (ADR-058 resume)
# ---------------------------------------------------------------------------


class TestSnapshotRestore:
    async def test_snapshot_then_restore_roundtrips_handle(self, tmp_path):
        snap_path = tmp_path / "rlm.pkl"

        # Bind a handle, snapshot, shut down.
        r1 = RLMRepl()
        await r1.start()
        try:
            await r1.bind("memo", "the answer is 42\n")
            await r1.snapshot(snap_path)
        finally:
            await r1.shutdown()

        assert snap_path.exists()

        # Fresh REPL → restore → handle should be addressable again.
        r2 = RLMRepl()
        await r2.start()
        try:
            await r2.restore(snap_path)
            # Handle metadata re-hydrated.
            h = r2.handles.get("memo")
            assert h is not None
            assert h.kind == "str"
            assert h.bound_by == "restore"
            # And the value is queryable.
            content = await r2.peek("memo")
            assert content == "the answer is 42\n"
        finally:
            await r2.shutdown()


# ---------------------------------------------------------------------------
# Op timeout
# ---------------------------------------------------------------------------


class TestOpTimeout:
    async def test_slow_exec_triggers_timeout_error(self):
        # Tiny op_timeout; a busy-loop sleep inside exec_code blocks the wire
        # response until the host's wait_for trips. Use the pure-python
        # ``time.sleep`` (re imported in NS but ``time`` is via builtins).
        r = RLMRepl(op_timeout=0.1)
        await r.start()
        try:
            with pytest.raises(RLMReplError, match="timed out"):
                # 2-second sleep inside the REPL — we'll trip the 100ms timeout.
                await r.exec_code("import time; time.sleep(2)")
        finally:
            # Best-effort shutdown — proc may still be mid-sleep.
            try:
                await asyncio.wait_for(r.shutdown(), timeout=5.0)
            except asyncio.TimeoutError:
                if r._proc is not None:
                    r._proc.kill()
                    await r._proc.wait()
