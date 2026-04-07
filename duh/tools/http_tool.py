"""HTTPTool — send HTTP requests for API testing."""

from __future__ import annotations

import json
from typing import Any

import httpx

from duh.kernel.tool import ToolContext, ToolResult

_MAX_BODY_BYTES = 10_000  # 10 KB — truncate response bodies beyond this
_DEFAULT_TIMEOUT = 30  # seconds
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})

# Headers worth surfacing in the tool output
_KEY_HEADERS = frozenset({
    "content-type",
    "content-length",
    "location",
    "retry-after",
    "x-request-id",
    "x-ratelimit-remaining",
    "www-authenticate",
    "etag",
    "cache-control",
})


def _is_json_content(content_type: str) -> bool:
    """Return True if the content-type suggests JSON."""
    ct = content_type.lower()
    return "application/json" in ct or "+json" in ct


def _format_body(raw: str, content_type: str) -> tuple[str, bool]:
    """Format the response body.  Returns (formatted, truncated)."""
    # Try JSON pretty-print if the content-type looks like JSON
    if _is_json_content(content_type):
        try:
            parsed = json.loads(raw)
            raw = json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            pass  # leave as-is

    truncated = len(raw) > _MAX_BODY_BYTES
    if truncated:
        raw = raw[:_MAX_BODY_BYTES]
    return raw, truncated


def _pick_headers(headers: httpx.Headers) -> dict[str, str]:
    """Extract the key headers worth showing to the user."""
    result: dict[str, str] = {}
    for name in headers:
        if name.lower() in _KEY_HEADERS:
            result[name] = headers[name]
    return result


class HTTPTool:
    """Send an HTTP request and return the response."""

    name = "HTTP"
    description = (
        "Send an HTTP request (GET/POST/PUT/DELETE/PATCH) and return the "
        "status code, key headers, and response body.  Useful for testing "
        "REST APIs.  JSON responses are auto-detected and pretty-printed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "description": "HTTP method.",
            },
            "url": {
                "type": "string",
                "description": "The URL to send the request to.",
            },
            "headers": {
                "type": "object",
                "description": "Optional request headers (e.g. Authorization).",
                "additionalProperties": {"type": "string"},
            },
            "body": {
                "type": "string",
                "description": "Optional request body (sent as-is).",
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds (default 30).",
            },
        },
        "required": ["method", "url"],
    }

    @property
    def is_read_only(self) -> bool:
        return False  # POST/PUT/DELETE can mutate remote state

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        method = (input.get("method") or "").upper().strip()
        url = (input.get("url") or "").strip()
        req_headers: dict[str, str] = input.get("headers") or {}
        body: str | None = input.get("body")
        timeout: int = input.get("timeout") or _DEFAULT_TIMEOUT

        # -- validation --
        if not method:
            return ToolResult(output="method is required", is_error=True)
        if method not in _ALLOWED_METHODS:
            return ToolResult(
                output=f"Invalid method: {method}. Must be one of {', '.join(sorted(_ALLOWED_METHODS))}.",
                is_error=True,
            )
        if not url:
            return ToolResult(output="url is required", is_error=True)
        if not url.startswith(("http://", "https://")):
            return ToolResult(
                output=f"Invalid URL: must start with http:// or https://: {url}",
                is_error=True,
            )
        if timeout < 1:
            timeout = _DEFAULT_TIMEOUT

        # -- build request kwargs --
        kwargs: dict[str, Any] = {
            "method": method,
            "url": url,
            "headers": req_headers,
        }
        if body is not None:
            kwargs["content"] = body

        # -- fire request --
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(timeout),
            ) as client:
                response = await client.request(**kwargs)
        except httpx.TimeoutException:
            return ToolResult(
                output=f"Request timed out after {timeout}s: {method} {url}",
                is_error=True,
            )
        except httpx.ConnectError as exc:
            return ToolResult(
                output=f"Connection failed (DNS or network error): {exc}",
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                output=f"HTTP error: {exc}",
                is_error=True,
            )

        # -- format output --
        content_type = response.headers.get("content-type", "")
        raw_body = response.text
        formatted_body, truncated = _format_body(raw_body, content_type)
        key_headers = _pick_headers(response.headers)

        parts: list[str] = [
            f"HTTP {response.status_code}",
        ]
        if key_headers:
            header_lines = "\n".join(f"  {k}: {v}" for k, v in key_headers.items())
            parts.append(f"Headers:\n{header_lines}")
        parts.append(f"Body:\n{formatted_body}")
        if truncated:
            parts.append(f"\n[Body truncated at {_MAX_BODY_BYTES // 1000}KB]")

        return ToolResult(
            output="\n\n".join(parts),
            metadata={
                "method": method,
                "url": str(response.url),
                "status_code": response.status_code,
                "content_type": content_type,
                "truncated": truncated,
                "body_length": len(raw_body),
            },
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
