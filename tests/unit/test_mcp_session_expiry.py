"""Tests for MCP session expiry detection and reconnection."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from duh.adapters.mcp_executor import (
    MCPExecutor,
    MCPServerConfig,
    MCPConnection,
    MCPToolInfo,
    _is_session_expired,
    MAX_SESSION_RETRIES,
    MAX_ERRORS_BEFORE_RECONNECT,
)


def test_session_expiry_detection():
    assert _is_session_expired(404, "Session not found")
    assert _is_session_expired(404, "session not found: abc-123")
    assert not _is_session_expired(200, "OK")
    assert not _is_session_expired(500, "Internal server error")
    assert not _is_session_expired(404, "Tool not found")


def test_constants():
    assert MAX_SESSION_RETRIES == 1
    assert MAX_ERRORS_BEFORE_RECONNECT == 3
