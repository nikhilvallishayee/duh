"""WebFetchTool — fetch a URL and return text content."""

from __future__ import annotations

import re
from typing import Any

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
