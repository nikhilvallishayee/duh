"""TUI-compatible approver — shows a Textual modal for permission requests.

Part of ADR-066 P1.  Replaces AutoApprover when the TUI is running with
an interactive modal that blocks the engine's tool-call pipeline until
the user responds.

The approver is wired into Deps.approve just like InteractiveApprover
is wired in the REPL.  It delegates cache logic to SessionPermissionCache.
"""

from __future__ import annotations

from typing import Any

from duh.kernel.permission_cache import SessionPermissionCache


class TUIApprover:
    """Approver that shows a Textual modal for permission requests.

    Parameters
    ----------
    app:
        The running ``DuhApp`` instance (needed to push the modal screen).
    permission_cache:
        Optional ``SessionPermissionCache`` for remembering always/never
        decisions within a session.
    """

    def __init__(
        self,
        app: Any,
        permission_cache: SessionPermissionCache | None = None,
    ) -> None:
        self._app = app
        self._cache = permission_cache or SessionPermissionCache()

    async def check(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Check whether a tool call is permitted.

        1. Consult the session cache first (instant return for always/never).
        2. Otherwise push a ``PermissionModal`` and await the user's choice.
        3. Record the decision in the cache.
        """
        # --- Check session cache first ---
        cached = self._cache.check(tool_name)
        if cached == "allow":
            return {"allowed": True}
        if cached == "deny":
            return {"allowed": False, "reason": "Denied for this session (cached)"}

        # --- Show modal and wait for response ---
        from duh.ui.permission_modal import PermissionModal

        result: str = await self._app.push_screen_wait(
            PermissionModal(tool_name, tool_input)
        )

        # --- Record in cache ---
        if result in ("y", "a", "n", "N"):
            self._cache.record(tool_name, result)

        if result in ("y", "a"):
            return {"allowed": True}
        return {"allowed": False, "reason": "User denied"}
