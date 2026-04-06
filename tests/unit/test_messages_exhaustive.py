"""Exhaustive tests for duh.kernel.messages — 100% coverage target.

Tests every constructor, property, edge case, and type combination.
"""

import uuid

from duh.kernel.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


# ═══════════════════════════════════════════════════════════════════
# TextBlock
# ═══════════════════════════════════════════════════════════════════

class TestTextBlockExhaustive:
    def test_create_simple(self):
        b = TextBlock(text="hello")
        assert b.text == "hello"
        assert b.type == "text"

    def test_empty_text(self):
        b = TextBlock(text="")
        assert b.text == ""

    def test_multiline_text(self):
        b = TextBlock(text="line1\nline2\nline3")
        assert "\n" in b.text

    def test_unicode_text(self):
        b = TextBlock(text="こんにちは 🎉")
        assert "こんにちは" in b.text

    def test_frozen_immutable(self):
        b = TextBlock(text="x")
        try:
            b.text = "y"  # type: ignore
            assert False, "Should raise"
        except (AttributeError, TypeError):
            pass

    def test_frozen_type_immutable(self):
        b = TextBlock(text="x")
        try:
            b.type = "other"  # type: ignore
            assert False
        except (AttributeError, TypeError):
            pass

    def test_equality(self):
        a = TextBlock(text="same")
        b = TextBlock(text="same")
        assert a == b

    def test_inequality(self):
        a = TextBlock(text="a")
        b = TextBlock(text="b")
        assert a != b


# ═══════════════════════════════════════════════════════════════════
# ToolUseBlock
# ═══════════════════════════════════════════════════════════════════

class TestToolUseBlockExhaustive:
    def test_create(self):
        b = ToolUseBlock(id="tu1", name="Read", input={"path": "/tmp"})
        assert b.id == "tu1"
        assert b.name == "Read"
        assert b.input == {"path": "/tmp"}
        assert b.type == "tool_use"

    def test_empty_input(self):
        b = ToolUseBlock(id="tu1", name="List", input={})
        assert b.input == {}

    def test_complex_input(self):
        b = ToolUseBlock(id="tu1", name="Bash", input={
            "command": "echo hello",
            "timeout": 30000,
            "run_in_background": True,
        })
        assert b.input["timeout"] == 30000

    def test_frozen(self):
        b = ToolUseBlock(id="tu1", name="X", input={})
        try:
            b.name = "Y"  # type: ignore
            assert False
        except (AttributeError, TypeError):
            pass


# ═══════════════════════════════════════════════════════════════════
# ToolResultBlock
# ═══════════════════════════════════════════════════════════════════

class TestToolResultBlockExhaustive:
    def test_success(self):
        b = ToolResultBlock(tool_use_id="tu1", content="file contents")
        assert b.tool_use_id == "tu1"
        assert b.content == "file contents"
        assert b.is_error is False
        assert b.type == "tool_result"

    def test_error(self):
        b = ToolResultBlock(tool_use_id="tu1", content="not found", is_error=True)
        assert b.is_error is True

    def test_list_content(self):
        b = ToolResultBlock(tool_use_id="tu1", content=[
            {"type": "text", "text": "result"},
        ])
        assert isinstance(b.content, list)

    def test_empty_content(self):
        b = ToolResultBlock(tool_use_id="tu1", content="")
        assert b.content == ""


# ═══════════════════════════════════════════════════════════════════
# ThinkingBlock
# ═══════════════════════════════════════════════════════════════════

class TestThinkingBlockExhaustive:
    def test_create(self):
        b = ThinkingBlock(thinking="Let me analyze...")
        assert b.thinking == "Let me analyze..."
        assert b.type == "thinking"

    def test_empty_thinking(self):
        b = ThinkingBlock(thinking="")
        assert b.thinking == ""

    def test_long_thinking(self):
        text = "x" * 10000
        b = ThinkingBlock(thinking=text)
        assert len(b.thinking) == 10000


# ═══════════════════════════════════════════════════════════════════
# Message — string content
# ═══════════════════════════════════════════════════════════════════

class TestMessageStringContent:
    def test_user_string(self):
        m = Message(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.text == "hello"

    def test_assistant_string(self):
        m = Message(role="assistant", content="hi there")
        assert m.text == "hi there"

    def test_system_string(self):
        m = Message(role="system", content="You are helpful")
        assert m.text == "You are helpful"

    def test_empty_content(self):
        m = Message(role="user", content="")
        assert m.text == ""

    def test_no_tool_use_for_string(self):
        m = Message(role="user", content="plain text")
        assert m.has_tool_use is False
        assert m.tool_use_blocks == []


# ═══════════════════════════════════════════════════════════════════
# Message — block content
# ═══════════════════════════════════════════════════════════════════

class TestMessageBlockContent:
    def test_single_text_block(self):
        m = Message(role="assistant", content=[TextBlock(text="hello")])
        assert m.text == "hello"
        assert m.has_tool_use is False

    def test_multiple_text_blocks(self):
        m = Message(role="assistant", content=[
            TextBlock(text="part1"),
            TextBlock(text="part2"),
        ])
        assert m.text == "part1part2"

    def test_text_and_tool_use(self):
        m = Message(role="assistant", content=[
            TextBlock(text="Reading file..."),
            ToolUseBlock(id="tu1", name="Read", input={"path": "x"}),
        ])
        assert m.text == "Reading file..."
        assert m.has_tool_use is True
        assert len(m.tool_use_blocks) == 1

    def test_multiple_tool_uses(self):
        m = Message(role="assistant", content=[
            ToolUseBlock(id="tu1", name="Read", input={"path": "a"}),
            ToolUseBlock(id="tu2", name="Read", input={"path": "b"}),
        ])
        assert len(m.tool_use_blocks) == 2

    def test_dict_text_blocks(self):
        m = Message(role="assistant", content=[
            {"type": "text", "text": "hello"},
        ])
        assert m.text == "hello"

    def test_dict_tool_use_blocks(self):
        m = Message(role="assistant", content=[
            {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {}},
        ])
        assert m.has_tool_use is True
        assert len(m.tool_use_blocks) == 1

    def test_mixed_dataclass_and_dict_blocks(self):
        m = Message(role="assistant", content=[
            TextBlock(text="text1"),
            {"type": "text", "text": "text2"},
            ToolUseBlock(id="tu1", name="X", input={}),
            {"type": "tool_use", "id": "tu2", "name": "Y", "input": {}},
        ])
        assert m.text == "text1text2"
        assert len(m.tool_use_blocks) == 2

    def test_thinking_blocks_not_in_text(self):
        m = Message(role="assistant", content=[
            ThinkingBlock(thinking="hmm"),
            TextBlock(text="answer"),
        ])
        assert m.text == "answer"  # thinking excluded from .text

    def test_tool_result_blocks(self):
        m = Message(role="user", content=[
            ToolResultBlock(tool_use_id="tu1", content="file data"),
        ])
        assert m.has_tool_use is False  # tool_result != tool_use
        assert m.text == ""  # tool_results have no .text

    def test_empty_block_list(self):
        m = Message(role="assistant", content=[])
        assert m.text == ""
        assert m.has_tool_use is False

    def test_non_text_dict_blocks_ignored_in_text(self):
        m = Message(role="assistant", content=[
            {"type": "image", "data": "..."},
            {"type": "text", "text": "caption"},
        ])
        assert m.text == "caption"


# ═══════════════════════════════════════════════════════════════════
# Message — auto-generated fields
# ═══════════════════════════════════════════════════════════════════

class TestMessageAutoFields:
    def test_id_is_uuid(self):
        m = Message(role="user", content="hi")
        parsed = uuid.UUID(m.id)  # should not raise
        assert parsed.version == 4

    def test_unique_ids(self):
        ids = {Message(role="user", content="x").id for _ in range(100)}
        assert len(ids) == 100

    def test_timestamp_is_iso(self):
        m = Message(role="user", content="hi")
        assert "T" in m.timestamp
        assert "+" in m.timestamp or "Z" in m.timestamp

    def test_custom_id(self):
        m = Message(role="user", content="hi", id="custom-123")
        assert m.id == "custom-123"

    def test_custom_timestamp(self):
        m = Message(role="user", content="hi", timestamp="2026-01-01T00:00:00Z")
        assert m.timestamp == "2026-01-01T00:00:00Z"

    def test_metadata_default_empty(self):
        m = Message(role="user", content="hi")
        assert m.metadata == {}

    def test_metadata_custom(self):
        m = Message(role="user", content="hi", metadata={"k": "v"})
        assert m.metadata["k"] == "v"

    def test_metadata_mutable(self):
        m = Message(role="user", content="hi")
        m.metadata["new_key"] = "new_val"
        assert m.metadata["new_key"] == "new_val"


# ═══════════════════════════════════════════════════════════════════
# Factory functions
# ═══════════════════════════════════════════════════════════════════

class TestFactoriesExhaustive:
    def test_user_message_string(self):
        m = UserMessage("hello")
        assert m.role == "user"
        assert isinstance(m, Message)

    def test_user_message_blocks(self):
        m = UserMessage([ToolResultBlock(tool_use_id="tu1", content="r")])
        assert m.role == "user"
        assert isinstance(m.content, list)

    def test_assistant_message_string(self):
        m = AssistantMessage("response")
        assert m.role == "assistant"

    def test_assistant_message_blocks(self):
        m = AssistantMessage([TextBlock(text="hi"), ToolUseBlock(id="t", name="X", input={})])
        assert m.role == "assistant"
        assert m.has_tool_use is True

    def test_system_message(self):
        m = SystemMessage("You are helpful")
        assert m.role == "system"

    def test_factory_with_kwargs(self):
        m = UserMessage("hi", id="custom", metadata={"source": "test"})
        assert m.id == "custom"
        assert m.metadata["source"] == "test"

    def test_factory_returns_message_type(self):
        assert type(UserMessage("x")) is Message
        assert type(AssistantMessage("x")) is Message
        assert type(SystemMessage("x")) is Message
