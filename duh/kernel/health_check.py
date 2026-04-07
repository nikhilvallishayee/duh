"""Health checking for providers and MCP servers.

Provides a HealthChecker class that tests connectivity to configured
providers (Anthropic, OpenAI, Ollama) and MCP servers, returning
structured results with latency measurements.

Used by:
- ``duh doctor`` for connectivity checks
- ``/health`` REPL command for on-demand diagnostics
- Graceful degradation (disabling unhealthy providers at runtime)
"""

from __future__ import annotations

import time
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

ProviderHealth = dict[str, Any]  # {"healthy": bool, "latency_ms": int, "error": str|None}
MCPHealth = dict[str, Any]       # {"healthy": bool, "tools": int}


# ---------------------------------------------------------------------------
# HealthChecker
# ---------------------------------------------------------------------------


class HealthChecker:
    """Periodically (or on-demand) check provider and MCP server health.

    Stateless by default -- each ``check_*`` call performs a fresh probe.
    The REPL may cache results for a short TTL if desired.
    """

    def __init__(self, *, timeout: float = 5.0) -> None:
        self._timeout = timeout

    # -- Provider checks ----------------------------------------------------

    def check_provider(self, provider_name: str) -> ProviderHealth:
        """Check connectivity to a named provider.

        Returns ``{"healthy": bool, "latency_ms": int, "error": str | None}``.

        Supported providers:
        - ``"anthropic"`` -- HEAD request to api.anthropic.com
        - ``"openai"``    -- HEAD request to api.openai.com
        - ``"ollama"``    -- GET to localhost:11434/api/tags
        """
        dispatch = {
            "anthropic": self._check_anthropic,
            "openai": self._check_openai,
            "ollama": self._check_ollama,
        }

        fn = dispatch.get(provider_name)
        if fn is None:
            return {"healthy": False, "latency_ms": 0, "error": f"Unknown provider: {provider_name}"}

        return fn()

    def check_all_providers(self) -> dict[str, ProviderHealth]:
        """Check all known providers. Returns {name: ProviderHealth}."""
        results: dict[str, ProviderHealth] = {}
        for name in ("anthropic", "openai", "ollama"):
            results[name] = self.check_provider(name)
        return results

    # -- MCP checks ---------------------------------------------------------

    def check_mcp(self, server_name: str, *, connections: dict[str, Any] | None = None) -> MCPHealth:
        """Check whether an MCP server is healthy.

        If *connections* is provided (a dict of server_name -> MCPConnection),
        we check whether the connection exists and has tools discovered.
        Otherwise we report the server as unreachable.

        Returns ``{"healthy": bool, "tools": int}``.
        """
        if connections is None:
            return {"healthy": False, "tools": 0}

        conn = connections.get(server_name)
        if conn is None:
            return {"healthy": False, "tools": 0}

        # Check that the session object exists (meaning it's connected)
        session = getattr(conn, "session", None)
        tools = getattr(conn, "tools", [])
        healthy = session is not None and len(tools) > 0
        return {"healthy": healthy, "tools": len(tools)}

    def check_all_mcp(self, *, connections: dict[str, Any] | None = None,
                       configs: dict[str, Any] | None = None) -> dict[str, MCPHealth]:
        """Check all configured MCP servers.

        *configs* is the dict of configured server names (from Config.mcp_servers).
        *connections* is the live connection dict from MCPExecutor._connections.
        """
        results: dict[str, MCPHealth] = {}
        names: set[str] = set()
        if configs:
            # Extract server names from the mcpServers config structure
            mcp_servers = configs.get("mcpServers", configs)
            names.update(mcp_servers.keys())
        if connections:
            names.update(connections.keys())

        for name in sorted(names):
            results[name] = self.check_mcp(name, connections=connections)
        return results

    # -- Internal probes ----------------------------------------------------

    def _timed_request(self, method: str, url: str, **kwargs: Any) -> ProviderHealth:
        """Execute an HTTP request and return a ProviderHealth dict."""
        start = time.monotonic()
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = getattr(client, method)(url, **kwargs)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            # Any 2xx/3xx/4xx with a response is "reachable" -- the server is up.
            # Only 5xx or connection errors mean unhealthy.
            healthy = resp.status_code < 500
            error = None if healthy else f"HTTP {resp.status_code}"
            return {"healthy": healthy, "latency_ms": elapsed_ms, "error": error}
        except httpx.ConnectError as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"healthy": False, "latency_ms": elapsed_ms, "error": f"Connection refused: {exc}"}
        except httpx.TimeoutException:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"healthy": False, "latency_ms": elapsed_ms, "error": "Timeout"}
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"healthy": False, "latency_ms": elapsed_ms, "error": str(exc)}

    def _check_anthropic(self) -> ProviderHealth:
        return self._timed_request("head", "https://api.anthropic.com")

    def _check_openai(self) -> ProviderHealth:
        return self._timed_request("head", "https://api.openai.com")

    def _check_ollama(self) -> ProviderHealth:
        return self._timed_request("get", "http://localhost:11434/api/tags")
