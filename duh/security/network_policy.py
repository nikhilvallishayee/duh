"""Centralized network egress control (ADR-072 P0).

All HTTP requests from tools should go through this policy.
Configurable allow/deny lists for hostnames.

This complements the sandbox-level NetworkPolicy in
``duh.adapters.sandbox.network`` which controls OS-level network
access.  This module operates at the *application* layer, gating
individual tool calls before they reach httpx.
"""

from __future__ import annotations

from urllib.parse import urlparse


class NetworkPolicy:
    """Centralized network access control.

    All HTTP requests from tools should go through this policy.
    Configurable allow/deny lists for hostnames.
    """

    def __init__(
        self,
        allowed_hosts: list[str] | None = None,
        denied_hosts: list[str] | None = None,
    ) -> None:
        self._allowed: set[str] = {h.lower() for h in (allowed_hosts or [])}
        self._denied: set[str] = {h.lower() for h in (denied_hosts or [])}

    def check(self, url: str) -> tuple[bool, str]:
        """Return ``(allowed, reason)``.

        Rules (evaluated in order):
        1. If the host is in the deny list -> blocked.
        2. If an allow list is configured and the host is NOT in it -> blocked.
        3. Otherwise -> allowed.
        """
        host = (urlparse(url).hostname or "").lower()

        if self._denied and host in self._denied:
            return False, f"Host {host} is denied by network policy"

        if self._allowed and host not in self._allowed:
            return False, f"Host {host} is not in the allowed list"

        return True, ""
