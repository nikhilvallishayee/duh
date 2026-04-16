"""WebFetchTool — fetch a URL and return text content."""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from duh.kernel.tool import ToolContext, ToolResult
from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.security.trifecta import Capability


def _wrap_network_body(text: str) -> UntrustedStr:
    """Tag network response body as NETWORK."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.NETWORK)


_MAX_CONTENT_BYTES = 100_000  # 100 KB
_DEFAULT_TIMEOUT = 30  # seconds

# Hostnames that cloud providers use for instance metadata services.
_BLOCKED_METADATA_HOSTNAMES: frozenset[str] = frozenset({
    "metadata.google.internal",
    "metadata.gcp.internal",
    "metadata",  # short alias sometimes used inside GCP
})


def _is_private_ip(addr: str) -> bool:
    """Return True if *addr* is a private, loopback, link-local, or reserved IP.

    Uses the ``ipaddress`` stdlib module which covers:
    - Loopback: 127.0.0.0/8, ::1
    - Private: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
    - Link-local: 169.254.0.0/16 (AWS metadata), fe80::/10
    - Reserved: 0.0.0.0/8, and various IANA reserved blocks
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        # If we can't parse it, treat as suspicious and block.
        return True

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified  # 0.0.0.0, ::
    )


def _validate_url_ssrf(url: str) -> None:
    """Raise ``ValueError`` if *url* targets a private/internal address (SSRF protection).

    Steps:
    1. Parse the URL and extract the hostname.
    2. Check hostname against known cloud metadata hostnames.
    3. Resolve hostname to IP(s) via ``socket.getaddrinfo``.
    4. Check every resolved IP against ``_is_private_ip``.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")

    if not hostname:
        raise ValueError("URL has no hostname")

    # Block cloud metadata hostnames directly (before DNS resolution).
    if hostname in _BLOCKED_METADATA_HOSTNAMES:
        raise ValueError(
            f"SSRF blocked: hostname {hostname!r} is a cloud metadata endpoint"
        )

    # Resolve hostname to IP addresses.
    try:
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for {hostname!r}: {exc}") from exc

    if not addrinfos:
        raise ValueError(f"DNS resolution returned no results for {hostname!r}")

    for family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        if _is_private_ip(ip_str):
            raise ValueError(
                f"SSRF blocked: {hostname!r} resolves to private/internal address {ip_str}"
            )


def _strip_html(html: str) -> str:
    """Crude but effective HTML-to-text: strip tags, collapse whitespace."""
    # Remove script and style blocks entirely
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common HTML entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse runs of whitespace into single spaces, preserve newlines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class WebFetchTool:
    """Fetch a URL and return the text content."""

    name = "WebFetch"

    def __init__(
        self,
        *,
        network_policy: "NetworkPolicy | None" = None,
    ) -> None:
        from duh.security.network_policy import NetworkPolicy  # noqa: F811
        self._network_policy: NetworkPolicy | None = network_policy
    capabilities = Capability.NETWORK_EGRESS
    description = "Fetch a URL and return its text content with HTML tags stripped."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch.",
            },
            "prompt": {
                "type": "string",
                "description": "Optional hint for what to extract from the page.",
            },
        },
        "required": ["url"],
    }

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        url = input.get("url", "").strip()
        prompt = input.get("prompt", "")

        if not url:
            return ToolResult(output="url is required", is_error=True)

        # Network policy check
        if self._network_policy is not None:
            allowed, reason = self._network_policy.check(url)
            if not allowed:
                return ToolResult(output=reason, is_error=True)

        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            return ToolResult(
                output=f"Invalid URL: must start with http:// or https://: {url}",
                is_error=True,
            )

        # SSRF protection — block private/internal IPs
        try:
            _validate_url_ssrf(url)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(_DEFAULT_TIMEOUT),
            ) as client:
                response = await client.get(url, headers={
                    "User-Agent": "duh-cli/0.1 (WebFetch tool)",
                    "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
                })
                response.raise_for_status()
        except httpx.TimeoutException:
            return ToolResult(
                output=f"Request timed out after {_DEFAULT_TIMEOUT}s: {url}",
                is_error=True,
            )
        except httpx.ConnectError as exc:
            return ToolResult(
                output=f"Connection failed (DNS or network error): {exc}",
                is_error=True,
            )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                output=f"HTTP {exc.response.status_code} error fetching {url}",
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                output=f"HTTP error fetching {url}: {exc}",
                is_error=True,
            )

        raw = response.text
        content_type = response.headers.get("content-type", "")

        # Strip HTML if it looks like HTML
        if "html" in content_type or raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
            text = _strip_html(raw)
        else:
            text = raw

        # Truncate if over size limit
        truncated = False
        if len(text) > _MAX_CONTENT_BYTES:
            text = text[:_MAX_CONTENT_BYTES]
            truncated = True

        # Build output
        parts: list[str] = []
        if prompt:
            parts.append(f"[Extraction hint: {prompt}]")
        parts.append(text)
        if truncated:
            parts.append(f"\n\n[Content truncated at {_MAX_CONTENT_BYTES // 1000}KB]")

        return ToolResult(
            output="\n".join(parts),
            metadata={
                "url": str(response.url),
                "status_code": response.status_code,
                "content_type": content_type,
                "truncated": truncated,
                "length": len(text),
            },
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
