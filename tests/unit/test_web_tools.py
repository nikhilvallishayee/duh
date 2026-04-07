"""Tests for WebFetchTool and WebSearchTool."""

import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.web_fetch import WebFetchTool, _strip_html
from duh.tools.web_search import WebSearchTool, _STUB_MESSAGE


def ctx() -> ToolContext:
    return ToolContext(cwd=".")


# ===========================================================================
# WebFetchTool — Protocol conformance
# ===========================================================================

class TestWebFetchProtocol:

    def test_satisfies_tool_protocol(self):
        tool = WebFetchTool()
        assert isinstance(tool, Tool)

    def test_name(self):
        assert WebFetchTool().name == "WebFetch"

    def test_description_non_empty(self):
        assert WebFetchTool().description

    def test_input_schema_structure(self):
        schema = WebFetchTool().input_schema
        assert schema["type"] == "object"
        assert "url" in schema["properties"]
        assert "url" in schema["required"]

    def test_is_read_only(self):
        assert WebFetchTool().is_read_only is True

    def test_is_not_destructive(self):
        assert WebFetchTool().is_destructive is False

    async def test_check_permissions(self):
        result = await WebFetchTool().check_permissions({}, ctx())
        assert result["allowed"] is True


# ===========================================================================
# WebFetchTool — URL validation
# ===========================================================================

class TestWebFetchValidation:
    tool = WebFetchTool()

    async def test_empty_url_is_error(self):
        result = await self.tool.call({"url": ""}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    async def test_missing_url_is_error(self):
        result = await self.tool.call({}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    async def test_no_scheme_is_error(self):
        result = await self.tool.call({"url": "example.com"}, ctx())
        assert result.is_error is True
        assert "http" in result.output.lower()

    async def test_ftp_scheme_is_error(self):
        result = await self.tool.call({"url": "ftp://example.com/file"}, ctx())
        assert result.is_error is True
        assert "http" in result.output.lower()


# ===========================================================================
# WebFetchTool — successful fetch (mocked)
# ===========================================================================

def _mock_response(
    text: str = "Hello, world!",
    status_code: int = 200,
    content_type: str = "text/plain",
    url: str = "https://example.com",
):
    """Create a mock httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        headers={"content-type": content_type},
        text=text,
        request=httpx.Request("GET", url),
    )
    return resp


class TestWebFetchSuccess:
    tool = WebFetchTool()

    async def test_plain_text_response(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_response("Hello, world!"))

        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await self.tool.call({"url": "https://example.com"}, ctx())

        assert result.is_error is False
        assert "Hello, world!" in result.output
        assert result.metadata["status_code"] == 200

    async def test_html_is_stripped(self):
        html = "<html><body><p>Hello <b>World</b></p></body></html>"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            return_value=_mock_response(html, content_type="text/html")
        )

        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await self.tool.call({"url": "https://example.com"}, ctx())

        assert result.is_error is False
        assert "<p>" not in result.output
        assert "<b>" not in result.output
        assert "Hello" in result.output
        assert "World" in result.output

    async def test_prompt_hint_included(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_response("Some content here."))

        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await self.tool.call(
                {"url": "https://example.com", "prompt": "find the price"},
                ctx(),
            )

        assert result.is_error is False
        assert "[Extraction hint: find the price]" in result.output

    async def test_metadata_fields(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_response("abc"))

        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await self.tool.call({"url": "https://example.com"}, ctx())

        assert "url" in result.metadata
        assert "status_code" in result.metadata
        assert "content_type" in result.metadata
        assert "truncated" in result.metadata
        assert "length" in result.metadata


# ===========================================================================
# WebFetchTool — content truncation
# ===========================================================================

class TestWebFetchTruncation:
    tool = WebFetchTool()

    async def test_large_content_is_truncated(self):
        large_text = "x" * 200_000  # 200KB > 100KB limit
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_response(large_text))

        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await self.tool.call({"url": "https://example.com"}, ctx())

        assert result.is_error is False
        assert result.metadata["truncated"] is True
        assert "truncated" in result.output.lower()
        assert result.metadata["length"] == 100_000

    async def test_small_content_not_truncated(self):
        small_text = "short"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_response(small_text))

        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await self.tool.call({"url": "https://example.com"}, ctx())

        assert result.metadata["truncated"] is False
        assert "truncated" not in result.output.lower()


# ===========================================================================
# WebFetchTool — error handling
# ===========================================================================

class TestWebFetchErrors:
    tool = WebFetchTool()

    async def test_timeout_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await self.tool.call({"url": "https://example.com"}, ctx())

        assert result.is_error is True
        assert "timed out" in result.output.lower()

    async def test_connection_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("DNS resolution failed")
        )

        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await self.tool.call(
                {"url": "https://nonexistent.invalid"}, ctx()
            )

        assert result.is_error is True
        assert "connection failed" in result.output.lower()

    async def test_http_404_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        resp_404 = _mock_response("Not Found", status_code=404)
        mock_client.get = AsyncMock(return_value=resp_404)

        # raise_for_status() will raise on 404
        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            with patch.object(resp_404, "raise_for_status", side_effect=httpx.HTTPStatusError(
                "404", request=httpx.Request("GET", "https://example.com/missing"), response=resp_404
            )):
                result = await self.tool.call(
                    {"url": "https://example.com/missing"}, ctx()
                )

        assert result.is_error is True
        assert "404" in result.output

    async def test_http_500_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        resp_500 = _mock_response("Server Error", status_code=500)
        mock_client.get = AsyncMock(return_value=resp_500)

        with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
            with patch.object(resp_500, "raise_for_status", side_effect=httpx.HTTPStatusError(
                "500", request=httpx.Request("GET", "https://example.com"), response=resp_500
            )):
                result = await self.tool.call(
                    {"url": "https://example.com"}, ctx()
                )

        assert result.is_error is True
        assert "500" in result.output


# ===========================================================================
# _strip_html helper
# ===========================================================================

class TestStripHtml:

    def test_basic_tags_removed(self):
        assert "Hello" in _strip_html("<p>Hello</p>")
        assert "<p>" not in _strip_html("<p>Hello</p>")

    def test_script_tags_removed(self):
        html = "<p>Hello</p><script>alert('xss')</script><p>World</p>"
        text = _strip_html(html)
        assert "alert" not in text
        assert "Hello" in text
        assert "World" in text

    def test_style_tags_removed(self):
        html = "<style>body{color:red}</style><p>Content</p>"
        text = _strip_html(html)
        assert "color" not in text
        assert "Content" in text

    def test_entities_decoded(self):
        text = _strip_html("&amp; &lt; &gt; &quot; &#39;")
        assert "&" in text
        assert "<" in text
        assert ">" in text

    def test_whitespace_collapsed(self):
        text = _strip_html("<p>  lots   of   spaces  </p>")
        assert "  " not in text

    def test_empty_input(self):
        assert _strip_html("") == ""


# ===========================================================================
# WebSearchTool — Protocol conformance
# ===========================================================================

class TestWebSearchProtocol:

    def test_satisfies_tool_protocol(self):
        tool = WebSearchTool()
        assert isinstance(tool, Tool)

    def test_name(self):
        assert WebSearchTool().name == "WebSearch"

    def test_description_non_empty(self):
        assert WebSearchTool().description

    def test_input_schema_structure(self):
        schema = WebSearchTool().input_schema
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "query" in schema["required"]

    def test_is_read_only(self):
        assert WebSearchTool().is_read_only is True

    def test_is_not_destructive(self):
        assert WebSearchTool().is_destructive is False

    async def test_check_permissions(self):
        result = await WebSearchTool().check_permissions({}, ctx())
        assert result["allowed"] is True


# ===========================================================================
# WebSearchTool — stub behavior
# ===========================================================================

class TestWebSearchStub:
    tool = WebSearchTool()

    async def test_empty_query_is_error(self):
        result = await self.tool.call({"query": ""}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    async def test_missing_query_is_error(self):
        result = await self.tool.call({}, ctx())
        assert result.is_error is True

    async def test_returns_stub_without_api_keys(self):
        """Without SERPER or TAVILY keys, returns a helpful config message."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure keys are absent
            env = os.environ.copy()
            env.pop("SERPER_API_KEY", None)
            env.pop("TAVILY_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                result = await self.tool.call({"query": "test search"}, ctx())

        assert result.is_error is False
        assert "requires configuration" in result.output.lower()
        assert "SERPER_API_KEY" in result.output or "TAVILY_API_KEY" in result.output

    async def test_stub_message_matches_constant(self):
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("SERPER_API_KEY", None)
            env.pop("TAVILY_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                result = await self.tool.call({"query": "hello"}, ctx())
        assert result.output == _STUB_MESSAGE


# ===========================================================================
# WebSearchTool — Serper integration (mocked)
# ===========================================================================

class TestWebSearchSerper:
    tool = WebSearchTool()

    async def test_serper_returns_results(self):
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "organic": [
                {"title": "Result 1", "link": "https://r1.com", "snippet": "Snippet 1"},
                {"title": "Result 2", "link": "https://r2.com", "snippet": "Snippet 2"},
            ]
        }
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.dict(os.environ, {"SERPER_API_KEY": "test-key"}, clear=False):
            with patch("duh.tools.web_search.httpx.AsyncClient", return_value=mock_client):
                result = await self.tool.call({"query": "test"}, ctx())

        assert result.is_error is False
        assert "Result 1" in result.output
        assert "Result 2" in result.output
        assert result.metadata["provider"] == "serper"

    async def test_serper_api_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPError("API error"))

        with patch.dict(os.environ, {"SERPER_API_KEY": "test-key"}, clear=False):
            with patch("duh.tools.web_search.httpx.AsyncClient", return_value=mock_client):
                result = await self.tool.call({"query": "test"}, ctx())

        assert result.is_error is True
        assert "serper" in result.output.lower()


# ===========================================================================
# WebSearchTool — Tavily integration (mocked)
# ===========================================================================

class TestWebSearchTavily:
    tool = WebSearchTool()

    async def test_tavily_returns_results(self):
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "results": [
                {"title": "Tavily 1", "url": "https://t1.com", "content": "Content 1"},
            ]
        }
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        # Only set TAVILY, not SERPER (Serper takes priority)
        env_patch = {"TAVILY_API_KEY": "test-key"}
        with patch.dict(os.environ, env_patch, clear=False):
            # Remove SERPER if present
            os.environ.pop("SERPER_API_KEY", None)
            with patch("duh.tools.web_search.httpx.AsyncClient", return_value=mock_client):
                result = await self.tool.call({"query": "test"}, ctx())

        assert result.is_error is False
        assert "Tavily 1" in result.output
        assert result.metadata["provider"] == "tavily"

    async def test_tavily_api_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPError("API error"))

        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}, clear=False):
            os.environ.pop("SERPER_API_KEY", None)
            with patch("duh.tools.web_search.httpx.AsyncClient", return_value=mock_client):
                result = await self.tool.call({"query": "test"}, ctx())

        assert result.is_error is True
        assert "tavily" in result.output.lower()
