"""Tests for duh.tools.mcp_tool — MCPToolWrapper adapter.

Verifies that the wrapper correctly adapts MCPToolInfo + MCPExecutor
into the D.U.H. Tool protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from duh.kernel.tool import ToolContext, ToolResult
from duh.tools.mcp_tool import MCPToolWrapper


# ---------------------------------------------------------------------------
# Fake MCPToolInfo (avoids importing the real one which needs mcp package)
# ---------------------------------------------------------------------------


@dataclass
class FakeMCPToolInfo:
    name: str
    server_name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wrapper(
    *,
    name: str = "navigate",
    server: str = "playwright",
    description: str = "Navigate to URL",
    schema: dict[str, Any] | None = None,
    run_result: str | Exception = "ok",
) -> tuple[MCPToolWrapper, AsyncMock]:
    """Create a wrapper with a mock executor."""
    info = FakeMCPToolInfo(
        name=name,
        server_name=server,
        description=description,
        input_schema=schema or {"type": "object", "properties": {"url": {"type": "string"}}},
    )
    executor = AsyncMock()
    if isinstance(run_result, Exception):
        executor.run = AsyncMock(side_effect=run_result)
    else:
        executor.run = AsyncMock(return_value=run_result)
    wrapper = MCPToolWrapper(info=info, executor=executor)
    return wrapper, executor


def _ctx(**kwargs: Any) -> ToolContext:
    return ToolContext(**kwargs)


# ---------------------------------------------------------------------------
# Tests: Construction
# ---------------------------------------------------------------------------


class TestMCPToolWrapperConstruction:
    def test_qualified_name(self) -> None:
        wrapper, _ = _make_wrapper(name="click", server="pw")
        assert wrapper.name == "mcp__pw__click"

    def test_description_from_info(self) -> None:
        wrapper, _ = _make_wrapper(description="Click an element")
        assert wrapper.description == "Click an element"

    def test_description_fallback(self) -> None:
        wrapper, _ = _make_wrapper(description="")
        assert "navigate" in wrapper.description.lower()

    def test_input_schema_from_info(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        wrapper, _ = _make_wrapper(schema=schema)
        assert wrapper.input_schema == schema

    def test_input_schema_fallback_when_empty(self) -> None:
        """When MCP tool reports an empty schema, the wrapper provides a default."""
        info = FakeMCPToolInfo(name="t", server_name="s", input_schema={})
        executor = AsyncMock()
        executor.run = AsyncMock(return_value="ok")
        wrapper = MCPToolWrapper(info=info, executor=executor)
        assert wrapper.input_schema == {"type": "object", "properties": {}}

    def test_is_read_only_false(self) -> None:
        wrapper, _ = _make_wrapper()
        assert wrapper.is_read_only is False

    def test_is_destructive_false(self) -> None:
        wrapper, _ = _make_wrapper()
        assert wrapper.is_destructive is False


# ---------------------------------------------------------------------------
# Tests: call()
# ---------------------------------------------------------------------------


class TestMCPToolWrapperCall:
    @pytest.mark.asyncio
    async def test_call_delegates_to_executor(self) -> None:
        wrapper, executor = _make_wrapper(run_result="page loaded")
        ctx = _ctx(tool_use_id="tu_123")

        result = await wrapper.call({"url": "https://example.com"}, ctx)

        assert isinstance(result, ToolResult)
        assert result.output == "page loaded"
        assert result.is_error is False
        executor.run.assert_awaited_once_with(
            "mcp__playwright__navigate",
            {"url": "https://example.com"},
            tool_use_id="tu_123",
        )

    @pytest.mark.asyncio
    async def test_call_passes_tool_use_id(self) -> None:
        wrapper, executor = _make_wrapper()
        ctx = _ctx(tool_use_id="abc-789")

        await wrapper.call({}, ctx)

        executor.run.assert_awaited_once()
        _, kwargs = executor.run.call_args
        assert kwargs["tool_use_id"] == "abc-789"

    @pytest.mark.asyncio
    async def test_call_error_returns_tool_result_with_error(self) -> None:
        wrapper, _ = _make_wrapper(run_result=RuntimeError("connection lost"))
        ctx = _ctx()

        result = await wrapper.call({"url": "http://bad"}, ctx)

        assert isinstance(result, ToolResult)
        assert result.is_error is True
        assert "connection lost" in result.output

    @pytest.mark.asyncio
    async def test_call_non_string_output_coerced(self) -> None:
        wrapper, executor = _make_wrapper()
        executor.run = AsyncMock(return_value=42)
        ctx = _ctx()

        result = await wrapper.call({}, ctx)

        assert result.output == "42"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_call_empty_string_output(self) -> None:
        wrapper, executor = _make_wrapper(run_result="")
        ctx = _ctx()

        result = await wrapper.call({}, ctx)

        assert result.output == ""
        assert result.is_error is False


# ---------------------------------------------------------------------------
# Tests: check_permissions()
# ---------------------------------------------------------------------------


class TestMCPToolWrapperPermissions:
    @pytest.mark.asyncio
    async def test_always_allowed(self) -> None:
        wrapper, _ = _make_wrapper()
        ctx = _ctx()

        perm = await wrapper.check_permissions({"any": "input"}, ctx)

        assert perm == {"allowed": True}


# ---------------------------------------------------------------------------
# Tests: Tool protocol compliance
# ---------------------------------------------------------------------------


class TestMCPToolProtocol:
    """Verify the wrapper satisfies the Tool protocol structurally."""

    def test_has_name(self) -> None:
        wrapper, _ = _make_wrapper()
        assert isinstance(wrapper.name, str)
        assert len(wrapper.name) > 0

    def test_has_description(self) -> None:
        wrapper, _ = _make_wrapper()
        assert isinstance(wrapper.description, str)

    def test_has_input_schema(self) -> None:
        wrapper, _ = _make_wrapper()
        assert isinstance(wrapper.input_schema, dict)

    def test_has_call(self) -> None:
        wrapper, _ = _make_wrapper()
        assert callable(wrapper.call)

    def test_has_check_permissions(self) -> None:
        wrapper, _ = _make_wrapper()
        assert callable(wrapper.check_permissions)
