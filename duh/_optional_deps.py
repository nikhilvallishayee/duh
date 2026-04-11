"""Lazy guards for optional third-party packages.

Centralises the try/except import + guard function pattern so that
multiple modules don't each duplicate the same boilerplate.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# websockets
# ---------------------------------------------------------------------------

try:
    import websockets  # noqa: F401

    ws_available = True
except ImportError:
    websockets = None  # type: ignore[assignment]
    ws_available = False


def require_websockets() -> None:
    """Raise RuntimeError if the ``websockets`` package is not installed."""
    if not ws_available:
        raise RuntimeError(
            "The 'websockets' package is required for WebSocket transport. "
            "Install it with: pip install websockets"
        )


# ---------------------------------------------------------------------------
# httpx
# ---------------------------------------------------------------------------

try:
    import httpx  # noqa: F401

    httpx_available = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    httpx_available = False


def require_httpx() -> None:
    """Raise RuntimeError if the ``httpx`` package is not installed."""
    if not httpx_available:
        raise RuntimeError(
            "The 'httpx' package is required for SSE/HTTP MCP transport. "
            "Install it with: pip install httpx"
        )
