"""MCP push ingress listener — ADR-031 §B.2.

.. note::

    **Stub implementation.** The current MCP integration in
    ``duh/adapters/mcp_transports.py`` (StreamableHTTPTransport,
    WebSocketTransport, etc.) treats ``notifications/*`` JSON-RPC
    frames as log-only — they are emitted to the logger and dropped.
    There is no public subscription API on the existing MCP client
    surface.

    This listener defines the *shape* the integration will take so
    callers in :mod:`duh.duhwave.ingress` and the subscription matcher
    can be wired today. Once the MCP client gains an
    ``add_notification_handler(server_name, callback)`` (or equivalent
    async iterator), :meth:`MCPPushListener.start` will register the
    callback and translate each incoming notification into a Trigger.

    Until then, :meth:`start` succeeds but emits no triggers; tests can
    drive the manual seam (:mod:`duh.duhwave.ingress.manual`) instead.

    TODO(adr-031-mcp-push): wire to the MCP client once a notification
    subscription API exists. Integration point lives in
    ``duh/adapters/mcp_transports.py`` — search for the ``treated as
    notifications and logged`` comment.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from duh.duhwave.ingress.triggers import Trigger, TriggerKind, TriggerLog

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MCPSubscription:
    """Declarative MCP-push subscription.

    ``server_name`` matches a configured MCP server entry. ``method``
    is the JSON-RPC ``notifications/<topic>`` method (e.g.
    ``notifications/resources/updated``); empty string means "all
    notifications from this server".
    """

    server_name: str
    method: str = ""


class MCPPushListener:
    """Translate MCP ``notifications/*`` frames into Triggers.

    Currently a no-op stub — see module docstring.
    """

    def __init__(
        self,
        log: TriggerLog,
        subscriptions: list[MCPSubscription] | None = None,
        mcp_client: object | None = None,
    ) -> None:
        self._log = log
        self._subscriptions: list[MCPSubscription] = list(subscriptions or [])
        self._mcp_client = mcp_client
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    async def start(self) -> None:
        """Register notification handlers with the MCP client.

        Stub: logs a one-time warning and returns. Idempotent.
        """
        if self._running:
            return
        self._running = True
        if self._mcp_client is None:
            logger.warning(
                "MCPPushListener started without an mcp_client — no triggers "
                "will be emitted (see module docstring TODO)"
            )
            return
        # TODO(adr-031-mcp-push): when the MCP client exposes a
        # notification subscription API, register a handler per
        # subscription that calls self._on_notification.
        logger.warning(
            "MCPPushListener: mcp_client provided but notification "
            "subscription API not yet implemented — no triggers will be "
            "emitted"
        )

    async def stop(self) -> None:
        """Cancel any background tasks. Idempotent."""
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    def _on_notification(
        self,
        server_name: str,
        method: str,
        params: dict[str, object],
    ) -> None:
        """Translate one notification into a Trigger.

        Public for future direct invocation from the MCP client; the
        notification subscription API will call this once wired.
        """
        trigger = Trigger(
            kind=TriggerKind.MCP_PUSH,
            source=f"mcp:{server_name}:{method}",
            payload=dict(params),
        )
        try:
            self._log.append(trigger)
        except Exception:  # pragma: no cover
            logger.exception(
                "failed to append mcp_push trigger from %s/%s",
                server_name,
                method,
            )
