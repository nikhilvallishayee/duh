"""Exhaustive tests for duh.kernel.loop — every branch, every edge case."""

from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.loop import query
from duh.kernel.messages import Message


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

async def _collect(gen) -> list[dict]:
    return [e async for e in gen]


def _simple_model_fn(text: str = "Hello!"):
    async def model(**kwargs) -> AsyncGenerator[dict, None]:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": text}],
        )}
    return model


def _tool_model_fn(tool_name="Read", tool_input=None, response_after="Done."):
    """Model that calls a tool, then responds after seeing the result."""
    if tool_input is None:
        tool_input = {"path": "test.txt"}
    call_count = [0]

    async def model(**kwargs) -> AsyncGenerator[dict, None]:
        call_count[0] += 1
        messages = kwargs.get("messages", [])
        has_result = any(
            isinstance(m.content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in m.content
            )
            for m in messages if isinstance(m, Message)
        )
        if has_result:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": response_after}],
            )}
        else:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "tool_use", "id": f"tu{call_count[0]}",
                          "name": tool_name, "input": tool_input}],
            )}
    return model


async def _echo_tool(name: str, input: dict) -> str:
    return f"executed {name}: {input}"


# ═══════════════════════════════════════════════════════════════════
# No model configured
# ═══════════════════════════════════════════════════════════════════

class TestNoModelConfigured:
    async def test_yields_error(self):
        events = await _collect(query(messages=[], deps=Deps()))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "No model provider" in events[0]["error"]

    async def test_no_done_event(self):
        events = await _collect(query(messages=[], deps=Deps()))
        assert not any(e["type"] == "done" for e in events)


# ═══════════════════════════════════════════════════════════════════
# Simple text response (no tool use)
# ═══════════════════════════════════════════════════════════════════

class TestSimpleResponse:
    async def test_yields_assistant_and_done(self):
        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=_simple_model_fn()),
        ))
        types = [e["type"] for e in events]
        assert "assistant" in types
        assert "done" in types

    async def test_done_has_end_turn(self):
        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=_simple_model_fn()),
        ))
        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "end_turn"
        assert done["turns"] == 1

    async def test_empty_messages_list(self):
        events = await _collect(query(
            messages=[],
            deps=Deps(call_model=_simple_model_fn()),
        ))
        assert any(e["type"] == "assistant" for e in events)

    async def test_multiple_user_messages(self):
        events = await _collect(query(
            messages=[
                Message(role="user", content="first"),
                Message(role="assistant", content="ok"),
                Message(role="user", content="second"),
            ],
            deps=Deps(call_model=_simple_model_fn()),
        ))
        assert any(e["type"] == "assistant" for e in events)


# ═══════════════════════════════════════════════════════════════════
# Streaming events passthrough
# ═══════════════════════════════════════════════════════════════════

class TestStreamingPassthrough:
    async def test_text_delta(self):
        async def model(**kw):
            yield {"type": "text_delta", "text": "chunk1"}
            yield {"type": "text_delta", "text": "chunk2"}
            yield {"type": "assistant", "message": Message(role="assistant", content="full")}

        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=model),
        ))
        deltas = [e for e in events if e["type"] == "text_delta"]
        assert len(deltas) == 2
        assert deltas[0]["text"] == "chunk1"

    async def test_thinking_delta(self):
        async def model(**kw):
            yield {"type": "thinking_delta", "text": "hmm"}
            yield {"type": "assistant", "message": Message(role="assistant", content="answer")}

        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=model),
        ))
        thinking = [e for e in events if e["type"] == "thinking_delta"]
        assert len(thinking) == 1

    async def test_content_block_events(self):
        async def model(**kw):
            yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}
            yield {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}
            yield {"type": "content_block_stop", "index": 0}
            yield {"type": "assistant", "message": Message(role="assistant", content="hi")}

        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=model),
        ))
        types = [e["type"] for e in events]
        assert "content_block_start" in types
        assert "content_block_delta" in types
        assert "content_block_stop" in types

    async def test_unknown_events_not_passed(self):
        async def model(**kw):
            yield {"type": "internal_debug", "data": "secret"}
            yield {"type": "assistant", "message": Message(role="assistant", content="hi")}

        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=model),
        ))
        assert not any(e["type"] == "internal_debug" for e in events)


# ═══════════════════════════════════════════════════════════════════
# Tool use — single tool
# ═══════════════════════════════════════════════════════════════════

class TestSingleToolUse:
    async def test_tool_use_event_yielded(self):
        events = await _collect(query(
            messages=[Message(role="user", content="read file")],
            deps=Deps(call_model=_tool_model_fn(), run_tool=_echo_tool),
        ))
        tool_uses = [e for e in events if e["type"] == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0]["name"] == "Read"
        assert tool_uses[0]["input"] == {"path": "test.txt"}

    async def test_tool_result_event_yielded(self):
        events = await _collect(query(
            messages=[Message(role="user", content="read file")],
            deps=Deps(call_model=_tool_model_fn(), run_tool=_echo_tool),
        ))
        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 1
        assert "executed Read" in results[0]["output"]
        assert results[0]["is_error"] is False

    async def test_follow_up_response_after_tool(self):
        events = await _collect(query(
            messages=[Message(role="user", content="read file")],
            deps=Deps(call_model=_tool_model_fn(response_after="Here it is."), run_tool=_echo_tool),
        ))
        assistants = [e for e in events if e["type"] == "assistant"]
        assert len(assistants) == 2  # tool_use + final response

    async def test_tool_id_preserved(self):
        events = await _collect(query(
            messages=[Message(role="user", content="read")],
            deps=Deps(call_model=_tool_model_fn(), run_tool=_echo_tool),
        ))
        use = next(e for e in events if e["type"] == "tool_use")
        result = next(e for e in events if e["type"] == "tool_result")
        assert use["id"] == result["tool_use_id"]


# ═══════════════════════════════════════════════════════════════════
# Tool use — parallel tools
# ═══════════════════════════════════════════════════════════════════

class TestParallelToolUse:
    async def test_two_tools(self):
        async def model(**kw):
            messages = kw.get("messages", [])
            has_result = any(
                isinstance(m.content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in m.content
                ) for m in messages if isinstance(m, Message)
            )
            if has_result:
                yield {"type": "assistant", "message": Message(role="assistant", content="Both done.")}
            else:
                yield {"type": "assistant", "message": Message(role="assistant", content=[
                    {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"path": "a"}},
                    {"type": "tool_use", "id": "tu2", "name": "Glob", "input": {"pattern": "*.py"}},
                ])}

        events = await _collect(query(
            messages=[Message(role="user", content="both")],
            deps=Deps(call_model=model, run_tool=_echo_tool),
        ))
        uses = [e for e in events if e["type"] == "tool_use"]
        results = [e for e in events if e["type"] == "tool_result"]
        assert len(uses) == 2
        assert len(results) == 2
        assert uses[0]["name"] == "Read"
        assert uses[1]["name"] == "Glob"


# ═══════════════════════════════════════════════════════════════════
# Tool errors
# ═══════════════════════════════════════════════════════════════════

class TestToolErrors:
    async def test_tool_exception_caught(self):
        async def failing_tool(name, input):
            raise RuntimeError("disk full")

        events = await _collect(query(
            messages=[Message(role="user", content="read")],
            deps=Deps(call_model=_tool_model_fn(), run_tool=failing_tool),
        ))
        results = [e for e in events if e["type"] == "tool_result"]
        assert results[0]["is_error"] is True
        assert "disk full" in results[0]["output"]

    async def test_no_executor_returns_error(self):
        events = await _collect(query(
            messages=[Message(role="user", content="read")],
            deps=Deps(call_model=_tool_model_fn()),  # no run_tool
        ))
        results = [e for e in events if e["type"] == "tool_result"]
        assert results[0]["is_error"] is True
        assert "No tool executor" in results[0]["output"]


# ═══════════════════════════════════════════════════════════════════
# Approval gate
# ═══════════════════════════════════════════════════════════════════

class TestApprovalGate:
    async def test_denied(self):
        async def deny(name, input):
            return {"allowed": False, "reason": "blocked by test"}

        events = await _collect(query(
            messages=[Message(role="user", content="read")],
            deps=Deps(call_model=_tool_model_fn(), run_tool=_echo_tool, approve=deny),
        ))
        results = [e for e in events if e["type"] == "tool_result"]
        assert results[0]["is_error"] is True
        assert "denied" in results[0]["output"].lower()

    async def test_allowed(self):
        async def allow(name, input):
            return {"allowed": True}

        events = await _collect(query(
            messages=[Message(role="user", content="read")],
            deps=Deps(call_model=_tool_model_fn(), run_tool=_echo_tool, approve=allow),
        ))
        results = [e for e in events if e["type"] == "tool_result"]
        assert results[0]["is_error"] is False

    async def test_no_approver_auto_allows(self):
        events = await _collect(query(
            messages=[Message(role="user", content="read")],
            deps=Deps(call_model=_tool_model_fn(), run_tool=_echo_tool),  # no approve
        ))
        results = [e for e in events if e["type"] == "tool_result"]
        assert results[0]["is_error"] is False

    async def test_selective_approval(self):
        async def selective(name, input):
            if name == "Bash":
                return {"allowed": False, "reason": "bash blocked"}
            return {"allowed": True}

        async def model(**kw):
            messages = kw.get("messages", [])
            has_result = any(
                isinstance(m.content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in m.content
                ) for m in messages if isinstance(m, Message)
            )
            if has_result:
                yield {"type": "assistant", "message": Message(role="assistant", content="done")}
            else:
                yield {"type": "assistant", "message": Message(role="assistant", content=[
                    {"type": "tool_use", "id": "tu1", "name": "Read", "input": {}},
                    {"type": "tool_use", "id": "tu2", "name": "Bash", "input": {"command": "rm -rf /"}},
                ])}

        events = await _collect(query(
            messages=[Message(role="user", content="do both")],
            deps=Deps(call_model=model, run_tool=_echo_tool, approve=selective),
        ))
        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 2
        read_result = results[0]
        bash_result = results[1]
        assert read_result["is_error"] is False
        assert bash_result["is_error"] is True
        assert "blocked" in bash_result["output"].lower()


# ═══════════════════════════════════════════════════════════════════
# Model errors
# ═══════════════════════════════════════════════════════════════════

class TestModelErrors:
    async def test_exception_yields_error(self):
        async def failing(**kw):
            raise ConnectionError("network down")
            yield  # noqa

        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=failing),
        ))
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert "network down" in errors[0]["error"]

    async def test_error_terminates_loop(self):
        async def failing(**kw):
            raise ValueError("bad input")
            yield  # noqa

        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=failing),
        ))
        assert not any(e["type"] == "done" for e in events)
        assert any(e["type"] == "error" for e in events)


# ═══════════════════════════════════════════════════════════════════
# Max turns
# ═══════════════════════════════════════════════════════════════════

class TestMaxTurns:
    async def test_stops_at_limit(self):
        counter = [0]
        async def always_tool(**kw):
            counter[0] += 1
            yield {"type": "assistant", "message": Message(role="assistant", content=[
                {"type": "tool_use", "id": f"tu{counter[0]}", "name": "X", "input": {}},
            ])}

        events = await _collect(query(
            messages=[Message(role="user", content="loop")],
            deps=Deps(call_model=always_tool, run_tool=_echo_tool),
            max_turns=2,
        ))
        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "max_turns"
        assert done["turns"] == 2

    async def test_max_turns_1(self):
        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=_simple_model_fn()),
            max_turns=1,
        ))
        done = next(e for e in events if e["type"] == "done")
        assert done["turns"] == 1

    async def test_default_max_turns(self):
        # Default is 100 — we don't run 100 turns, just verify it doesn't crash at 1
        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=_simple_model_fn()),
        ))
        done = next(e for e in events if e["type"] == "done")
        assert done["turns"] == 1


# ═══════════════════════════════════════════════════════════════════
# Kwargs passthrough
# ═══════════════════════════════════════════════════════════════════

class TestKwargsPassthrough:
    async def test_system_prompt_string(self):
        captured = {}
        async def capture(**kw):
            captured.update(kw)
            yield {"type": "assistant", "message": Message(role="assistant", content="ok")}

        await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=capture),
            system_prompt="Be a pirate",
        ))
        assert captured["system_prompt"] == "Be a pirate"

    async def test_system_prompt_list(self):
        captured = {}
        async def capture(**kw):
            captured.update(kw)
            yield {"type": "assistant", "message": Message(role="assistant", content="ok")}

        await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=capture),
            system_prompt=["Part 1", "Part 2"],
        ))
        assert captured["system_prompt"] == ["Part 1", "Part 2"]

    async def test_model_name(self):
        captured = {}
        async def capture(**kw):
            captured.update(kw)
            yield {"type": "assistant", "message": Message(role="assistant", content="ok")}

        await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=capture),
            model="gpt-4o",
        ))
        assert captured["model"] == "gpt-4o"

    async def test_thinking_config(self):
        captured = {}
        async def capture(**kw):
            captured.update(kw)
            yield {"type": "assistant", "message": Message(role="assistant", content="ok")}

        await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=capture),
            thinking={"type": "adaptive"},
        ))
        assert captured["thinking"] == {"type": "adaptive"}

    async def test_tools_passed(self):
        captured = {}
        async def capture(**kw):
            captured.update(kw)
            yield {"type": "assistant", "message": Message(role="assistant", content="ok")}

        tools = [{"name": "Read"}, {"name": "Bash"}]
        await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=capture),
            tools=tools,
        ))
        assert captured["tools"] == tools

    async def test_messages_passed_to_model(self):
        captured = {}
        async def capture(**kw):
            captured.update(kw)
            yield {"type": "assistant", "message": Message(role="assistant", content="ok")}

        msgs = [Message(role="user", content="hello")]
        await _collect(query(messages=msgs, deps=Deps(call_model=capture)))
        assert len(captured["messages"]) == 1
        assert captured["messages"][0].content == "hello"


# ═══════════════════════════════════════════════════════════════════
# Message accumulation across turns
# ═══════════════════════════════════════════════════════════════════

class TestMessageAccumulation:
    async def test_messages_grow_across_tool_turns(self):
        captured_messages = []

        async def tracking_model(**kw):
            messages = kw.get("messages", [])
            captured_messages.append(len(messages))
            has_result = any(
                isinstance(m.content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in m.content
                ) for m in messages if isinstance(m, Message)
            )
            if has_result:
                yield {"type": "assistant", "message": Message(role="assistant", content="done")}
            else:
                yield {"type": "assistant", "message": Message(role="assistant", content=[
                    {"type": "tool_use", "id": "tu1", "name": "X", "input": {}},
                ])}

        await _collect(query(
            messages=[Message(role="user", content="do it")],
            deps=Deps(call_model=tracking_model, run_tool=_echo_tool),
        ))

        # First call: 1 message (user)
        # Second call: 1 (user) + 1 (assistant w/ tool_use) + 1 (user w/ tool_result) = 3
        assert captured_messages[0] == 1
        assert captured_messages[1] == 3
