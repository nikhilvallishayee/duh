"""Tests for duh.kernel.messages — the message data model."""

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


class TestTextBlock:
    def test_create(self):
        b = TextBlock(text="hello")
        assert b.text == "hello"
        assert b.type == "text"

    def test_frozen(self):
        b = TextBlock(text="hello")
        try:
            b.text = "world"
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestToolUseBlock:
    def test_create(self):
        b = ToolUseBlock(id="tu1", name="Read", input={"path": "/tmp"})
        assert b.id == "tu1"
        assert b.name == "Read"
        assert b.input == {"path": "/tmp"}
        assert b.type == "tool_use"


class TestToolResultBlock:
    def test_create(self):
        b = ToolResultBlock(tool_use_id="tu1", content="file contents")
        assert b.tool_use_id == "tu1"
        assert b.content == "file contents"
        assert b.is_error is False

    def test_error_result(self):
        b = ToolResultBlock(tool_use_id="tu1", content="not found", is_error=True)
        assert b.is_error is True


class TestThinkingBlock:
    def test_create(self):
        b = ThinkingBlock(thinking="Let me think...")
        assert b.thinking == "Let me think..."
        assert b.type == "thinking"


class TestMessage:
    def test_create_with_string_content(self):
        m = Message(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.text == "hello"
        assert m.id  # auto-generated UUID
        assert m.timestamp  # auto-generated

    def test_create_with_blocks(self):
        m = Message(role="assistant", content=[
            TextBlock(text="Here's the file:"),
            ToolUseBlock(id="tu1", name="Read", input={"path": "x.py"}),
        ])
        assert m.text == "Here's the file:"
        assert m.has_tool_use is True
        assert len(m.tool_use_blocks) == 1

    def test_text_extracts_from_blocks(self):
        m = Message(role="assistant", content=[
            TextBlock(text="part1"),
            TextBlock(text="part2"),
        ])
        assert m.text == "part1part2"

    def test_text_from_dict_blocks(self):
        m = Message(role="assistant", content=[
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "tu1", "name": "Read", "input": {}},
        ])
        assert m.text == "hello"
        assert m.has_tool_use is True

    def test_no_tool_use(self):
        m = Message(role="assistant", content="just text")
        assert m.has_tool_use is False
        assert m.tool_use_blocks == []

    def test_metadata(self):
        m = Message(role="user", content="hi", metadata={"source": "test"})
        assert m.metadata["source"] == "test"


class TestMessageFactories:
    def test_user_message(self):
        m = UserMessage("hello")
        assert m.role == "user"
        assert m.content == "hello"

    def test_assistant_message(self):
        m = AssistantMessage("hi there")
        assert m.role == "assistant"
        assert m.content == "hi there"

    def test_system_message(self):
        m = SystemMessage("You are helpful")
        assert m.role == "system"
        assert m.content == "You are helpful"

    def test_user_message_with_blocks(self):
        m = UserMessage([
            ToolResultBlock(tool_use_id="tu1", content="result"),
        ])
        assert m.role == "user"
        assert isinstance(m.content, list)

    def test_factories_generate_unique_ids(self):
        m1 = UserMessage("a")
        m2 = UserMessage("b")
        assert m1.id != m2.id
