"""Network policy -- controls network access for sandboxed commands.

Three modes:
    FULL    -- All network requests allowed (default)
    LIMITED -- Only safe HTTP methods (GET, HEAD, OPTIONS) allowed
    NONE    -- No network access at all

Limited mode is enforced at the application level (in WebFetch tool).
Full/None are enforced at the sandbox level (Seatbelt deny network*,
or Landlock -- though Landlock v1 doesn't restrict network, only fs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse


class NetworkMode(Enum):
    """Network access modes."""
    FULL = "full"
    LIMITED = "limited"
    NONE = "none"


# HTTP methods considered safe (non-mutating)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class NetworkPolicy:
    """Network access policy for sandboxed environments.

    Enforced at two levels:
    1. Sandbox level: network allowed or denied (Seatbelt/Landlock)
    2. Application level: method filtering in WebFetch (LIMITED mode)
    """
    mode: NetworkMode = NetworkMode.FULL
    allowed_hosts: list[str] = field(default_factory=list)
    denied_hosts: list[str] = field(default_factory=list)

    def _extract_host(self, url: str) -> str:
        """Extract hostname from a URL."""
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            return host.lower()
        except Exception:
            return ""

    def is_request_allowed(self, method: str, url: str) -> bool:
        """Check if a network request is allowed under this policy.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL being requested.

        Returns:
            True if the request is allowed.
        """
        # NONE mode blocks everything
        if self.mode == NetworkMode.NONE:
            return False

        # LIMITED mode: only safe methods
        if self.mode == NetworkMode.LIMITED:
            if method.upper() not in _SAFE_METHODS:
                return False

        # Check host filters
        host = self._extract_host(url)

        # Denied hosts always block (checked first)
        if self.denied_hosts:
            for denied in self.denied_hosts:
                if host == denied.lower() or host.endswith(f".{denied.lower()}"):
                    return False

        # If allowed_hosts is set, only those are permitted
        if self.allowed_hosts:
            for allowed in self.allowed_hosts:
                if host == allowed.lower() or host.endswith(f".{allowed.lower()}"):
                    return True
            return False

        return True

    def to_sandbox_flag(self) -> bool:
        """Convert to a boolean for SandboxPolicy.network_allowed.

        FULL and LIMITED both need network at the OS level.
        LIMITED filtering happens at the application layer.
        """
        return self.mode != NetworkMode.NONE
