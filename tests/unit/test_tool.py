"""Tests for duh.kernel.tool — the tool protocol."""

import asyncio
from dataclasses import dataclass

from duh.kernel.tool import Tool, ToolContext, ToolResult


# --- Test tool implementations ---

class EchoTool:
    """A simple tool for testing."""
    name = "Echo"
    description = "Echoes the input"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def call(self, input: dict, context: ToolContext) -> ToolResult:
        return ToolResult(output=input.get("text", ""))

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def check_permissions(self, input: dict, context: ToolContext) -> dict:
        return {"allowed": True}


class FailingTool:
    """A tool that always fails."""
    name = "Fail"
    description = "Always fails"
    input_schema = {"type": "object", "properties": {}}

    async def call(self, input: dict, context: ToolContext) -> ToolResult:
        raise RuntimeError("intentional failure")

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return True

    async def check_permissions(self, input: dict, context: ToolContext) -> dict:
        return {"allowed": False, "reason": "always denied"}


class TestToolResult:
    def test_default(self):
        r = ToolResult()
        assert r.output == ""
        assert r.is_error is False
        assert r.metadata == {}

    def test_with_output(self):
        r = ToolResult(output="file contents here")
        assert r.output == "file contents here"

    def test_error_result(self):
        r = ToolResult(output="not found", is_error=True)
        assert r.is_error is True

    def test_metadata(self):
        r = ToolResult(output="ok", metadata={"duration_ms": 42})
        assert r.metadata["duration_ms"] == 42


class TestToolContext:
    def test_defaults(self):
        ctx = ToolContext()
        assert ctx.cwd == "."
        assert ctx.tool_use_id == ""
        assert ctx.session_id == ""

    def test_custom(self):
        ctx = ToolContext(cwd="/tmp", tool_use_id="tu1", session_id="s1")
        assert ctx.cwd == "/tmp"
        assert ctx.tool_use_id == "tu1"
        assert ctx.session_id == "s1"


def test_tool_context_has_confirm_token_field() -> None:
    ctx = ToolContext(tool_name="Bash", input_obj={"command": "ls"})
    assert hasattr(ctx, "confirm_token")
    assert ctx.confirm_token is None  # default


def test_tool_context_accepts_confirm_token() -> None:
    ctx = ToolContext(
        tool_name="Bash",
        input_obj={"command": "ls"},
        confirm_token="duh-confirm-123-abc",
    )
    assert ctx.confirm_token == "duh-confirm-123-abc"


class TestToolProtocol:
    def test_echo_tool_satisfies_protocol(self):
        tool = EchoTool()
        assert isinstance(tool, Tool)

    def test_echo_tool_call(self):
        tool = EchoTool()
        ctx = ToolContext()
        result = asyncio.run(tool.call({"text": "hello"}, ctx))
        assert result.output == "hello"
        assert result.is_error is False

    def test_echo_tool_is_read_only(self):
        tool = EchoTool()
        assert tool.is_read_only is True
        assert tool.is_destructive is False

    def test_echo_tool_permissions(self):
        tool = EchoTool()
        ctx = ToolContext()
        result = asyncio.run(tool.check_permissions({"text": "hi"}, ctx))
        assert result["allowed"] is True

    def test_failing_tool_permissions(self):
        tool = FailingTool()
        ctx = ToolContext()
        result = asyncio.run(tool.check_permissions({}, ctx))
        assert result["allowed"] is False
        assert "denied" in result["reason"]

    def test_failing_tool_is_destructive(self):
        tool = FailingTool()
        assert tool.is_destructive is True

    def test_tool_schema(self):
        tool = EchoTool()
        assert tool.input_schema["type"] == "object"
        assert "text" in tool.input_schema["properties"]
        assert "text" in tool.input_schema["required"]
