"""Runtime hook bindings — registers security callbacks on PRE/POST_TOOL_USE
and SESSION_START/END events using ADR-045 HookResponse blocking semantics.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from duh.hooks import HookConfig, HookEvent, HookRegistry, HookResponse, HookType
from duh.security.config import SecurityPolicy
from duh.security.engine import FindingStore
from duh.security.exceptions import ExceptionStore
from duh.security.policy import ToolUseEvent, resolve

logger = logging.getLogger(__name__)


class ConsoleLike(Protocol):
    def notify(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def summary(self, payload: Any) -> None: ...


@dataclass
class SecurityContext:
    policy: SecurityPolicy
    findings: FindingStore
    exceptions: ExceptionStore
    console: ConsoleLike
    project_root: Path


def install(*, registry: HookRegistry, ctx: SecurityContext) -> None:
    if not ctx.policy.runtime.enabled:
        return

    async def pre_tool_use(event: HookEvent, data: dict[str, Any]) -> HookResponse:
        tool_event = data.get("event")
        if tool_event is None:
            return HookResponse(decision="continue")
        try:
            decision = await asyncio.wait_for(
                asyncio.to_thread(
                    resolve, tool_event, ctx.policy, ctx.findings, ctx.exceptions,
                ),
                timeout=ctx.policy.runtime.resolver_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning("security resolver timed out, fail-open")
            ctx.console.notify("duh-sec: resolver timeout — allowing tool call")
            return HookResponse(decision="continue")

        if decision.action == "block" and ctx.policy.runtime.block_pre_tool_use:
            return HookResponse(decision="block", message=decision.remediation or decision.reason)
        if decision.action == "warn":
            ctx.console.warn(decision.reason)
        return HookResponse(decision="continue")

    async def post_tool_use(event: HookEvent, data: dict[str, Any]) -> HookResponse:
        return HookResponse(decision="continue")

    async def session_start(event: HookEvent, data: dict[str, Any]) -> HookResponse:
        if ctx.policy.runtime.session_start_audit:
            expiring = ctx.exceptions.expiring_within(days=7)
            if expiring:
                ctx.console.notify(
                    f"{len(expiring)} security exception(s) expire in 7 days"
                )
        return HookResponse(decision="continue")

    async def session_end(event: HookEvent, data: dict[str, Any]) -> HookResponse:
        if ctx.policy.runtime.session_end_summary:
            delta = ctx.findings.all()
            if delta:
                ctx.console.summary(delta)
        return HookResponse(decision="continue")

    bindings = [
        (HookEvent.PRE_TOOL_USE, pre_tool_use),
        (HookEvent.POST_TOOL_USE, post_tool_use),
        (HookEvent.SESSION_START, session_start),
        (HookEvent.SESSION_END, session_end),
    ]
    for ev, cb in bindings:
        registry.register(HookConfig(
            event=ev,
            hook_type=HookType.FUNCTION,
            name=f"duh-security-{ev.value}",
            callback=cb,
            timeout=ctx.policy.runtime.resolver_timeout_s,
        ))
