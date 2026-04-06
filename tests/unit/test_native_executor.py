"""Exhaustive tests for duh.adapters.native_executor — 100% coverage."""

import pytest
from duh.adapters.native_executor import NativeExecutor
from duh.kernel.tool import ToolContext, ToolResult


# --- Test tools ---

class EchoTool:
    name = "Echo"
    description = "Echoes input"
    input_schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def call(self, input, context):
        return ToolResult(output=input.get("text", ""))

    async def check_permissions(self, input, context):
        return {"allowed": True}


class DenyTool:
    name = "Deny"
    description = "Always denies"
    input_schema = {}

    async def call(self, input, context):
        return ToolResult(output="should not reach here")

    async def check_permissions(self, input, context):
        return {"allowed": False, "reason": "always denied"}


class ErrorTool:
    name = "Error"
    description = "Always errors"
    input_schema = {}

    async def call(self, input, context):
        return ToolResult(output="something broke", is_error=True)


class ExceptionTool:
    name = "Exception"
    description = "Raises exception"
    input_schema = {}

    async def call(self, input, context):
        raise RuntimeError("tool crashed")


class NoPermsTool:
    name = "NoPerm"
    description = "No check_permissions method"
    input_schema = {}

    async def call(self, input, context):
        return ToolResult(output="ran without perm check")


class RawReturnTool:
    name = "Raw"
    description = "Returns a raw string, not ToolResult"
    input_schema = {}

    async def call(self, input, context):
        return "raw string result"


class TestConstruction:
    def test_empty(self):
        e = NativeExecutor()
        assert e.tool_names == []

    def test_with_tools(self):
        e = NativeExecutor(tools=[EchoTool(), DenyTool()])
        assert sorted(e.tool_names) == ["Deny", "Echo"]

    def test_register(self):
        e = NativeExecutor()
        e.register(EchoTool())
        assert "Echo" in e.tool_names

    def test_register_no_name_raises(self):
        e = NativeExecutor()
        with pytest.raises(ValueError, match="must have a 'name'"):
            e.register(object())

    def test_get_tool(self):
        tool = EchoTool()
        e = NativeExecutor(tools=[tool])
        assert e.get_tool("Echo") is tool

    def test_get_tool_missing(self):
        e = NativeExecutor()
        assert e.get_tool("Nonexistent") is None

    def test_custom_cwd(self):
        e = NativeExecutor(cwd="/custom/path")
        assert e._cwd == "/custom/path"


class TestRun:
    async def test_successful_execution(self):
        e = NativeExecutor(tools=[EchoTool()])
        result = await e.run("Echo", {"text": "hello"})
        assert result == "hello"

    async def test_tool_not_found(self):
        e = NativeExecutor()
        with pytest.raises(KeyError, match="Tool not found"):
            await e.run("Nonexistent", {})

    async def test_permission_denied(self):
        e = NativeExecutor(tools=[DenyTool()])
        with pytest.raises(PermissionError, match="always denied"):
            await e.run("Deny", {})

    async def test_tool_error_result(self):
        e = NativeExecutor(tools=[ErrorTool()])
        with pytest.raises(RuntimeError, match="something broke"):
            await e.run("Error", {})

    async def test_tool_exception(self):
        e = NativeExecutor(tools=[ExceptionTool()])
        with pytest.raises(RuntimeError, match="tool crashed"):
            await e.run("Exception", {})

    async def test_no_permissions_method(self):
        e = NativeExecutor(tools=[NoPermsTool()])
        result = await e.run("NoPerm", {})
        assert result == "ran without perm check"

    async def test_raw_string_return(self):
        e = NativeExecutor(tools=[RawReturnTool()])
        result = await e.run("Raw", {})
        assert result == "raw string result"

    async def test_tool_use_id_passed(self):
        captured_ctx = []

        class CaptureTool:
            name = "Capture"
            description = ""
            input_schema = {}
            async def call(self, input, context):
                captured_ctx.append(context)
                return ToolResult(output="ok")

        e = NativeExecutor(tools=[CaptureTool()])
        await e.run("Capture", {}, tool_use_id="tu-123")
        assert captured_ctx[0].tool_use_id == "tu-123"

    async def test_cwd_passed_to_context(self):
        captured_ctx = []

        class CaptureTool:
            name = "Capture"
            description = ""
            input_schema = {}
            async def call(self, input, context):
                captured_ctx.append(context)
                return ToolResult(output="ok")

        e = NativeExecutor(tools=[CaptureTool()], cwd="/my/project")
        await e.run("Capture", {})
        assert captured_ctx[0].cwd == "/my/project"

    async def test_empty_input(self):
        e = NativeExecutor(tools=[EchoTool()])
        result = await e.run("Echo", {})
        assert result == ""

    async def test_multiple_tools_registered(self):
        e = NativeExecutor(tools=[EchoTool(), NoPermsTool(), RawReturnTool()])
        assert await e.run("Echo", {"text": "a"}) == "a"
        assert await e.run("NoPerm", {}) == "ran without perm check"
        assert await e.run("Raw", {}) == "raw string result"
