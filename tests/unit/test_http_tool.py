"""Tests for HTTPTool — HTTP API testing tool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.http_tool import HTTPTool, _format_body, _is_json_content, _pick_headers


def ctx() -> ToolContext:
    return ToolContext(cwd=".")


def _mock_response(
    text: str = "",
    status_code: int = 200,
    content_type: str = "text/plain",
    url: str = "https://api.example.com",
    headers: dict[str, str] | None = None,
    method: str = "GET",
) -> httpx.Response:
    """Build a mock httpx.Response."""
    h = {"content-type": content_type}
    if headers:
        h.update(headers)
    return httpx.Response(
        status_code=status_code,
        headers=h,
        text=text,
        request=httpx.Request(method, url),
    )


def _mock_client(response: httpx.Response) -> AsyncMock:
    """Build an AsyncMock that acts like httpx.AsyncClient context manager."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.request = AsyncMock(return_value=response)
    return client


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestHTTPToolProtocol:

    def test_satisfies_tool_protocol(self):
        assert isinstance(HTTPTool(), Tool)

    def test_name(self):
        assert HTTPTool().name == "HTTP"

    def test_description_non_empty(self):
        assert HTTPTool().description

    def test_input_schema_structure(self):
        schema = HTTPTool().input_schema
        assert schema["type"] == "object"
        assert "method" in schema["properties"]
        assert "url" in schema["properties"]
        assert "headers" in schema["properties"]
        assert "body" in schema["properties"]
        assert "timeout" in schema["properties"]
        assert set(schema["required"]) == {"method", "url"}

    def test_is_not_read_only(self):
        # POST/PUT/DELETE mutate remote state
        assert HTTPTool().is_read_only is False

    def test_is_not_destructive(self):
        assert HTTPTool().is_destructive is False

    async def test_check_permissions(self):
        result = await HTTPTool().check_permissions({}, ctx())
        assert result["allowed"] is True


# ===========================================================================
# Input validation
# ===========================================================================


class TestHTTPToolValidation:
    tool = HTTPTool()

    async def test_missing_method_is_error(self):
        result = await self.tool.call({"url": "https://example.com"}, ctx())
        assert result.is_error is True
        assert "method" in result.output.lower()

    async def test_empty_method_is_error(self):
        result = await self.tool.call({"method": "", "url": "https://x.com"}, ctx())
        assert result.is_error is True
        assert "method" in result.output.lower()

    async def test_invalid_method_is_error(self):
        result = await self.tool.call({"method": "TRACE", "url": "https://x.com"}, ctx())
        assert result.is_error is True
        assert "TRACE" in result.output

    async def test_missing_url_is_error(self):
        result = await self.tool.call({"method": "GET"}, ctx())
        assert result.is_error is True
        assert "url" in result.output.lower()

    async def test_empty_url_is_error(self):
        result = await self.tool.call({"method": "GET", "url": ""}, ctx())
        assert result.is_error is True
        assert "url" in result.output.lower()

    async def test_no_scheme_is_error(self):
        result = await self.tool.call({"method": "GET", "url": "example.com/api"}, ctx())
        assert result.is_error is True
        assert "http" in result.output.lower()

    async def test_ftp_scheme_is_error(self):
        result = await self.tool.call({"method": "GET", "url": "ftp://x.com"}, ctx())
        assert result.is_error is True
        assert "http" in result.output.lower()


# ===========================================================================
# Successful requests
# ===========================================================================


class TestHTTPToolSuccess:
    tool = HTTPTool()

    async def test_get_plain_text(self):
        resp = _mock_response("Hello, API!", status_code=200)
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com/health"}, ctx()
            )
        assert result.is_error is False
        assert "HTTP 200" in result.output
        assert "Hello, API!" in result.output
        assert result.metadata["status_code"] == 200
        assert result.metadata["method"] == "GET"

    async def test_post_with_body(self):
        resp = _mock_response('{"id": 1}', status_code=201, content_type="application/json")
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {
                    "method": "POST",
                    "url": "https://api.example.com/items",
                    "headers": {"Content-Type": "application/json"},
                    "body": '{"name": "test"}',
                },
                ctx(),
            )
        assert result.is_error is False
        assert "HTTP 201" in result.output
        # The body kwarg should have been passed as content
        call_kwargs = client.request.call_args
        assert call_kwargs.kwargs.get("content") == '{"name": "test"}'

    async def test_delete_request(self):
        resp = _mock_response("", status_code=204)
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "DELETE", "url": "https://api.example.com/items/1"}, ctx()
            )
        assert result.is_error is False
        assert "HTTP 204" in result.output
        assert result.metadata["method"] == "DELETE"

    async def test_bearer_auth_header_forwarded(self):
        resp = _mock_response('{"user": "me"}', content_type="application/json")
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {
                    "method": "GET",
                    "url": "https://api.example.com/me",
                    "headers": {"Authorization": "Bearer tok_123"},
                },
                ctx(),
            )
        assert result.is_error is False
        call_kwargs = client.request.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer tok_123"

    async def test_method_case_insensitive(self):
        resp = _mock_response("ok")
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "get", "url": "https://api.example.com"}, ctx()
            )
        assert result.is_error is False
        call_kwargs = client.request.call_args
        assert call_kwargs.kwargs["method"] == "GET"

    async def test_custom_timeout_used(self):
        resp = _mock_response("ok")
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client) as mock_cls:
            await self.tool.call(
                {"method": "GET", "url": "https://api.example.com", "timeout": 5}, ctx()
            )
        # AsyncClient was called with timeout=5
        call_args = mock_cls.call_args
        assert call_args.kwargs["timeout"] == httpx.Timeout(5)


# ===========================================================================
# JSON auto-detection and pretty-printing
# ===========================================================================


class TestHTTPToolJSON:
    tool = HTTPTool()

    async def test_json_response_pretty_printed(self):
        raw_json = '{"a":1,"b":[2,3]}'
        resp = _mock_response(raw_json, content_type="application/json")
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com"}, ctx()
            )
        assert result.is_error is False
        # Should have indented JSON
        assert '"a": 1' in result.output
        assert '"b": [\n' in result.output

    async def test_json_subtype_detected(self):
        """Content-Type like application/vnd.api+json is also pretty-printed."""
        raw_json = '{"x":1}'
        resp = _mock_response(raw_json, content_type="application/vnd.api+json; charset=utf-8")
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com"}, ctx()
            )
        assert '"x": 1' in result.output

    async def test_broken_json_returned_raw(self):
        """If content-type says JSON but body isn't valid, return raw."""
        resp = _mock_response("not json{", content_type="application/json")
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com"}, ctx()
            )
        assert result.is_error is False
        assert "not json{" in result.output


# ===========================================================================
# Body truncation
# ===========================================================================


class TestHTTPToolTruncation:
    tool = HTTPTool()

    async def test_large_body_truncated(self):
        large = "x" * 20_000  # 20 KB > 10 KB limit
        resp = _mock_response(large)
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com"}, ctx()
            )
        assert result.is_error is False
        assert result.metadata["truncated"] is True
        assert "truncated" in result.output.lower()
        assert result.metadata["body_length"] == 20_000

    async def test_small_body_not_truncated(self):
        resp = _mock_response("short body")
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com"}, ctx()
            )
        assert result.metadata["truncated"] is False
        assert "truncated" not in result.output.lower()


# ===========================================================================
# Key headers extracted
# ===========================================================================


class TestHTTPToolHeaders:
    tool = HTTPTool()

    async def test_key_headers_shown(self):
        resp = _mock_response(
            "ok",
            headers={
                "x-request-id": "req-abc",
                "x-ratelimit-remaining": "42",
                "x-custom-irrelevant": "hidden",
            },
        )
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com"}, ctx()
            )
        assert "req-abc" in result.output
        assert "42" in result.output
        # Custom non-key header should NOT appear
        assert "hidden" not in result.output


# ===========================================================================
# Error handling
# ===========================================================================


class TestHTTPToolErrors:
    tool = HTTPTool()

    async def test_timeout_error(self):
        client = _mock_client(_mock_response())
        client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com"}, ctx()
            )
        assert result.is_error is True
        assert "timed out" in result.output.lower()

    async def test_connection_error(self):
        client = _mock_client(_mock_response())
        client.request = AsyncMock(side_effect=httpx.ConnectError("DNS failed"))
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://no-such-host.invalid"}, ctx()
            )
        assert result.is_error is True
        assert "connection failed" in result.output.lower()

    async def test_generic_http_error(self):
        client = _mock_client(_mock_response())
        client.request = AsyncMock(side_effect=httpx.HTTPError("something broke"))
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com"}, ctx()
            )
        assert result.is_error is True
        assert "http error" in result.output.lower()

    async def test_non_2xx_not_raised_but_reported(self):
        """Unlike WebFetch, HTTP tool does NOT call raise_for_status — it
        returns any status code as a legitimate result so the user can inspect it."""
        resp = _mock_response("Not Found", status_code=404)
        client = _mock_client(resp)
        with patch("duh.tools.http_tool.httpx.AsyncClient", return_value=client):
            result = await self.tool.call(
                {"method": "GET", "url": "https://api.example.com/nope"}, ctx()
            )
        assert result.is_error is False
        assert "HTTP 404" in result.output
        assert result.metadata["status_code"] == 404


# ===========================================================================
# Helpers (unit tests)
# ===========================================================================


class TestHelpers:

    def test_is_json_content_application_json(self):
        assert _is_json_content("application/json") is True

    def test_is_json_content_with_charset(self):
        assert _is_json_content("application/json; charset=utf-8") is True

    def test_is_json_content_subtype(self):
        assert _is_json_content("application/vnd.api+json") is True

    def test_is_json_content_plain(self):
        assert _is_json_content("text/plain") is False

    def test_format_body_pretty_prints_json(self):
        body, truncated = _format_body('{"a":1}', "application/json")
        assert '"a": 1' in body
        assert truncated is False

    def test_format_body_truncates(self):
        body, truncated = _format_body("x" * 20_000, "text/plain")
        assert len(body) == 10_000
        assert truncated is True

    def test_pick_headers_filters(self):
        headers = httpx.Headers({
            "content-type": "application/json",
            "x-request-id": "abc",
            "x-custom": "skip",
        })
        picked = _pick_headers(headers)
        assert "content-type" in picked
        assert "x-request-id" in picked
        assert "x-custom" not in picked
