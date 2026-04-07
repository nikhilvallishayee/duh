"""Tests for duh.adapters.file_store — JSONL session persistence."""

import asyncio
import json
from pathlib import Path
from dataclasses import asdict

from duh.adapters.file_store import FileStore
from duh.kernel.messages import Message, TextBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_msg(role: str = "user", content: str = "hello") -> Message:
    return Message(role=role, content=content, id="msg-1", timestamp="2025-01-01T00:00:00+00:00")


def _read_lines(path: Path) -> list[str]:
    """Return non-empty lines from a file."""
    return [l for l in path.read_text("utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# Save / Load round-trip
# ---------------------------------------------------------------------------

class TestSaveLoad:
    async def test_round_trip_single_message(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        msg = _make_msg()
        await store.save("s1", [msg])
        loaded = await store.load("s1")
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["role"] == "user"
        assert loaded[0]["content"] == "hello"

    async def test_round_trip_multiple_messages(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        msgs = [
            Message(role="user", content="hi", id="m1", timestamp="t1"),
            Message(role="assistant", content="hey", id="m2", timestamp="t2"),
        ]
        await store.save("s1", msgs)
        loaded = await store.load("s1")
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["role"] == "user"
        assert loaded[1]["role"] == "assistant"

    async def test_round_trip_with_content_blocks(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        msg = Message(
            role="assistant",
            content=[
                TextBlock(text="Checking file"),
                ToolUseBlock(id="tu1", name="Read", input={"path": "/tmp/x"}),
            ],
            id="m1",
            timestamp="t1",
        )
        await store.save("s1", [msg])
        loaded = await store.load("s1")
        assert loaded is not None
        assert len(loaded) == 1
        blocks = loaded[0]["content"]
        assert isinstance(blocks, list)
        assert blocks[0]["text"] == "Checking file"
        assert blocks[1]["name"] == "Read"

    async def test_round_trip_dict_messages(self, tmp_path: Path):
        """Plain dicts (not Message objects) should also round-trip."""
        store = FileStore(base_dir=tmp_path)
        raw = [{"role": "user", "content": "raw dict"}]
        await store.save("s1", raw)
        loaded = await store.load("s1")
        assert loaded == raw

    async def test_load_returns_dicts_not_messages(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        await store.save("s1", [_make_msg()])
        loaded = await store.load("s1")
        assert loaded is not None
        assert isinstance(loaded[0], dict)


# ---------------------------------------------------------------------------
# Append semantics
# ---------------------------------------------------------------------------

class TestAppend:
    async def test_save_appends_not_overwrites(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        m1 = Message(role="user", content="first", id="m1", timestamp="t1")
        m2 = Message(role="assistant", content="second", id="m2", timestamp="t2")

        await store.save("s1", [m1])
        await store.save("s1", [m1, m2])  # full list, only m2 is new

        loaded = await store.load("s1")
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["content"] == "first"
        assert loaded[1]["content"] == "second"

    async def test_save_idempotent_when_no_new(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        msgs = [_make_msg()]
        await store.save("s1", msgs)
        await store.save("s1", msgs)  # same list again

        lines = _read_lines(tmp_path / "s1.jsonl")
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# Load missing
# ---------------------------------------------------------------------------

class TestLoadMissing:
    async def test_load_nonexistent_returns_none(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        assert await store.load("no-such-session") is None


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    async def test_list_empty(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        assert await store.list_sessions() == []

    async def test_list_empty_dir_not_created(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path / "nonexistent")
        assert await store.list_sessions() == []

    async def test_list_multiple(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        await store.save("aaa", [_make_msg()])
        await store.save("bbb", [_make_msg(), _make_msg(content="two")])

        sessions = await store.list_sessions()
        ids = {s["session_id"] for s in sessions}
        assert ids == {"aaa", "bbb"}

        for s in sessions:
            assert "created" in s
            assert "modified" in s
            assert "message_count" in s

        counts = {s["session_id"]: s["message_count"] for s in sessions}
        assert counts["aaa"] == 1
        assert counts["bbb"] == 2

    async def test_list_ignores_non_jsonl(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        await store.save("real", [_make_msg()])
        (tmp_path / "noise.txt").write_text("ignore me")
        sessions = await store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "real"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    async def test_delete_existing(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        await store.save("s1", [_make_msg()])
        assert await store.delete("s1") is True
        assert await store.load("s1") is None

    async def test_delete_nonexistent(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        assert await store.delete("ghost") is False


# ---------------------------------------------------------------------------
# JSONL format verification
# ---------------------------------------------------------------------------

class TestJSONLFormat:
    async def test_one_message_per_line(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        msgs = [
            Message(role="user", content="a", id="m1", timestamp="t1"),
            Message(role="assistant", content="b", id="m2", timestamp="t2"),
        ]
        await store.save("s1", msgs)

        lines = _read_lines(tmp_path / "s1.jsonl")
        assert len(lines) == 2

    async def test_each_line_valid_json(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        msgs = [
            Message(role="user", content="x", id="m1", timestamp="t1"),
            Message(role="assistant", content="y", id="m2", timestamp="t2"),
            Message(role="user", content="z", id="m3", timestamp="t3"),
        ]
        await store.save("s1", msgs)

        lines = _read_lines(tmp_path / "s1.jsonl")
        for line in lines:
            obj = json.loads(line)
            assert "role" in obj
            assert "content" in obj

    async def test_no_trailing_newline_issues(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        await store.save("s1", [_make_msg()])
        raw = (tmp_path / "s1.jsonl").read_text("utf-8")
        # Should end with exactly one newline
        assert raw.endswith("\n")
        assert not raw.endswith("\n\n")


# ---------------------------------------------------------------------------
# Unicode
# ---------------------------------------------------------------------------

class TestUnicode:
    async def test_unicode_content_preserved(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        content = "Hello \u4e16\u754c \U0001f600 caf\u00e9 \u2603"
        msg = Message(role="user", content=content, id="m1", timestamp="t1")
        await store.save("s1", [msg])
        loaded = await store.load("s1")
        assert loaded is not None
        assert loaded[0]["content"] == content

    async def test_unicode_in_metadata(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        msg = Message(
            role="user", content="hi", id="m1", timestamp="t1",
            metadata={"note": "\u65e5\u672c\u8a9e\u30c6\u30b9\u30c8"},
        )
        await store.save("s1", [msg])
        loaded = await store.load("s1")
        assert loaded is not None
        assert loaded[0]["metadata"]["note"] == "\u65e5\u672c\u8a9e\u30c6\u30b9\u30c8"


# ---------------------------------------------------------------------------
# Empty sessions
# ---------------------------------------------------------------------------

class TestEmptySession:
    async def test_save_empty_list(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        await store.save("s1", [])
        # File should not be created for empty save
        assert not (tmp_path / "s1.jsonl").exists()

    async def test_load_empty_file(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        (tmp_path / "s1.jsonl").write_text("")
        loaded = await store.load("s1")
        assert loaded == []


# ---------------------------------------------------------------------------
# Large messages
# ---------------------------------------------------------------------------

class TestLargeMessages:
    async def test_large_content(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        big_text = "x" * 1_000_000  # 1 MB
        msg = Message(role="assistant", content=big_text, id="m1", timestamp="t1")
        await store.save("s1", [msg])
        loaded = await store.load("s1")
        assert loaded is not None
        assert loaded[0]["content"] == big_text

    async def test_many_messages(self, tmp_path: Path):
        store = FileStore(base_dir=tmp_path)
        msgs = [
            Message(role="user", content=f"msg-{i}", id=f"m{i}", timestamp=f"t{i}")
            for i in range(500)
        ]
        await store.save("s1", msgs)
        loaded = await store.load("s1")
        assert loaded is not None
        assert len(loaded) == 500
        assert loaded[0]["content"] == "msg-0"
        assert loaded[499]["content"] == "msg-499"


# ---------------------------------------------------------------------------
# Concurrent save safety
# ---------------------------------------------------------------------------

class TestConcurrency:
    async def test_concurrent_saves_no_corruption(self, tmp_path: Path):
        """Multiple concurrent saves to *different* sessions must not interfere."""
        store = FileStore(base_dir=tmp_path)

        async def save_session(sid: str):
            msgs = [Message(role="user", content=f"hello from {sid}", id=sid, timestamp="t")]
            await store.save(sid, msgs)

        await asyncio.gather(*(save_session(f"s{i}") for i in range(20)))

        sessions = await store.list_sessions()
        assert len(sessions) == 20

        for i in range(20):
            loaded = await store.load(f"s{i}")
            assert loaded is not None
            assert loaded[0]["content"] == f"hello from s{i}"

    async def test_atomic_write_leaves_valid_file(self, tmp_path: Path):
        """After save, the file should always be valid JSONL."""
        store = FileStore(base_dir=tmp_path)
        msgs = [
            Message(role="user", content="one", id="m1", timestamp="t1"),
            Message(role="assistant", content="two", id="m2", timestamp="t2"),
        ]
        await store.save("s1", msgs)

        # Verify every line parses
        lines = _read_lines(tmp_path / "s1.jsonl")
        for line in lines:
            json.loads(line)  # should not raise
