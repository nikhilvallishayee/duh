"""Tests for the zero-config WebSearchTool fallback chain.

Covers:
  - No keys set → DuckDuckGo Instant Answer (mocked)
  - IA empty → fall through to DDG HTML scrape (mocked)
  - SERPER_API_KEY set → uses Serper (mocked)
  - BRAVE_SEARCH_API_KEY set → uses Brave (mocked)
  - Output format matches the "Web search results for query:" shape
  - Output tainted as TaintSource.NETWORK
  - SSRF guard is NOT triggered on legitimate public search URLs
  - Timeout handling
  - HTML scrape parses 5 results from a realistic fixture
  - Priority: Serper wins over Brave, Brave wins over DDG
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from duh.kernel.tool import ToolContext
from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.tools.web_search import (
    WebSearchTool,
    _format_results,
    _parse_ddg_html,
)


def _ctx() -> ToolContext:
    return ToolContext(cwd=".")


def _clear_search_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no search-provider API keys leak into a test's environment."""
    for key in ("SERPER_API_KEY", "TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _mock_json_response(payload: dict, status_code: int = 200):
    """Return an httpx.Response-like mock that yields *payload* from .json()."""
    resp = AsyncMock()
    resp.status_code = status_code
    resp.json = lambda: payload
    resp.raise_for_status = lambda: None
    return resp


def _mock_text_response(text: str, status_code: int = 200):
    resp = AsyncMock()
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status = lambda: None
    return resp


def _mock_client(method_name: str, response_or_exc) -> AsyncMock:
    """Build an async-context-manager httpx client mock."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if isinstance(response_or_exc, Exception):
        setattr(client, method_name, AsyncMock(side_effect=response_or_exc))
    else:
        setattr(client, method_name, AsyncMock(return_value=response_or_exc))
    return client


# ===========================================================================
# Fallback chain: no keys → DDG Instant Answer
# ===========================================================================

class TestDefaultToDuckDuckGoInstant:
    tool = WebSearchTool()

    async def test_no_keys_falls_back_to_ia(self, monkeypatch):
        _clear_search_env(monkeypatch)

        ia_payload = {
            "AbstractText": "Python is a high-level programming language.",
            "AbstractURL": "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "AbstractSource": "Wikipedia",
            "RelatedTopics": [
                {
                    "FirstURL": "https://python.org",
                    "Text": "Python.org — the official site",
                },
                {
                    "FirstURL": "https://docs.python.org",
                    "Text": "Python docs",
                },
            ],
        }
        client = _mock_client("get", _mock_json_response(ia_payload))

        with patch("duh.tools.web_search.httpx.AsyncClient", return_value=client):
            result = await self.tool.call({"query": "python"}, _ctx())

        assert result.is_error is False
        assert result.metadata["provider"] == "duckduckgo_instant"
        assert "Python is a high-level programming language." in result.output
        assert "wikipedia.org" in result.output
        # Output format marker
        assert 'Web search results for query: "python"' in result.output

    async def test_ia_empty_falls_through_to_html(self, monkeypatch):
        """When IA returns empty, the tool must try HTML scraping next."""
        _clear_search_env(monkeypatch)

        # First call (IA) returns empty; second call (HTML) returns a real page.
        ia_empty = {"AbstractText": "", "AbstractURL": "", "RelatedTopics": []}
        html_page = (
            '<html><body>'
            '<div class="result">'
            '<a class="result__a" href="https://example.com/first">First Title</a>'
            '<a class="result__snippet" href="#">First snippet text</a>'
            '</div>'
            '</body></html>'
        )

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=[
            _mock_json_response(ia_empty),
            _mock_text_response(html_page),
        ])

        with patch("duh.tools.web_search.httpx.AsyncClient", return_value=client):
            result = await self.tool.call({"query": "current event"}, _ctx())

        assert result.is_error is False
        assert result.metadata["provider"] == "duckduckgo_html"
        assert "First Title" in result.output
        assert "example.com/first" in result.output


# ===========================================================================
# Priority ordering: Serper > Tavily > Brave > DDG
# ===========================================================================

class TestProviderPriority:
    tool = WebSearchTool()

    async def test_serper_wins_when_key_set(self, monkeypatch):
        _clear_search_env(monkeypatch)
        monkeypatch.setenv("SERPER_API_KEY", "s-key")
        # Brave also set — Serper should still win.
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b-key")

        payload = {"organic": [{"title": "S", "link": "https://s.example", "snippet": "SS"}]}
        client = _mock_client("post", _mock_json_response(payload))

        with patch("duh.tools.web_search.httpx.AsyncClient", return_value=client):
            result = await self.tool.call({"query": "x"}, _ctx())

        assert result.metadata["provider"] == "serper"
        assert "S" in result.output
        # Ensure we did a POST (serper) not a GET (brave/ddg).
        assert client.post.call_count == 1

    async def test_brave_used_when_only_brave_key(self, monkeypatch):
        _clear_search_env(monkeypatch)
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b-key")

        payload = {
            "web": {
                "results": [
                    {
                        "title": "Brave Hit",
                        "url": "https://brave.example/r",
                        "description": "Brave description",
                    }
                ]
            }
        }
        client = _mock_client("get", _mock_json_response(payload))

        with patch("duh.tools.web_search.httpx.AsyncClient", return_value=client):
            result = await self.tool.call({"query": "x"}, _ctx())

        assert result.is_error is False
        assert result.metadata["provider"] == "brave"
        assert "Brave Hit" in result.output
        assert "brave.example/r" in result.output
        # Verify auth header was sent with the subscription token.
        args, kwargs = client.get.call_args
        assert kwargs["headers"]["X-Subscription-Token"] == "b-key"


# ===========================================================================
# Output format
# ===========================================================================

class TestOutputFormat:

    def test_format_results_header(self):
        out = _format_results("kittens", [
            {"title": "T", "url": "https://u", "snippet": "S"},
        ])
        assert 'Web search results for query: "kittens"' in out
        assert "Links:" in out

    def test_format_results_json_links(self):
        hits = [{"title": "A", "url": "https://a", "snippet": "sa"}]
        out = _format_results("q", hits)
        # Links: line should contain valid JSON with the hit fields.
        assert '"title": "A"' in out
        assert '"url": "https://a"' in out
        assert '"snippet": "sa"' in out

    def test_format_results_empty(self):
        out = _format_results("q", [])
        assert "No links found." in out


# ===========================================================================
# Taint propagation
# ===========================================================================

class TestTaint:
    tool = WebSearchTool()

    async def test_ddg_output_tainted_network(self, monkeypatch):
        _clear_search_env(monkeypatch)
        payload = {
            "AbstractText": "x",
            "AbstractURL": "https://x.example",
            "AbstractSource": "X",
            "RelatedTopics": [],
        }
        client = _mock_client("get", _mock_json_response(payload))
        with patch("duh.tools.web_search.httpx.AsyncClient", return_value=client):
            result = await self.tool.call({"query": "q"}, _ctx())

        assert isinstance(result.output, UntrustedStr)
        assert result.output.source == TaintSource.NETWORK

    async def test_serper_output_tainted_network(self, monkeypatch):
        _clear_search_env(monkeypatch)
        monkeypatch.setenv("SERPER_API_KEY", "k")
        payload = {"organic": [{"title": "t", "link": "https://u", "snippet": "s"}]}
        client = _mock_client("post", _mock_json_response(payload))
        with patch("duh.tools.web_search.httpx.AsyncClient", return_value=client):
            result = await self.tool.call({"query": "q"}, _ctx())

        assert isinstance(result.output, UntrustedStr)
        assert result.output.source == TaintSource.NETWORK


# ===========================================================================
# Timeout handling
# ===========================================================================

class TestTimeout:
    tool = WebSearchTool()

    async def test_ddg_instant_timeout_returns_error(self, monkeypatch):
        _clear_search_env(monkeypatch)
        client = _mock_client("get", httpx.TimeoutException("slow"))
        with patch("duh.tools.web_search.httpx.AsyncClient", return_value=client):
            result = await self.tool.call({"query": "q"}, _ctx())

        assert result.is_error is True
        assert "timeout" in result.output.lower() or "timed out" in result.output.lower()

    async def test_serper_timeout_returns_error(self, monkeypatch):
        _clear_search_env(monkeypatch)
        monkeypatch.setenv("SERPER_API_KEY", "k")
        client = _mock_client("post", httpx.TimeoutException("slow"))
        with patch("duh.tools.web_search.httpx.AsyncClient", return_value=client):
            result = await self.tool.call({"query": "q"}, _ctx())

        assert result.is_error is True
        assert "serper" in result.output.lower()
        assert "timeout" in result.output.lower()


# ===========================================================================
# SSRF: the tool must be usable against public search endpoints
# ===========================================================================

class TestSsrfCompat:
    tool = WebSearchTool()

    async def test_public_ddg_host_not_blocked(self, monkeypatch):
        """Sanity: the DDG URL is public and must not trigger web_fetch's
        SSRF guard. WebSearchTool does not invoke _validate_url_ssrf itself —
        it uses well-known public endpoints — but this test confirms the tool
        reaches the network layer instead of being short-circuited."""
        _clear_search_env(monkeypatch)

        payload = {
            "AbstractText": "ok",
            "AbstractURL": "https://duckduckgo.com/",
            "AbstractSource": "DDG",
            "RelatedTopics": [],
        }
        client = _mock_client("get", _mock_json_response(payload))
        with patch("duh.tools.web_search.httpx.AsyncClient", return_value=client):
            result = await self.tool.call({"query": "hi"}, _ctx())

        assert result.is_error is False
        # The URL we hit must be the public DDG API:
        call_url = client.get.call_args.args[0]
        assert call_url.startswith("https://api.duckduckgo.com/")


# ===========================================================================
# HTML scrape parser
# ===========================================================================

# Realistic DDG HTML results fixture (stripped-down structure). Using five
# unique result blocks to exercise the 5-limit cap.
_DDG_HTML_FIXTURE = """
<html><body>
<div class="serp__results">

  <div class="result results_links">
    <h2 class="result__title">
      <a class="result__a" href="https://example.com/python-tutorial">Python Tutorial - Example.com</a>
    </h2>
    <a class="result__snippet" href="//example.com">Learn Python in 30 days. A complete hands-on tutorial with examples.</a>
  </div>

  <div class="result results_links">
    <h2 class="result__title">
      <a class="result__a" href="https://docs.python.org/3/">Python Docs &mdash; 3.x</a>
    </h2>
    <a class="result__snippet" href="//docs.python.org">Official <b>Python</b> documentation for the current stable release.</a>
  </div>

  <div class="result results_links">
    <h2 class="result__title">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Frealpython.com%2F">Real Python</a>
    </h2>
    <a class="result__snippet">Tutorials, articles, and books about Python programming.</a>
  </div>

  <div class="result results_links">
    <h2 class="result__title">
      <a class="result__a" href="https://wiki.python.org/moin/">Python Wiki</a>
    </h2>
    <a class="result__snippet">Community wiki for Python developers &amp; learners.</a>
  </div>

  <div class="result results_links">
    <h2 class="result__title">
      <a class="result__a" href="https://pypi.org/">PyPI — The Python Package Index</a>
    </h2>
    <a class="result__snippet">Find, install and publish Python packages.</a>
  </div>

  <div class="result results_links">
    <h2 class="result__title">
      <a class="result__a" href="https://stackoverflow.com/questions/tagged/python">Stack Overflow</a>
    </h2>
    <a class="result__snippet">Q&amp;A for Python developers.</a>
  </div>

</div>
</body></html>
"""


class TestHtmlParser:

    def test_parses_five_results_from_fixture(self):
        hits = _parse_ddg_html(_DDG_HTML_FIXTURE)
        assert len(hits) == 5
        # First hit
        assert hits[0]["title"] == "Python Tutorial - Example.com"
        assert hits[0]["url"].startswith("https://example.com/python-tutorial")
        assert "Learn Python" in hits[0]["snippet"]

    def test_parser_decodes_ddg_redirect(self):
        hits = _parse_ddg_html(_DDG_HTML_FIXTURE)
        # Third hit used the /l/?uddg= redirect form; should unwrap to realpython.com.
        real_python = [h for h in hits if "realpython" in h["url"]]
        assert real_python, "redirect should decode to realpython.com"
        assert real_python[0]["url"].startswith("https://realpython.com")

    def test_parser_strips_inline_html_from_snippets(self):
        hits = _parse_ddg_html(_DDG_HTML_FIXTURE)
        # Second fixture entry has <b>Python</b> inside the snippet.
        docs = [h for h in hits if "docs.python.org" in h["url"]]
        assert docs
        assert "<b>" not in docs[0]["snippet"]
        assert "Python" in docs[0]["snippet"]

    def test_parser_decodes_html_entities(self):
        hits = _parse_ddg_html(_DDG_HTML_FIXTURE)
        wiki = [h for h in hits if "wiki.python.org" in h["url"]]
        assert wiki
        # &amp; should have been decoded to &.
        assert "&" in wiki[0]["snippet"]
        assert "&amp;" not in wiki[0]["snippet"]

    def test_parser_respects_limit(self):
        hits = _parse_ddg_html(_DDG_HTML_FIXTURE, limit=3)
        assert len(hits) == 3

    def test_parser_empty_input_returns_empty(self):
        assert _parse_ddg_html("") == []

    def test_parser_handles_malformed_html(self):
        # Random junk should not raise.
        hits = _parse_ddg_html("<html><body>not a search page</body></html>")
        assert hits == []


# ===========================================================================
# Input validation
# ===========================================================================

class TestInput:
    tool = WebSearchTool()

    async def test_empty_query(self):
        result = await self.tool.call({"query": ""}, _ctx())
        assert result.is_error is True

    async def test_whitespace_only_query(self):
        result = await self.tool.call({"query": "   "}, _ctx())
        assert result.is_error is True
