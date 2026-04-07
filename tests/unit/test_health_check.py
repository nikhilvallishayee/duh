"""Tests for duh.kernel.health_check — provider and MCP health checking.

All HTTP calls are mocked. No real network traffic.

Covers:
- check_provider for anthropic, openai, ollama (healthy + unhealthy)
- check_provider for unknown provider
- check_all_providers aggregation
- check_mcp with live connection, missing connection, None connections
- check_all_mcp aggregation
- Timeout and connection error handling
- _format_latency helper in doctor.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from duh.kernel.health_check import HealthChecker


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def checker() -> HealthChecker:
    return HealthChecker(timeout=2.0)


def _mock_response(status_code: int = 200) -> httpx.Response:
    """Build a minimal httpx.Response for mocking."""
    return httpx.Response(status_code=status_code, request=httpx.Request("HEAD", "https://example.com"))


# ===================================================================
# check_provider — Anthropic
# ===================================================================


class TestCheckProviderAnthropic:
    """Anthropic provider health checks."""

    def test_healthy(self, checker: HealthChecker) -> None:
        with patch.object(httpx.Client, "head", return_value=_mock_response(200)):
            result = checker.check_provider("anthropic")
        assert result["healthy"] is True
        assert result["error"] is None
        assert isinstance(result["latency_ms"], int)
        assert result["latency_ms"] >= 0

    def test_unhealthy_500(self, checker: HealthChecker) -> None:
        with patch.object(httpx.Client, "head", return_value=_mock_response(500)):
            result = checker.check_provider("anthropic")
        assert result["healthy"] is False
        assert "500" in result["error"]

    def test_unhealthy_connection_refused(self, checker: HealthChecker) -> None:
        with patch.object(
            httpx.Client, "head",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result = checker.check_provider("anthropic")
        assert result["healthy"] is False
        assert "Connection refused" in result["error"]


# ===================================================================
# check_provider — OpenAI
# ===================================================================


class TestCheckProviderOpenAI:
    """OpenAI provider health checks."""

    def test_healthy(self, checker: HealthChecker) -> None:
        with patch.object(httpx.Client, "head", return_value=_mock_response(200)):
            result = checker.check_provider("openai")
        assert result["healthy"] is True
        assert result["error"] is None

    def test_unhealthy_timeout(self, checker: HealthChecker) -> None:
        with patch.object(
            httpx.Client, "head",
            side_effect=httpx.TimeoutException("Timed out"),
        ):
            result = checker.check_provider("openai")
        assert result["healthy"] is False
        assert result["error"] == "Timeout"
        assert result["latency_ms"] >= 0


# ===================================================================
# check_provider — Ollama
# ===================================================================


class TestCheckProviderOllama:
    """Ollama provider health checks."""

    def test_healthy(self, checker: HealthChecker) -> None:
        with patch.object(httpx.Client, "get", return_value=_mock_response(200)):
            result = checker.check_provider("ollama")
        assert result["healthy"] is True
        assert result["error"] is None

    def test_unhealthy_not_running(self, checker: HealthChecker) -> None:
        with patch.object(
            httpx.Client, "get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result = checker.check_provider("ollama")
        assert result["healthy"] is False
        assert "Connection refused" in result["error"]


# ===================================================================
# check_provider — unknown
# ===================================================================


class TestCheckProviderUnknown:
    """Unknown provider returns an error immediately."""

    def test_unknown_provider(self, checker: HealthChecker) -> None:
        result = checker.check_provider("nope")
        assert result["healthy"] is False
        assert "Unknown provider" in result["error"]
        assert result["latency_ms"] == 0


# ===================================================================
# check_all_providers
# ===================================================================


class TestCheckAllProviders:
    """Aggregation across all providers."""

    def test_all_healthy(self, checker: HealthChecker) -> None:
        with patch.object(httpx.Client, "head", return_value=_mock_response(200)), \
             patch.object(httpx.Client, "get", return_value=_mock_response(200)):
            results = checker.check_all_providers()
        assert set(results.keys()) == {"anthropic", "openai", "ollama"}
        for name, r in results.items():
            assert r["healthy"] is True, f"{name} should be healthy"

    def test_mixed_health(self, checker: HealthChecker) -> None:
        """One provider healthy, one not."""
        def fake_head(self_client: Any, url: str, **kw: Any) -> httpx.Response:
            if "anthropic" in url:
                return _mock_response(200)
            raise httpx.TimeoutException("timeout")

        with patch.object(httpx.Client, "head", fake_head), \
             patch.object(httpx.Client, "get", return_value=_mock_response(200)):
            results = checker.check_all_providers()
        assert results["anthropic"]["healthy"] is True
        assert results["openai"]["healthy"] is False
        assert results["ollama"]["healthy"] is True


# ===================================================================
# check_mcp
# ===================================================================


@dataclass
class _FakeConnection:
    """Mimics MCPConnection for testing."""
    server_name: str
    session: Any = None
    tools: list[Any] = field(default_factory=list)


class TestCheckMCP:
    """MCP server health checks."""

    def test_healthy_connection(self, checker: HealthChecker) -> None:
        conn = _FakeConnection(
            server_name="github",
            session=MagicMock(),
            tools=[MagicMock(), MagicMock()],
        )
        result = checker.check_mcp("github", connections={"github": conn})
        assert result["healthy"] is True
        assert result["tools"] == 2

    def test_no_session(self, checker: HealthChecker) -> None:
        conn = _FakeConnection(server_name="github", session=None, tools=[])
        result = checker.check_mcp("github", connections={"github": conn})
        assert result["healthy"] is False
        assert result["tools"] == 0

    def test_missing_server(self, checker: HealthChecker) -> None:
        result = checker.check_mcp("missing", connections={"other": MagicMock()})
        assert result["healthy"] is False

    def test_none_connections(self, checker: HealthChecker) -> None:
        result = checker.check_mcp("anything")
        assert result["healthy"] is False
        assert result["tools"] == 0


# ===================================================================
# check_all_mcp
# ===================================================================


class TestCheckAllMCP:
    """Aggregation across MCP servers."""

    def test_all_mcp_from_configs(self, checker: HealthChecker) -> None:
        conn = _FakeConnection(
            server_name="github",
            session=MagicMock(),
            tools=[MagicMock()],
        )
        configs = {"github": {"command": "npx"}, "slack": {"command": "npx"}}
        connections = {"github": conn}
        results = checker.check_all_mcp(configs=configs, connections=connections)
        assert results["github"]["healthy"] is True
        assert results["slack"]["healthy"] is False

    def test_empty(self, checker: HealthChecker) -> None:
        results = checker.check_all_mcp()
        assert results == {}


# ===================================================================
# _timed_request — edge cases
# ===================================================================


class TestTimedRequest:
    """Low-level HTTP probe edge cases."""

    def test_4xx_is_healthy(self, checker: HealthChecker) -> None:
        """A 4xx means the server is reachable (just auth-rejected, etc.)."""
        with patch.object(httpx.Client, "head", return_value=_mock_response(403)):
            result = checker.check_provider("anthropic")
        assert result["healthy"] is True

    def test_generic_exception(self, checker: HealthChecker) -> None:
        """Unexpected exceptions are caught and reported."""
        with patch.object(
            httpx.Client, "head",
            side_effect=RuntimeError("boom"),
        ):
            result = checker.check_provider("anthropic")
        assert result["healthy"] is False
        assert "boom" in result["error"]


# ===================================================================
# doctor._format_latency
# ===================================================================


class TestFormatLatency:
    """Test the latency formatting helper in doctor.py."""

    def test_milliseconds(self) -> None:
        from duh.cli.doctor import _format_latency
        assert _format_latency(42) == "42ms"

    def test_seconds(self) -> None:
        from duh.cli.doctor import _format_latency
        assert _format_latency(1500) == "1.5s"

    def test_zero(self) -> None:
        from duh.cli.doctor import _format_latency
        assert _format_latency(0) == "0ms"
