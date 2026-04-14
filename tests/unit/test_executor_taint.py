"""Tool executor outputs must carry TOOL_OUTPUT or MCP_OUTPUT taint."""

from __future__ import annotations

from duh.adapters.native_executor import _wrap_tool_output
from duh.adapters.mcp_executor import _wrap_mcp_output
from duh.kernel.untrusted import TaintSource, UntrustedStr


def test_native_executor_wraps_tool_output() -> None:
    result = _wrap_tool_output("file contents here")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.TOOL_OUTPUT


def test_native_executor_idempotent() -> None:
    pre = UntrustedStr("already tagged", TaintSource.TOOL_OUTPUT)
    result = _wrap_tool_output(pre)
    assert result.source == TaintSource.TOOL_OUTPUT


def test_mcp_executor_wraps_mcp_output() -> None:
    result = _wrap_mcp_output("mcp server response")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.MCP_OUTPUT


def test_mcp_executor_idempotent() -> None:
    pre = UntrustedStr("already tagged", TaintSource.MCP_OUTPUT)
    result = _wrap_mcp_output(pre)
    assert result.source == TaintSource.MCP_OUTPUT
