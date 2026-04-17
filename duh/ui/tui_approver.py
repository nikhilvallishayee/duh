"""TUI-compatible approver — shows a Textual modal for permission requests.

Part of ADR-066 P1.  Replaces AutoApprover when the TUI is running with
an interactive modal that blocks the engine's tool-call pipeline until
the user responds.

The approver is wired into Deps.approve just like InteractiveApprover
is wired in the REPL.  It delegates cache logic to SessionPermissionCache.

ADR-073 Wave 1 task 3: An ``asyncio.wait_for`` races the modal against a
configurable timeout (``AppConfig.approval_timeout_seconds``).  On timeout
we auto-deny, cache the decision so repeated requests don't re-prompt,
and surface a warning in the message log.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from duh.kernel.permission_cache import SessionPermissionCache


logger = logging.getLogger(__name__)


# Sentinel used to distinguish the "auto-deny" entry in the cache from a
# user-entered "never" decision.  Both keep the tool denied for the
# session, but only this one short-circuits without surfacing a reason.
_AUTO_DENY_REASON_FMT = "Auto-denied after {timeout}s — no response"


class TUIApprover:
    """Approver that shows a Textual modal for permission requests.

    Parameters
    ----------
    app:
        The running ``DuhApp`` instance (needed to push the modal screen).
    permission_cache:
        Optional ``SessionPermissionCache`` for remembering always/never
        decisions within a session.
    timeout_seconds:
        Auto-deny after this many seconds of no response.  ``None``
        disables the timeout (the modal will wait forever).  Default 60s.
    """

    def __init__(
        self,
        app: Any,
        permission_cache: SessionPermissionCache | None = None,
        timeout_seconds: float | None = 60.0,
    ) -> None:
        self._app = app
        self._cache = permission_cache or SessionPermissionCache()
        # Normalise: non-positive values disable the timeout.
        if timeout_seconds is not None and timeout_seconds <= 0:
            timeout_seconds = None
        self._timeout = timeout_seconds

    @property
    def timeout_seconds(self) -> float | None:
        return self._timeout

    async def check(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Check whether a tool call is permitted.

        1. Consult the session cache first (instant return for always/never).
        2. Otherwise push a ``PermissionModal`` and await the user's choice,
           racing against ``self._timeout``.
        3. Record the decision in the cache.  On timeout: record as "N"
           (never, this session) and surface a system message.
        """
        # --- Check session cache first ---
        cached = self._cache.check(tool_name)
        if cached == "allow":
            return {"allowed": True}
        if cached == "deny":
            return {"allowed": False, "reason": "Denied for this session (cached)"}

        # --- Show modal and wait for response (with optional timeout) ---
        from duh.ui.permission_modal import PermissionModal

        modal_coro = self._app.push_screen_wait(
            PermissionModal(tool_name, tool_input)
        )

        try:
            if self._timeout is None:
                result: str = await modal_coro
            else:
                result = await asyncio.wait_for(modal_coro, timeout=self._timeout)
        except asyncio.TimeoutError:
            # Auto-deny.  Log at WARNING (timeout is expected under some
            # conditions — walk-away user, unattended session).
            timeout_val = self._timeout
            reason = _AUTO_DENY_REASON_FMT.format(timeout=timeout_val)
            logger.warning(
                "TUIApprover: no response for tool %r within %.1fs — auto-denying",
                tool_name, timeout_val,
            )
            # Cache as "never" so subsequent requests for the same tool
            # don't re-prompt (and re-hang) in this session.
            self._cache.record(tool_name, "N")
            # Surface in the TUI message log so the user sees what happened
            # when they return.  Best-effort: any failure here (headless
            # tests, missing log widget) must not mask the auto-deny.
            self._notify_auto_deny(tool_name, timeout_val)
            return {"allowed": False, "reason": reason}

        # --- Record in cache ---
        if result in ("y", "a", "n", "N"):
            self._cache.record(tool_name, result)

        if result in ("y", "a"):
            return {"allowed": True}
        return {"allowed": False, "reason": "User denied"}

    # ----------------------------------------------------------------- helpers

    def _notify_auto_deny(self, tool_name: str, timeout: float) -> None:
        """Push a yellow/warning system message into the TUI message log.

        Best-effort: any exception here is swallowed.  The auto-deny
        itself has already been recorded; this is purely UI feedback.
        We're already running on the Textual event loop (the engine awaits
        our ``check()``), so scheduling a task is safe.
        """
        try:
            from textual.containers import ScrollableContainer
            from textual.widgets import Static

            log = self._app.query_one("#message-log", ScrollableContainer)
            text = (
                f"[yellow]⏱ Permission auto-denied after {timeout:.0f}s"
                f" — no response from user[/yellow]\n"
                f"[dim]tool: {tool_name}[/dim]"
            )
            widget = Static(text, classes="system-message warning")
            # log.mount() returns an awaitable; fire-and-forget it so the
            # caller's happy path returns the auto-deny immediately.
            asyncio.ensure_future(log.mount(widget))
        except Exception as exc:  # noqa: BLE001 — UI feedback is best-effort
            logger.debug("TUIApprover: failed to surface auto-deny message: %s", exc)
