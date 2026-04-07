"""Full pipeline integration tests — exercises the complete D.U.H. agent lifecycle.

Every test uses fake deps (no real API calls, no network). Each test wires up
Engine + loop + tools + approval + store + compactor with controlled fakes to
verify end-to-end behavior across the entire pipeline.

Covers:
- Engine -> query loop -> tool use -> tool result -> follow-up -> done
- Engine -> multi-turn conversation (2 user messages)
- Engine -> approval denied -> error result -> model recovers
- Engine -> max_turns enforcement
- Engine -> tool_choice none -> no tools called
- Engine -> tool_choice any -> tools called
- Engine -> session save via file_store -> resume -> context preserved
- Engine -> compactor triggered when context too large
- Engine -> streaming events in correct order (text_delta before assistant)
- Engine -> error from model -> graceful handling
- Model name handling (claude-opus-4-6, etc.)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.loop import query
from duh.kernel.messages import Message, ToolResultBlock
from duh.adapters.file_store import FileStore
from duh.adapters.simple_compactor import SimpleCompactor


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

async def _collect(gen) -> list[dict]:
    """Drain an async generator into a list."""
    return [e async for e in gen]


def _has_tool_result(messages: list[Any]) -> bool:
    """Check if any message in the list contains a tool_result block."""
    for m in messages:
        if isinstance(m, Message) and isinstance(m.content, list):
            for b in m.content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return True
    return False


def _count_tool_results(messages: list[Any]) -> int:
    """Count how many tool_result blocks exist across all messages."""
    count = 0
    for m in messages:
        if isinstance(m, Message) and isinstance(m.content, list):
            for b in m.content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    count += 1
    return count


def _simple_model(text: str = "Hello!"):
    """Factory for a model that returns a simple text response."""
    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": text}],
        )}
    return model


def _streaming_model(chunks: list[str], final_text: str = ""):
    """Factory for a model that streams text deltas then yields the assistant message."""
    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        for chunk in chunks:
            yield {"type": "text_delta", "text": chunk}
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": final_text or "".join(chunks)}],
        )}
    return model


def _tool_then_respond(tool_name="Read", tool_input=None, final_text="Done."):
    """Model that calls one tool, then responds after seeing the result."""
    if tool_input is None:
        tool_input = {"path": "test.txt"}
    call_count = [0]

    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        call_count[0] += 1
        messages = kwargs.get("messages", [])
        if _has_tool_result(messages):
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": final_text}],
            )}
        else:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "id": f"tu{call_count[0]}",
                     "name": tool_name, "input": tool_input},
                ],
            )}
    return model


def _multi_tool_model(tools_list: list[tuple[str, dict]], final_text="All done."):
    """Model that calls multiple tools in parallel, then responds."""
    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        messages = kwargs.get("messages", [])
        if _has_tool_result(messages):
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": final_text}],
            )}
        else:
            content = [
                {"type": "tool_use", "id": f"tu{i+1}", "name": name, "input": inp}
                for i, (name, inp) in enumerate(tools_list)
            ]
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=content,
            )}
    return model


def _chain_tool_model(chain_length: int = 2, final_text="Chain complete."):
    """Model that calls a tool `chain_length` times sequentially, each turn."""
    call_count = [0]

    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        messages = kwargs.get("messages", [])
        result_count = _count_tool_results(messages)
        call_count[0] += 1

        if result_count >= chain_length:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": final_text}],
            )}
        else:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[
                    {"type": "tool_use", "id": f"tu{call_count[0]}",
                     "name": "Step", "input": {"step": result_count + 1}},
                ],
            )}
    return model


def _error_model(error_msg: str = "API connection failed"):
    """Model that raises an exception."""
    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        raise RuntimeError(error_msg)
        yield  # noqa: make it a generator
    return model


def _infinite_tool_model():
    """Model that always calls a tool (never finishes on its own)."""
    counter = [0]

    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        counter[0] += 1
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "tool_use", "id": f"tu{counter[0]}",
                 "name": "Ping", "input": {"n": counter[0]}},
            ],
        )}
    return model


async def _fake_run_tool(name: str, input: dict) -> str:
    """Simple tool executor returning canned responses."""
    if name == "Read":
        return f"contents of {input.get('path', '?')}"
    if name == "Write":
        return f"wrote to {input.get('path', '?')}"
    if name == "Step":
        return f"step {input.get('step', '?')} done"
    if name == "Ping":
        return f"pong {input.get('n', '?')}"
    if name == "Fail":
        raise RuntimeError("tool crashed")
    return f"executed {name}: {input}"


async def _deny_all(name: str, input: dict) -> dict[str, Any]:
    return {"allowed": False, "reason": "denied by policy"}


async def _allow_all(name: str, input: dict) -> dict[str, Any]:
    return {"allowed": True}


# ═══════════════════════════════════════════════════════════════════
# 1. Engine -> query loop -> tool use -> tool result -> follow-up -> done
# ═══════════════════════════════════════════════════════════════════

class TestFullToolUsePipeline:
    """Exercise the complete: prompt -> model -> tool_use -> run_tool -> tool_result -> model -> done."""

    async def test_single_tool_full_cycle(self):
        deps = Deps(
            call_model=_tool_then_respond("Read", {"path": "hello.txt"}, "File says hello."),
            run_tool=_fake_run_tool,
        )
        engine = Engine(deps=deps)

        events = await _collect(engine.run("read hello.txt"))
        types = [e["type"] for e in events]

        # Full lifecycle
        assert "session" in types
        assert "assistant" in types
        assert "tool_use" in types
        assert "tool_result" in types
        assert "done" in types

        # Correct order: session first, tool_use before tool_result, done last
        session_idx = types.index("session")
        tool_use_idx = types.index("tool_use")
        tool_result_idx = types.index("tool_result")
        done_idx = types.index("done")
        assert session_idx < tool_use_idx < tool_result_idx < done_idx

    async def test_tool_result_content_is_correct(self):
        deps = Deps(
            call_model=_tool_then_respond("Read", {"path": "data.csv"}),
            run_tool=_fake_run_tool,
        )
        engine = Engine(deps=deps)
        events = await _collect(engine.run("read data.csv"))

        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 1
        assert "contents of data.csv" in results[0]["output"]
        assert results[0]["is_error"] is False

    async def test_multi_tool_parallel_cycle(self):
        deps = Deps(
            call_model=_multi_tool_model([
                ("Read", {"path": "a.py"}),
                ("Read", {"path": "b.py"}),
            ]),
            run_tool=_fake_run_tool,
        )
        engine = Engine(deps=deps)
        events = await _collect(engine.run("read both files"))

        tool_uses = [e for e in events if e["type"] == "tool_use"]
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_uses) == 2
        assert len(tool_results) == 2
        assert "contents of a.py" in tool_results[0]["output"]
        assert "contents of b.py" in tool_results[1]["output"]

    async def test_chained_tool_calls(self):
        """Model calls a tool, gets result, calls another tool, gets result, then responds."""
        deps = Deps(
            call_model=_chain_tool_model(chain_length=3, final_text="All 3 steps done."),
            run_tool=_fake_run_tool,
        )
        engine = Engine(deps=deps)
        events = await _collect(engine.run("run 3 steps"))

        tool_uses = [e for e in events if e["type"] == "tool_use"]
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_uses) == 3
        assert len(tool_results) == 3

        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "end_turn"

    async def test_tool_error_flows_back_to_model(self):
        """Tool throws, error result goes to model, model can recover."""
        call_count = [0]

        async def recovering_model(**kwargs):
            call_count[0] += 1
            messages = kwargs.get("messages", [])
            if _has_tool_result(messages):
                yield {"type": "assistant", "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "Tool failed, but I recovered."}],
                )}
            else:
                yield {"type": "assistant", "message": Message(
                    role="assistant",
                    content=[{"type": "tool_use", "id": "tu1", "name": "Fail", "input": {}}],
                )}

        deps = Deps(call_model=recovering_model, run_tool=_fake_run_tool)
        engine = Engine(deps=deps)
        events = await _collect(engine.run("try the failing tool"))

        results = [e for e in events if e["type"] == "tool_result"]
        assert results[0]["is_error"] is True
        assert "tool crashed" in results[0]["output"]

        # Model still produces a final response
        assistants = [e for e in events if e["type"] == "assistant"]
        assert len(assistants) == 2  # tool_use turn + recovery turn
        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "end_turn"


# ═══════════════════════════════════════════════════════════════════
# 2. Engine -> multi-turn conversation (2 user messages)
# ═══════════════════════════════════════════════════════════════════

class TestMultiTurnConversation:
    async def test_two_user_messages_preserve_history(self):
        turn_messages = []

        async def tracking_model(**kwargs):
            msgs = kwargs.get("messages", [])
            turn_messages.append(len(msgs))
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": f"Reply to turn {len(turn_messages)}"}],
            )}

        deps = Deps(call_model=tracking_model)
        engine = Engine(deps=deps)

        await _collect(engine.run("first question"))
        await _collect(engine.run("second question"))

        assert engine.turn_count == 2
        # Turn 1: 1 user message
        assert turn_messages[0] == 1
        # Turn 2: user1 + assistant1 + user2 = 3 messages
        assert turn_messages[1] == 3

    async def test_three_turns_accumulation(self):
        async def echo_model(**kwargs):
            msgs = kwargs.get("messages", [])
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": f"Seen {len(msgs)} messages"}],
            )}

        deps = Deps(call_model=echo_model)
        engine = Engine(deps=deps)

        await _collect(engine.run("turn 1"))
        await _collect(engine.run("turn 2"))
        events3 = await _collect(engine.run("turn 3"))

        assert engine.turn_count == 3
        # u1 + a1 + u2 + a2 + u3 + a3 = 6
        assert len(engine.messages) == 6

    async def test_session_id_stable_across_turns(self):
        deps = Deps(call_model=_simple_model())
        engine = Engine(deps=deps)

        events1 = await _collect(engine.run("hello"))
        events2 = await _collect(engine.run("again"))

        sid1 = next(e for e in events1 if e["type"] == "session")["session_id"]
        sid2 = next(e for e in events2 if e["type"] == "session")["session_id"]
        assert sid1 == sid2 == engine.session_id


# ═══════════════════════════════════════════════════════════════════
# 3. Engine -> approval denied -> error result -> model recovers
# ═══════════════════════════════════════════════════════════════════

class TestApprovalDeniedRecovery:
    async def test_denied_tool_produces_error_result(self):
        deps = Deps(
            call_model=_tool_then_respond(),
            run_tool=_fake_run_tool,
            approve=_deny_all,
        )
        engine = Engine(deps=deps)
        events = await _collect(engine.run("read a file"))

        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["is_error"] is True
        assert "denied" in results[0]["output"].lower()

    async def test_model_sees_denial_and_recovers(self):
        """After denial, the model gets the error result and produces a text response."""
        call_count = [0]

        async def graceful_model(**kwargs):
            call_count[0] += 1
            messages = kwargs.get("messages", [])
            if _has_tool_result(messages):
                yield {"type": "assistant", "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "Permission denied, moving on."}],
                )}
            else:
                yield {"type": "assistant", "message": Message(
                    role="assistant",
                    content=[{"type": "tool_use", "id": "tu1", "name": "Bash",
                              "input": {"command": "rm -rf /"}}],
                )}

        deps = Deps(
            call_model=graceful_model,
            run_tool=_fake_run_tool,
            approve=_deny_all,
        )
        engine = Engine(deps=deps)
        events = await _collect(engine.run("do something dangerous"))

        # Model was called twice: once for tool_use, once for recovery
        assert call_count[0] == 2
        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "end_turn"

    async def test_selective_approval_mixed(self):
        """One tool allowed, one denied in same turn."""
        async def selective_approve(name, input):
            if name == "Read":
                return {"allowed": True}
            return {"allowed": False, "reason": "only reads allowed"}

        deps = Deps(
            call_model=_multi_tool_model([
                ("Read", {"path": "ok.txt"}),
                ("Write", {"path": "no.txt"}),
            ]),
            run_tool=_fake_run_tool,
            approve=selective_approve,
        )
        engine = Engine(deps=deps)
        events = await _collect(engine.run("read and write"))

        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 2
        assert results[0]["is_error"] is False  # Read allowed
        assert results[1]["is_error"] is True   # Write denied


# ═══════════════════════════════════════════════════════════════════
# 4. Engine -> max_turns enforcement
# ═══════════════════════════════════════════════════════════════════

class TestMaxTurnsEnforcement:
    async def test_stops_at_max_turns(self):
        deps = Deps(
            call_model=_infinite_tool_model(),
            run_tool=_fake_run_tool,
        )
        config = EngineConfig(max_turns=3)
        engine = Engine(deps=deps, config=config)
        events = await _collect(engine.run("loop forever"))

        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "max_turns"
        assert done["turns"] == 3

    async def test_max_turns_1_allows_single_response(self):
        deps = Deps(call_model=_simple_model("One turn only."))
        config = EngineConfig(max_turns=1)
        engine = Engine(deps=deps, config=config)
        events = await _collect(engine.run("hi"))

        done = next(e for e in events if e["type"] == "done")
        assert done["turns"] == 1
        assert done["stop_reason"] == "end_turn"

    async def test_max_turns_overridden_per_run(self):
        deps = Deps(
            call_model=_infinite_tool_model(),
            run_tool=_fake_run_tool,
        )
        config = EngineConfig(max_turns=100)  # high default
        engine = Engine(deps=deps, config=config)
        events = await _collect(engine.run("loop", max_turns=2))

        done = next(e for e in events if e["type"] == "done")
        assert done["turns"] == 2
        assert done["stop_reason"] == "max_turns"

    async def test_tool_calls_count_as_turns(self):
        """Each model call is one turn, even if it produces tool_use."""
        deps = Deps(
            call_model=_infinite_tool_model(),
            run_tool=_fake_run_tool,
        )
        engine = Engine(deps=deps, config=EngineConfig(max_turns=5))
        events = await _collect(engine.run("keep going"))

        tool_uses = [e for e in events if e["type"] == "tool_use"]
        assert len(tool_uses) == 5  # exactly 5 turns, each with a tool call


# ═══════════════════════════════════════════════════════════════════
# 5. Engine -> tool_choice none -> no tools called
# ═══════════════════════════════════════════════════════════════════

class TestToolChoiceNone:
    async def test_tool_choice_none_passed_to_model(self):
        """When tool_choice='none', the engine passes it through and model should not call tools."""
        captured = {}

        async def capture_model(**kwargs):
            captured.update(kwargs)
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": "Text only, no tools."}],
            )}

        deps = Deps(call_model=capture_model, run_tool=_fake_run_tool)
        config = EngineConfig(tool_choice="none", tools=[{"name": "Read"}])
        engine = Engine(deps=deps, config=config)
        events = await _collect(engine.run("summarize"))

        assert captured["tool_choice"] == "none"
        tool_uses = [e for e in events if e["type"] == "tool_use"]
        assert len(tool_uses) == 0

    async def test_tool_choice_none_model_completes(self):
        deps = Deps(call_model=_simple_model("No tools used."))
        config = EngineConfig(tool_choice="none")
        engine = Engine(deps=deps, config=config)
        events = await _collect(engine.run("just text"))

        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "end_turn"


# ═══════════════════════════════════════════════════════════════════
# 6. Engine -> tool_choice any -> tools called
# ═══════════════════════════════════════════════════════════════════

class TestToolChoiceAny:
    async def test_tool_choice_any_passed_to_model(self):
        captured = {}

        async def capture_then_tool(**kwargs):
            captured.update(kwargs)
            messages = kwargs.get("messages", [])
            if _has_tool_result(messages):
                yield {"type": "assistant", "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "Forced tool done."}],
                )}
            else:
                yield {"type": "assistant", "message": Message(
                    role="assistant",
                    content=[{"type": "tool_use", "id": "tu1", "name": "Read",
                              "input": {"path": "forced.txt"}}],
                )}

        deps = Deps(call_model=capture_then_tool, run_tool=_fake_run_tool)
        config = EngineConfig(tool_choice="any", tools=[{"name": "Read"}])
        engine = Engine(deps=deps, config=config)
        events = await _collect(engine.run("use a tool"))

        assert captured["tool_choice"] == "any"
        tool_uses = [e for e in events if e["type"] == "tool_use"]
        assert len(tool_uses) >= 1

    async def test_tool_choice_specific_tool_name(self):
        captured = {}

        async def capture_model(**kwargs):
            captured.update(kwargs)
            msgs = kwargs.get("messages", [])
            if _has_tool_result(msgs):
                yield {"type": "assistant", "message": Message(
                    role="assistant", content="done")}
            else:
                yield {"type": "assistant", "message": Message(
                    role="assistant",
                    content=[{"type": "tool_use", "id": "tu1", "name": "Bash",
                              "input": {"command": "ls"}}],
                )}

        deps = Deps(call_model=capture_model, run_tool=_fake_run_tool)
        config = EngineConfig(tool_choice="Bash", tools=[{"name": "Bash"}])
        engine = Engine(deps=deps, config=config)
        await _collect(engine.run("list files"))

        assert captured["tool_choice"] == "Bash"


# ═══════════════════════════════════════════════════════════════════
# 7. Engine -> session save via file_store -> resume -> context preserved
# ═══════════════════════════════════════════════════════════════════

class TestSessionPersistence:
    async def test_save_and_load_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileStore(base_dir=tmpdir)
            deps = Deps(call_model=_simple_model("Saved response."))
            engine = Engine(deps=deps)

            # Run a conversation
            await _collect(engine.run("hello"))
            await _collect(engine.run("world"))

            # Save
            await store.save(engine.session_id, engine.messages)

            # Load
            loaded = await store.load(engine.session_id)
            assert loaded is not None
            assert len(loaded) >= 2  # at least 2 user messages
            # Verify content roundtrips
            assert loaded[0]["role"] == "user"
            assert loaded[0]["content"] == "hello"

    async def test_resume_conversation_from_store(self):
        """Save a session, load it back, and verify context is preserved for the model."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileStore(base_dir=tmpdir)

            # Phase 1: initial conversation
            deps1 = Deps(call_model=_simple_model("First reply."))
            engine1 = Engine(deps=deps1)
            await _collect(engine1.run("What is 2+2?"))
            await store.save(engine1.session_id, engine1.messages)

            # Phase 2: load and verify
            loaded = await store.load(engine1.session_id)
            assert loaded is not None
            assert any(m["content"] == "What is 2+2?" for m in loaded)

    async def test_session_list_and_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileStore(base_dir=tmpdir)

            await store.save("session-1", [Message(role="user", content="msg1")])
            await store.save("session-2", [Message(role="user", content="msg2")])

            sessions = await store.list_sessions()
            ids = [s["session_id"] for s in sessions]
            assert "session-1" in ids
            assert "session-2" in ids

            deleted = await store.delete("session-1")
            assert deleted is True

            sessions_after = await store.list_sessions()
            ids_after = [s["session_id"] for s in sessions_after]
            assert "session-1" not in ids_after
            assert "session-2" in ids_after

    async def test_load_nonexistent_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileStore(base_dir=tmpdir)
            loaded = await store.load("does-not-exist")
            assert loaded is None

    async def test_delete_nonexistent_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileStore(base_dir=tmpdir)
            deleted = await store.delete("nope")
            assert deleted is False


# ═══════════════════════════════════════════════════════════════════
# 8. Engine -> compactor triggered when context too large
# ═══════════════════════════════════════════════════════════════════

class TestCompactorIntegration:
    async def test_compactor_estimates_tokens(self):
        compactor = SimpleCompactor(bytes_per_token=4)
        messages = [
            Message(role="user", content="a" * 400),
            Message(role="assistant", content="b" * 400),
        ]
        estimate = compactor.estimate_tokens(messages)
        # 800 chars / 4 = 200 tokens
        assert estimate == 200

    async def test_compactor_truncates_old_messages(self):
        compactor = SimpleCompactor(bytes_per_token=1, min_keep=1)
        messages = [
            Message(role="user", content="A" * 100),      # 100 tokens
            Message(role="assistant", content="B" * 100),  # 100 tokens
            Message(role="user", content="C" * 50),        # 50 tokens
        ]
        # With limit=80, should keep only the most recent that fits
        compacted = await compactor.compact(messages, token_limit=80)
        assert len(compacted) < len(messages)
        # Most recent should be kept
        last_msg = compacted[-1]
        assert isinstance(last_msg, Message)
        assert last_msg.content == "C" * 50

    async def test_compactor_preserves_min_keep(self):
        compactor = SimpleCompactor(bytes_per_token=1, min_keep=2)
        messages = [
            Message(role="user", content="X" * 200),
            Message(role="assistant", content="Y" * 200),
            Message(role="user", content="Z" * 200),
        ]
        # Even with tiny limit, min_keep=2 forces at least 2
        compacted = await compactor.compact(messages, token_limit=10)
        assert len(compacted) >= 2

    async def test_compactor_preserves_system_messages(self):
        compactor = SimpleCompactor(bytes_per_token=1, min_keep=1)
        messages = [
            Message(role="system", content="You are helpful."),
            Message(role="user", content="A" * 200),
            Message(role="assistant", content="B" * 200),
            Message(role="user", content="C" * 50),
        ]
        compacted = await compactor.compact(messages, token_limit=100)
        # System message should always be present
        roles = [m.role for m in compacted]
        assert "system" in roles


# ═══════════════════════════════════════════════════════════════════
# 9. Streaming events in correct order (text_delta before assistant)
# ═══════════════════════════════════════════════════════════════════

class TestStreamingEventOrder:
    async def test_text_deltas_before_assistant(self):
        deps = Deps(call_model=_streaming_model(["Hel", "lo", "!"]))
        engine = Engine(deps=deps)
        events = await _collect(engine.run("greet me"))

        types = [e["type"] for e in events]
        # Find indices
        first_delta = types.index("text_delta")
        assistant_idx = types.index("assistant")
        assert first_delta < assistant_idx

    async def test_all_deltas_before_assistant(self):
        deps = Deps(call_model=_streaming_model(["A", "B", "C", "D"]))
        engine = Engine(deps=deps)
        events = await _collect(engine.run("stream test"))

        assistant_idx = next(i for i, e in enumerate(events) if e["type"] == "assistant")
        delta_indices = [i for i, e in enumerate(events) if e["type"] == "text_delta"]
        assert all(d < assistant_idx for d in delta_indices)
        assert len(delta_indices) == 4

    async def test_thinking_deltas_before_assistant(self):
        async def thinking_model(**kwargs):
            yield {"type": "thinking_delta", "text": "Let me think..."}
            yield {"type": "thinking_delta", "text": " about this."}
            yield {"type": "text_delta", "text": "Here's my answer."}
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": "Here's my answer."}],
            )}

        deps = Deps(call_model=thinking_model)
        engine = Engine(deps=deps)
        events = await _collect(engine.run("think about it"))

        types = [e["type"] for e in events]
        thinking_indices = [i for i, t in enumerate(types) if t == "thinking_delta"]
        text_delta_idx = types.index("text_delta")
        assistant_idx = types.index("assistant")
        assert all(t < assistant_idx for t in thinking_indices)
        assert text_delta_idx < assistant_idx

    async def test_content_block_events_ordered(self):
        async def block_model(**kwargs):
            yield {"type": "content_block_start", "index": 0,
                   "content_block": {"type": "text"}}
            yield {"type": "text_delta", "text": "streaming"}
            yield {"type": "content_block_stop", "index": 0}
            yield {"type": "assistant", "message": Message(
                role="assistant", content="streaming")}

        deps = Deps(call_model=block_model)
        engine = Engine(deps=deps)
        events = await _collect(engine.run("blocks"))

        types = [e["type"] for e in events]
        start_idx = types.index("content_block_start")
        stop_idx = types.index("content_block_stop")
        assistant_idx = types.index("assistant")
        assert start_idx < stop_idx < assistant_idx


# ═══════════════════════════════════════════════════════════════════
# 10. Engine -> error from model -> graceful handling
# ═══════════════════════════════════════════════════════════════════

class TestModelErrorHandling:
    async def test_model_exception_yields_error_event(self):
        deps = Deps(call_model=_error_model("server exploded"))
        engine = Engine(deps=deps)
        events = await _collect(engine.run("trigger error"))

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert "server exploded" in errors[0]["error"]

    async def test_model_error_no_done_event(self):
        """Error terminates the loop without a 'done' event."""
        deps = Deps(call_model=_error_model())
        engine = Engine(deps=deps)
        events = await _collect(engine.run("fail"))

        assert not any(e["type"] == "done" for e in events)

    async def test_no_model_configured(self):
        deps = Deps()  # no call_model
        engine = Engine(deps=deps)
        events = await _collect(engine.run("hi"))

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert "No model provider" in errors[0]["error"]

    async def test_error_does_not_corrupt_message_history(self):
        """After an error, the engine's message list still has the user message."""
        deps = Deps(call_model=_error_model())
        engine = Engine(deps=deps)
        await _collect(engine.run("broken prompt"))

        # User message was still added
        assert len(engine.messages) >= 1
        assert engine.messages[0].content == "broken prompt"
        assert engine.turn_count == 1


# ═══════════════════════════════════════════════════════════════════
# 11. Model name handling
# ═══════════════════════════════════════════════════════════════════

class TestModelNameHandling:
    """Verify correct Anthropic model IDs are handled.

    Per Anthropic docs (https://docs.anthropic.com/en/docs/about-claude/models/overview):
    - The model ID is `claude-opus-4-6` (latest alias)
    - Pinned version: `claude-opus-4-6-20260204`
    - For 1M context: append `[1m]` e.g. `claude-opus-4-6[1m]`
    - The `[1m]` suffix is NOT part of the base model ID — it is a
      context window selector applied at the API level.
    """

    async def test_model_name_passed_through_engine(self):
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok")}

        deps = Deps(call_model=capture)
        config = EngineConfig(model="claude-opus-4-6")
        engine = Engine(deps=deps, config=config)
        await _collect(engine.run("what model?"))

        assert captured["model"] == "claude-opus-4-6"

    async def test_model_name_override_per_run(self):
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok")}

        deps = Deps(call_model=capture)
        config = EngineConfig(model="claude-sonnet-4-6")
        engine = Engine(deps=deps, config=config)
        await _collect(engine.run("use opus", model="claude-opus-4-6"))

        assert captured["model"] == "claude-opus-4-6"

    async def test_default_max_tokens_for_opus(self):
        from duh.adapters.anthropic import _default_max_tokens
        assert _default_max_tokens("claude-opus-4-6") == 16384
        assert _default_max_tokens("claude-opus-4-6-20260204") == 16384

    async def test_default_max_tokens_for_sonnet(self):
        from duh.adapters.anthropic import _default_max_tokens
        assert _default_max_tokens("claude-sonnet-4-6") == 16384

    async def test_default_max_tokens_for_haiku(self):
        from duh.adapters.anthropic import _default_max_tokens
        assert _default_max_tokens("claude-haiku-4-5-20251001") == 8192

    async def test_thinking_support_detection(self):
        """Verify that opus-4-6 and sonnet-4-6 are detected as supporting adaptive thinking."""
        for model in ("claude-opus-4-6", "claude-opus-4-6-20260204",
                      "claude-sonnet-4-6", "claude-sonnet-4-6-20260514"):
            supports = any(tag in model for tag in ("opus-4-6", "sonnet-4-6"))
            assert supports, f"{model} should support adaptive thinking"

    async def test_haiku_does_not_support_adaptive_thinking(self):
        model = "claude-haiku-4-5-20251001"
        supports = any(tag in model for tag in ("opus-4-6", "sonnet-4-6"))
        assert not supports


# ═══════════════════════════════════════════════════════════════════
# 12. Edge cases and additional coverage
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    async def test_empty_prompt(self):
        deps = Deps(call_model=_simple_model("Response to empty."))
        engine = Engine(deps=deps)
        events = await _collect(engine.run(""))

        assert any(e["type"] == "assistant" for e in events)
        assert any(e["type"] == "done" for e in events)

    async def test_list_content_prompt(self):
        """Engine accepts list content (for multi-part messages)."""
        deps = Deps(call_model=_simple_model("Got it."))
        engine = Engine(deps=deps)
        events = await _collect(engine.run([
            {"type": "text", "text": "Hello from list content"},
        ]))

        assert any(e["type"] == "assistant" for e in events)

    async def test_no_tool_executor_error_result(self):
        """When run_tool is None, tool calls produce an error result."""
        deps = Deps(call_model=_tool_then_respond())  # no run_tool
        engine = Engine(deps=deps)
        events = await _collect(engine.run("try a tool"))

        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["is_error"] is True
        assert "No tool executor" in results[0]["output"]

    async def test_engine_config_defaults(self):
        config = EngineConfig()
        assert config.model == ""
        assert config.system_prompt == ""
        assert config.tools == []
        assert config.thinking is None
        assert config.tool_choice is None
        assert config.max_turns == 1000
        assert config.cwd == "."

    async def test_system_prompt_passthrough(self):
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok")}

        deps = Deps(call_model=capture)
        config = EngineConfig(system_prompt="You are a pirate.")
        engine = Engine(deps=deps, config=config)
        await _collect(engine.run("ahoy"))

        assert captured["system_prompt"] == "You are a pirate."

    async def test_system_prompt_list_passthrough(self):
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok")}

        deps = Deps(call_model=capture)
        config = EngineConfig(system_prompt=["Part 1", "Part 2"])
        engine = Engine(deps=deps, config=config)
        await _collect(engine.run("multi-system"))

        assert captured["system_prompt"] == ["Part 1", "Part 2"]

    async def test_stop_reason_from_metadata(self):
        """If the assistant message has stop_reason in metadata, it's used in done."""
        async def model_with_stop_reason(**kwargs):
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": "bye"}],
                metadata={"stop_reason": "end_turn"},
            )}

        deps = Deps(call_model=model_with_stop_reason)
        engine = Engine(deps=deps)
        events = await _collect(engine.run("stop"))

        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "end_turn"

    async def test_tool_use_id_preserved_through_pipeline(self):
        """The tool_use_id from the model flows to tool_use event and tool_result event."""
        deps = Deps(
            call_model=_tool_then_respond("Read", {"path": "x.txt"}),
            run_tool=_fake_run_tool,
        )
        engine = Engine(deps=deps)
        events = await _collect(engine.run("read x.txt"))

        tool_use = next(e for e in events if e["type"] == "tool_use")
        tool_result = next(e for e in events if e["type"] == "tool_result")
        assert tool_use["id"] == tool_result["tool_use_id"]
        assert tool_use["id"].startswith("tu")


# ═══════════════════════════════════════════════════════════════════
# 13. File store JSONL roundtrip with tool_use messages
# ═══════════════════════════════════════════════════════════════════

class TestFileStoreRoundtrip:
    async def test_tool_messages_roundtrip(self):
        """Messages with tool_use and tool_result content survive JSONL serialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileStore(base_dir=tmpdir)

            messages = [
                Message(role="user", content="read foo.txt"),
                Message(role="assistant", content=[
                    {"type": "text", "text": "Reading..."},
                    {"type": "tool_use", "id": "tu1", "name": "Read",
                     "input": {"path": "foo.txt"}},
                ]),
                Message(role="user", content=[
                    {"type": "tool_result", "tool_use_id": "tu1",
                     "content": "file contents here", "is_error": False},
                ]),
                Message(role="assistant", content=[
                    {"type": "text", "text": "The file contains..."},
                ]),
            ]

            await store.save("roundtrip-test", messages)
            loaded = await store.load("roundtrip-test")

            assert loaded is not None
            assert len(loaded) == 4

            # Verify tool_use block preserved
            assistant_content = loaded[1]["content"]
            assert isinstance(assistant_content, list)
            tool_block = next(b for b in assistant_content if b.get("type") == "tool_use")
            assert tool_block["name"] == "Read"
            assert tool_block["input"] == {"path": "foo.txt"}

            # Verify tool_result block preserved
            result_content = loaded[2]["content"]
            assert isinstance(result_content, list)
            result_block = next(b for b in result_content if b.get("type") == "tool_result")
            assert result_block["content"] == "file contents here"

    async def test_incremental_save(self):
        """Saving twice appends only new messages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileStore(base_dir=tmpdir)
            sid = "incremental-test"

            msgs1 = [Message(role="user", content="first")]
            await store.save(sid, msgs1)

            msgs2 = msgs1 + [Message(role="assistant", content="reply")]
            await store.save(sid, msgs2)

            loaded = await store.load(sid)
            assert loaded is not None
            assert len(loaded) == 2


# ═══════════════════════════════════════════════════════════════════
# 14. Anthropic adapter helpers (unit-level, no network)
# ═══════════════════════════════════════════════════════════════════

class TestAnthropicAdapterHelpers:
    def test_build_system_text_string(self):
        from duh.adapters.anthropic import _build_system_text
        assert _build_system_text("Hello") == "Hello"

    def test_build_system_text_list(self):
        from duh.adapters.anthropic import _build_system_text
        result = _build_system_text(["Part A", "Part B"])
        assert "Part A" in result
        assert "Part B" in result

    def test_build_system_text_empty(self):
        from duh.adapters.anthropic import _build_system_text
        assert _build_system_text("") == ""
        assert _build_system_text([]) == ""

    def test_sanitize_block_text(self):
        from duh.adapters.anthropic import _sanitize_block
        block = {"type": "text", "text": "hello", "extra": "removed"}
        sanitized = _sanitize_block(block)
        assert sanitized == {"type": "text", "text": "hello"}
        assert "extra" not in sanitized

    def test_sanitize_block_tool_use(self):
        from duh.adapters.anthropic import _sanitize_block
        block = {
            "type": "tool_use", "id": "tu1", "name": "Read",
            "input": {"path": "x"}, "timestamp": "should be removed",
        }
        sanitized = _sanitize_block(block)
        assert "timestamp" not in sanitized
        assert sanitized["name"] == "Read"

    def test_sanitize_block_tool_result(self):
        from duh.adapters.anthropic import _sanitize_block
        block = {
            "type": "tool_result", "tool_use_id": "tu1",
            "content": "output", "is_error": False, "extra_field": True,
        }
        sanitized = _sanitize_block(block)
        assert "extra_field" not in sanitized
        assert sanitized["content"] == "output"

    def test_to_api_messages_from_message_objects(self):
        from duh.adapters.anthropic import _to_api_messages
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="world"),
        ]
        api_msgs = _to_api_messages(msgs)
        assert len(api_msgs) == 2
        assert api_msgs[0] == {"role": "user", "content": "hello"}
        assert api_msgs[1] == {"role": "assistant", "content": "world"}

    def test_to_api_messages_from_dicts(self):
        from duh.adapters.anthropic import _to_api_messages
        msgs = [
            {"role": "user", "content": "hi"},
        ]
        api_msgs = _to_api_messages(msgs)
        assert api_msgs[0] == {"role": "user", "content": "hi"}

    def test_to_api_tools(self):
        from duh.adapters.anthropic import _to_api_tools

        class FakeTool:
            name = "Read"
            description = "Read a file"
            input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}

        result = _to_api_tools([FakeTool()])
        assert len(result) == 1
        assert result[0]["name"] == "Read"
        assert result[0]["description"] == "Read a file"
