"""Event ingress — ADR-031 §B.

External events (webhooks, file watches, cron, MCP push) materialise as
:class:`Trigger` records and spawn :class:`~duh.duhwave.task.Task`
records via subscription matching declared in the topology (ADR-032).

Listener implementations live alongside this module:

- :mod:`duh.duhwave.ingress.webhook`   — aiohttp HTTP listener (127.0.0.1)
- :mod:`duh.duhwave.ingress.filewatch` — ``watchfiles`` debounced
- :mod:`duh.duhwave.ingress.cron`      — ``croniter``
- :mod:`duh.duhwave.ingress.mcp_push`  — MCP ``notifications/*`` channel
- :mod:`duh.duhwave.ingress.manual`    — Unix-socket seam for tests
- :mod:`duh.duhwave.ingress.matcher`   — Subscription → agent routing
"""
from __future__ import annotations

from duh.duhwave.ingress.cron import CronListener
from duh.duhwave.ingress.filewatch import FileWatchListener
from duh.duhwave.ingress.manual import ManualSeam
from duh.duhwave.ingress.matcher import SubscriptionMatcher
from duh.duhwave.ingress.mcp_push import MCPPushListener, MCPSubscription
from duh.duhwave.ingress.triggers import Trigger, TriggerKind, TriggerLog
from duh.duhwave.ingress.webhook import WebhookListener

__all__ = [
    "CronListener",
    "FileWatchListener",
    "ManualSeam",
    "MCPPushListener",
    "MCPSubscription",
    "SubscriptionMatcher",
    "Trigger",
    "TriggerKind",
    "TriggerLog",
    "WebhookListener",
]
