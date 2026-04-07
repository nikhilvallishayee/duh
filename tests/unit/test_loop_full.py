"""Full coverage for duh.kernel.loop — line 189 (dict assistant_message)."""

from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.loop import query
from duh.kernel.messages import Message


async def _collect(gen) -> list[dict]:
    return [e async for e in gen]


class TestDictAssistantMessage:
    """Cover line 189: when assistant_message is a dict, not a Message object."""

    async def test_dict_assistant_with_tool_use(self):
        """When the model yields a dict assistant message with tool_use,
        the loop wraps it in a Message for the next turn."""
        call_count = [0]

        async def model_yields_dict(**kwargs):
            call_count[0] += 1
            messages = kwargs.get("messages", [])

            # Check if we've already gone around
            if call_count[0] > 1:
                yield {"type": "assistant", "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "Done."}],
                )}
                return

            # First call: yield a dict (not Message) as the assistant message
            yield {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "tu1", "name": "Read",
                         "input": {"path": "x"}},
                    ],
                },
            }

        async def fake_tool(name, input):
            return "file contents"

        events = await _collect(query(
            messages=[Message(role="user", content="read")],
            deps=Deps(call_model=model_yields_dict, run_tool=fake_tool),
            max_turns=3,
        ))

        # Should have tool_use, tool_result, and eventually done
        types = [e["type"] for e in events]
        assert "tool_use" in types
        assert "tool_result" in types


class TestStopReasonFromMetadata:
    """When assistant message has a custom stop_reason in metadata."""

    async def test_custom_stop_reason(self):
        async def model(**kw):
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": "Done."}],
                metadata={"stop_reason": "max_tokens"},
            )}

        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=model),
        ))

        done = next(e for e in events if e["type"] == "done")
        assert done["stop_reason"] == "max_tokens"


class TestNonDictEvent:
    """When model yields a non-dict event, it should be handled gracefully."""

    async def test_non_dict_event(self):
        async def model(**kw):
            yield "not a dict"  # type: ignore
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok"
            )}

        events = await _collect(query(
            messages=[Message(role="user", content="hi")],
            deps=Deps(call_model=model),
        ))

        # Non-dict should be silently skipped (event_type will be "")
        assert any(e["type"] == "done" for e in events)
