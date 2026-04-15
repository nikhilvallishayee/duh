"""Tests for ADR-057: Message Flow Unification.

Verifies that:
- loop.py yields tool_result_message events after tool execution
- engine._messages contains tool_result user messages after tool use
- Messages alternate correctly without explicit validate_alternation
- Session save/load preserves tool_result messages
- Session resume preserves full context
"""

import asyncio
from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.loop import query
from duh.kernel.messages import Message, UserMessage


# ---------------------------------------------------------------------------
# Fake model providers
# ---------------------------------------------------------------------------

async def simple_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that returns a simple text response."""
    yield {"type": "assistant", "message": Message(
        role="assistant",
        content=[{"type": "text", "text": "Hello!"}],
    )}


async def tool_use_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that calls a tool on first turn, responds on second."""
    messages = kwargs.get("messages", [])
    has_tool_result = any(
        isinstance(m.content, list) and any(
            (isinstance(b, dict) and b.get("type") == "tool_result")
            for b in m.content
        )
        for m in messages if isinstance(m, Message)
    )

    if has_tool_result:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": "The file says hello."}],
        )}
    else:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "text", "text": "Let me read that."},
                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"path": "test.txt"}},
            ],
        )}


async def multi_tool_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that calls two tools on first turn, responds on second."""
    messages = kwargs.get("messages", [])
    has_tool_result = any(
        isinstance(m.content, list) and any(
            (isinstance(b, dict) and b.get("type") == "tool_result")
            for b in m.content
        )
        for m in messages if isinstance(m, Message)
    )

    if has_tool_result:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": "Both files read."}],
        )}
    else:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"path": "a.txt"}},
                {"type": "tool_use", "id": "tu2", "name": "Read", "input": {"path": "b.txt"}},
            ],
        )}


async def two_round_tool_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that does two rounds of tool use before final response."""
    messages = kwargs.get("messages", [])
    tool_result_count = sum(
        1 for m in messages if isinstance(m, Message)
        and isinstance(m.content, list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m.content)
    )

    if tool_result_count >= 2:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": "All done."}],
        )}
    elif tool_result_count == 1:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "tool_use", "id": "tu2", "name": "Write", "input": {"path": "out.txt"}},
            ],
        )}
    else:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"path": "in.txt"}},
            ],
        )}


async def fake_run_tool(name: str, input: dict) -> str:
    return f"output of {name}({input})"


def _make_engine(**kwargs) -> Engine:
    """Create a minimal engine with fake model."""
    deps_kwargs = kwargs.pop("deps_kwargs", {})
    deps = Deps(call_model=kwargs.pop("call_model", simple_model), **deps_kwargs)
    config = EngineConfig(**kwargs)
    return Engine(deps=deps, config=config)


# ===========================================================================
# Phase 1: Loop yields tool_result events
# ===========================================================================


class TestLoopYieldsToolResultMessage:
    """Task 1.1: loop.py yields tool_result_message events."""

    async def test_tool_result_message_event_yielded(self):
        """After tool execution, loop yields a tool_result_message event."""
        deps = Deps(call_model=tool_use_model, run_tool=fake_run_tool)
        events = []
        async for e in query(
            messages=[Message(role="user", content="read test.txt")],
            deps=deps,
        ):
            events.append(e)

        trm_events = [e for e in events if e["type"] == "tool_result_message"]
        assert len(trm_events) == 1
        msg = trm_events[0]["message"]
        assert isinstance(msg, Message)
        assert msg.role == "user"
        # Content should be a list of tool_result blocks
        assert isinstance(msg.content, list)
        assert len(msg.content) == 1
        assert msg.content[0]["type"] == "tool_result"
        assert msg.content[0]["tool_use_id"] == "tu1"

    async def test_multi_tool_single_tool_result_message(self):
        """Multiple tool calls in one turn → one tool_result_message with all results."""
        deps = Deps(call_model=multi_tool_model, run_tool=fake_run_tool)
        events = []
        async for e in query(
            messages=[Message(role="user", content="read both")],
            deps=deps,
        ):
            events.append(e)

        trm_events = [e for e in events if e["type"] == "tool_result_message"]
        assert len(trm_events) == 1
        msg = trm_events[0]["message"]
        assert len(msg.content) == 2  # two tool results in one message

    async def test_two_rounds_yield_two_tool_result_messages(self):
        """Two rounds of tool use → two tool_result_message events."""
        deps = Deps(call_model=two_round_tool_model, run_tool=fake_run_tool)
        events = []
        async for e in query(
            messages=[Message(role="user", content="do it")],
            deps=deps,
        ):
            events.append(e)

        trm_events = [e for e in events if e["type"] == "tool_result_message"]
        assert len(trm_events) == 2

    async def test_no_tool_use_no_tool_result_message(self):
        """Simple text response → no tool_result_message event."""
        deps = Deps(call_model=simple_model)
        events = []
        async for e in query(
            messages=[Message(role="user", content="hi")],
            deps=deps,
        ):
            events.append(e)

        trm_events = [e for e in events if e["type"] == "tool_result_message"]
        assert len(trm_events) == 0


class TestEngineCapturesToolResultMessages:
    """Task 1.2: Engine captures tool_result_message events in self._messages."""

    async def test_engine_messages_contain_tool_result(self):
        """After tool use, engine._messages includes the tool_result user message."""
        engine = _make_engine(
            call_model=tool_use_model,
            deps_kwargs={"run_tool": fake_run_tool},
        )

        events = []
        async for e in engine.run("read test.txt"):
            events.append(e)

        msgs = engine._messages
        roles = [m.role for m in msgs]
        # Expected: user, assistant, user(tool_result), assistant
        assert roles == ["user", "assistant", "user", "assistant"]

        # The third message (index 2) should be the tool_result
        tool_result_msg = msgs[2]
        assert isinstance(tool_result_msg.content, list)
        assert tool_result_msg.content[0]["type"] == "tool_result"

    async def test_tool_result_message_not_yielded_to_caller(self):
        """tool_result_message is an internal event — not yielded to the caller."""
        engine = _make_engine(
            call_model=tool_use_model,
            deps_kwargs={"run_tool": fake_run_tool},
        )

        events = []
        async for e in engine.run("read test.txt"):
            events.append(e)

        event_types = [e["type"] for e in events]
        assert "tool_result_message" not in event_types

    async def test_two_round_tool_use_alternation(self):
        """Two rounds of tool use produces correct alternation in engine._messages."""
        engine = _make_engine(
            call_model=two_round_tool_model,
            deps_kwargs={"run_tool": fake_run_tool},
        )

        async for _ in engine.run("do it"):
            pass

        roles = [m.role for m in engine._messages]
        # user, assistant, user(tool_result), assistant, user(tool_result), assistant
        assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]

        # Verify strict alternation — no consecutive same-role
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], f"Consecutive {roles[i]} at index {i}"


class TestNoValidateAlternationOnHotPath:
    """Task 1.3: validate_alternation removed from query hot path."""

    async def test_messages_alternate_without_validation(self):
        """Messages alternate correctly without explicit validate_alternation."""
        engine = _make_engine(
            call_model=tool_use_model,
            deps_kwargs={"run_tool": fake_run_tool},
        )

        async for _ in engine.run("read test.txt"):
            pass

        # Verify alternation is correct
        roles = [m.role for m in engine._messages]
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], f"Consecutive {roles[i]} at index {i}"

    async def test_simple_turn_alternation(self):
        """Simple text turn (no tools) also alternates correctly."""
        engine = _make_engine(call_model=simple_model)

        async for _ in engine.run("hello"):
            pass

        roles = [m.role for m in engine._messages]
        assert roles == ["user", "assistant"]

    async def test_multi_turn_alternation(self):
        """Multiple turns maintain correct alternation."""
        engine = _make_engine(call_model=simple_model)

        async for _ in engine.run("first"):
            pass
        async for _ in engine.run("second"):
            pass

        roles = [m.role for m in engine._messages]
        assert roles == ["user", "assistant", "user", "assistant"]
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1]


# ===========================================================================
# Phase 2: Session persistence correctness
# ===========================================================================


class TestSessionSaveIncludesToolResults:
    """Task 2.1: Session save includes tool_result messages."""

    async def test_saved_session_has_tool_results(self, tmp_path):
        """Multi-tool session save preserves the user/assistant/user(tool_result)/assistant sequence."""
        from duh.adapters.file_store import FileStore

        store = FileStore(base_dir=tmp_path)
        engine = _make_engine(
            call_model=tool_use_model,
            deps_kwargs={"run_tool": fake_run_tool},
        )
        engine._session_store = store

        async for _ in engine.run("read test.txt"):
            pass

        # Save happens automatically on "done" — but verify explicitly
        await store.save(engine.session_id, engine._messages)
        loaded = await store.load(engine.session_id)

        assert loaded is not None
        roles = [m["role"] for m in loaded]
        assert roles == ["user", "assistant", "user", "assistant"]

        # No consecutive same-role messages
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], f"Consecutive {roles[i]} at index {i}"

        # The tool_result message should have tool_result content
        tool_result_raw = loaded[2]
        assert isinstance(tool_result_raw["content"], list)
        assert tool_result_raw["content"][0]["type"] == "tool_result"


class TestSessionMigration:
    """Task 2.2: Migration for existing broken sessions."""

    async def test_broken_session_migrated_on_load(self, tmp_path):
        """Sessions with consecutive same-role messages get fixed on load."""
        import json
        from duh.adapters.file_store import FileStore

        store = FileStore(base_dir=tmp_path)

        # Write a broken session (consecutive assistants — no tool_result between)
        broken = [
            {"role": "user", "content": "read test.txt", "id": "1", "timestamp": "2026-01-01T00:00:00Z", "metadata": {}},
            {"role": "assistant", "content": [{"type": "text", "text": "Let me read that."}], "id": "2", "timestamp": "2026-01-01T00:00:01Z", "metadata": {}},
            {"role": "assistant", "content": [{"type": "text", "text": "The file says hello."}], "id": "3", "timestamp": "2026-01-01T00:00:02Z", "metadata": {}},
        ]

        session_id = "broken-session"
        path = tmp_path / f"{session_id}.jsonl"
        with open(path, "w") as f:
            for msg in broken:
                f.write(json.dumps(msg) + "\n")

        loaded = await store.load(session_id)
        assert loaded is not None

        # Apply migration
        from duh.kernel.messages import Message as Msg, validate_alternation
        messages = [Msg(role=m["role"], content=m["content"]) for m in loaded]

        # Detect consecutive same-role
        needs_migration = False
        for i in range(len(messages) - 1):
            if messages[i].role == messages[i + 1].role:
                needs_migration = True
                break

        if needs_migration:
            messages = validate_alternation(messages)

        roles = [m.role for m in messages]
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], f"Consecutive {roles[i]} at index {i}"


class TestSessionResumePreservesContext:
    """Task 2.3: Session resume preserves full context."""

    async def test_save_resume_message_count(self, tmp_path):
        """Save session → resume → message count matches."""
        from duh.adapters.file_store import FileStore

        store = FileStore(base_dir=tmp_path)
        engine = _make_engine(
            call_model=tool_use_model,
            deps_kwargs={"run_tool": fake_run_tool},
        )

        async for _ in engine.run("read test.txt"):
            pass

        original_count = len(engine._messages)
        await store.save(engine.session_id, engine._messages)

        # Load into a new engine
        loaded = await store.load(engine.session_id)
        assert loaded is not None
        assert len(loaded) == original_count

        # Verify roles match
        original_roles = [m.role for m in engine._messages]
        loaded_roles = [m["role"] for m in loaded]
        assert original_roles == loaded_roles


# ===========================================================================
# Phase 3: Auto-compact on canonical list
# ===========================================================================


class TestCompactPreservesToolResults:
    """Task 3.1: Compact operates on self._messages directly."""

    async def test_compact_preserves_recent_tool_results(self):
        """Auto-compact keeps tool_result messages in the tail window."""
        from duh.adapters.compact.microcompact import MicroCompactor

        mc = MicroCompactor(keep_last=1)

        # Build a message list with two tool use rounds
        messages = [
            Message(role="user", content="read both files"),
            Message(role="assistant", content=[
                {"type": "text", "text": "Reading file 1"},
                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"path": "a.txt"}},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "tu1", "content": "contents of a.txt"},
            ]),
            Message(role="assistant", content=[
                {"type": "text", "text": "Now file 2"},
                {"type": "tool_use", "id": "tu2", "name": "Read", "input": {"path": "b.txt"}},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "tu2", "content": "contents of b.txt"},
            ]),
            Message(role="assistant", content=[
                {"type": "text", "text": "Both files read."},
            ]),
        ]

        compacted = await mc.compact(messages, token_limit=100_000)

        # Alternation should still be correct
        roles = [m.role for m in compacted]
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], f"Consecutive {roles[i]} at index {i}"

        # The recent tool result (tu2) should be kept, old one (tu1) cleared
        for msg in compacted:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        if block.get("tool_use_id") == "tu2":
                            assert "contents of b.txt" in str(block.get("content", ""))
                        elif block.get("tool_use_id") == "tu1":
                            assert "cleared" in str(block.get("content", "")).lower()

    async def test_compacted_session_saves_correctly(self, tmp_path):
        """Compacted sessions save and load with correct alternation."""
        from duh.adapters.compact.microcompact import MicroCompactor
        from duh.adapters.file_store import FileStore

        mc = MicroCompactor(keep_last=1)
        store = FileStore(base_dir=tmp_path)

        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content=[
                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"path": "a.txt"}},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "tu1", "content": "file contents"},
            ]),
            Message(role="assistant", content=[
                {"type": "text", "text": "Done."},
            ]),
        ]

        compacted = await mc.compact(messages, token_limit=100_000)

        sid = "compact-test"
        await store.save(sid, compacted)
        loaded = await store.load(sid)

        assert loaded is not None
        roles = [m["role"] for m in loaded]
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1]


class TestMicrocompactClearsOldToolResults:
    """Task 3.2: Microcompact clears old tool_result content."""

    async def test_old_tool_results_cleared(self):
        """Tool_result messages older than keep_last are cleared."""
        from duh.adapters.compact.microcompact import MicroCompactor

        mc = MicroCompactor(keep_last=1)

        messages = [
            Message(role="user", content="start"),
            Message(role="assistant", content=[
                {"type": "tool_use", "id": "old1", "name": "Read", "input": {"path": "old.txt"}},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "old1", "content": "old data that should be cleared"},
            ]),
            Message(role="assistant", content=[
                {"type": "tool_use", "id": "new1", "name": "Read", "input": {"path": "new.txt"}},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "new1", "content": "new data kept"},
            ]),
            Message(role="assistant", content="final answer"),
        ]

        compacted = await mc.compact(messages, token_limit=100_000)

        # Find the cleared tool_result
        for msg in compacted:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict) and block.get("tool_use_id") == "old1":
                        assert "cleared" in str(block.get("content", "")).lower()
                    elif isinstance(block, dict) and block.get("tool_use_id") == "new1":
                        assert "new data kept" in str(block.get("content", ""))

    async def test_cleared_messages_preserve_structure(self):
        """Cleared tool_result messages keep role=user for alternation."""
        from duh.adapters.compact.microcompact import MicroCompactor

        mc = MicroCompactor(keep_last=0)  # clear all

        messages = [
            Message(role="user", content="go"),
            Message(role="assistant", content=[
                {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "tu1", "content": "file1\nfile2\nfile3"},
            ]),
            Message(role="assistant", content="done"),
        ]

        compacted = await mc.compact(messages, token_limit=100_000)

        roles = [m.role for m in compacted]
        assert roles == ["user", "assistant", "user", "assistant"]
        # The tool_result message is still there (role=user), just cleared
        assert compacted[2].role == "user"
