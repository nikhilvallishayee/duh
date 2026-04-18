"""WebSearchTool — zero-config web search with 5-tier provider fallback.

Fallback chain (first match wins):

  1. SERPER_API_KEY       → Serper (Google Search API, POST JSON)
  2. TAVILY_API_KEY       → Tavily (POST JSON)
  3. BRAVE_SEARCH_API_KEY → Brave Search (GET, X-Subscription-Token)
  4. (no key)             → DuckDuckGo Instant Answer API (GET JSON)
  5. (no key)             → DuckDuckGo HTML scrape (GET HTML)

The DDG Instant Answer API frequently returns an empty payload for non-
encyclopedic queries ("latest news", "today in X"); in that case we
transparently fall through to the HTML scrape, decoding DDG's ``/l/?uddg=``
redirect wrapper so downstream consumers see real target URLs.

All network output is wrapped in ``UntrustedStr(..., TaintSource.NETWORK)``
so tainted search results propagate through the rest of the agent and the
trifecta policy can refuse dangerous tool calls that chain through them.

Environment variables
---------------------
SERPER_API_KEY           Prefer Serper when set.
TAVILY_API_KEY           Prefer Tavily when the above is unset.
BRAVE_SEARCH_API_KEY     Prefer Brave when the above two are unset.
DUH_WEBSEARCH_TIMEOUT    Per-request timeout in seconds (default 5).
"""

from __future__ import annotations

import html as _html
import json
import os
import re
from typing import Any
from urllib.parse import unquote

import httpx

from duh.kernel.tool import ToolContext, ToolResult
from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.security.trifecta import Capability


# ---------------------------------------------------------------------------
# Legacy compatibility symbol
# ---------------------------------------------------------------------------
#
# Older tests (``tests/unit/test_web_tools.py``) still import ``_STUB_MESSAGE``.
# The zero-config DDG fallback means a stub is no longer produced, but we keep
# the symbol exported so existing imports continue to resolve — the message is
# only shown if, somehow, *every* provider tier is unreachable.

_STUB_MESSAGE = (
    "WebSearch could not reach any search provider. "
    "Set SERPER_API_KEY, TAVILY_API_KEY, or BRAVE_SEARCH_API_KEY, "
    "or check your network connection to DuckDuckGo."
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 5.0  # seconds — search endpoints should be fast
_MAX_RESULTS = 5
_DDG_IA_ENDPOINT = "https://api.duckduckgo.com/"
_DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_SERPER_ENDPOINT = "https://google.serper.dev/search"
_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# A realistic desktop Chrome UA — DDG's HTML endpoint blocks obvious bot UAs.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _get_timeout() -> float:
    """Return the configured per-request timeout, honouring DUH_WEBSEARCH_TIMEOUT."""
    raw = os.environ.get("DUH_WEBSEARCH_TIMEOUT", "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT
    return value if value > 0 else _DEFAULT_TIMEOUT


def _taint(text: str) -> UntrustedStr:
    """Wrap *text* as a NETWORK-sourced UntrustedStr."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.NETWORK)


# ---------------------------------------------------------------------------
# Helpers — exported for tests
# ---------------------------------------------------------------------------

def _format_results(query: str, results: list[dict[str, str]]) -> str:
    """Render a list of hits as the canonical WebSearch output.

    The format is designed to be both human-legible and easy to parse:

        Web search results for query: "<query>"

        Links: [{"title": ..., "url": ..., "snippet": ...}, ...]

        1. <title>
           <url>
           <snippet>

    When ``results`` is empty, a single ``"No links found."`` line follows the
    header so the model knows the provider responded but had nothing to say.
    """
    header = f'Web search results for query: "{query}"'
    if not results:
        return f"{header}\n\nNo links found."

    links_json = json.dumps(results, ensure_ascii=False)
    lines = [header, "", f"Links: {links_json}", ""]
    for idx, hit in enumerate(results, 1):
        title = hit.get("title", "").strip() or "(untitled)"
        url = hit.get("url", "").strip()
        snippet = hit.get("snippet", "").strip()
        lines.append(f"{idx}. {title}")
        if url:
            lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_DDG_REDIRECT_RE = re.compile(
    r"^(?:https?:)?//duckduckgo\.com/l/\?.*?uddg=([^&]+)",
    re.IGNORECASE,
)


def _unwrap_ddg_redirect(href: str) -> str:
    """DDG wraps outbound links as ``//duckduckgo.com/l/?uddg=<url-encoded>``.

    Return the decoded target URL when we recognise that pattern, otherwise
    ``href`` unchanged (after normalising protocol-relative URLs).
    """
    if not href:
        return href
    match = _DDG_REDIRECT_RE.match(href)
    if match:
        return unquote(match.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


_RESULT_BLOCK_RE = re.compile(
    # Match a result block by anchoring on the opening <div class="... result ...">
    # and delegating to the inner-content scanners — we don't need to find the
    # exact closing </div>, which is hard to do without a real HTML parser when
    # result blocks may themselves contain nested <div>s. Instead we capture up
    # to the next result-block opener or end-of-string and let the link/snippet
    # regexes pull structured fields out of each slice.
    r'<div\s+class="[^"]*\bresult\b[^"]*"[^>]*>'
    r'(?P<body>.*?)'
    r'(?=<div\s+class="[^"]*\bresult\b|</body|\Z)',
    re.IGNORECASE | re.DOTALL,
)
_RESULT_LINK_RE = re.compile(
    r'<a[^>]*class="[^"]*\bresult__a\b[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_RESULT_SNIPPET_RE = re.compile(
    r'<a[^>]*class="[^"]*\bresult__snippet\b[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_inline_html(fragment: str) -> str:
    """Remove HTML tags and decode entities inside a single text fragment."""
    text = _TAG_RE.sub("", fragment)
    text = _html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _parse_ddg_html(html: str, limit: int = _MAX_RESULTS) -> list[dict[str, str]]:
    """Parse a DuckDuckGo HTML search page into structured hits.

    We deliberately use regexes rather than BeautifulSoup:

    * DDG's markup is hand-tuned for machine consumption and stable across
      months; a dedicated parser is overkill.
    * Avoiding a soft dependency on BeautifulSoup keeps the tool dependency
      footprint the same as WebFetch.

    Each returned dict has ``title``, ``url``, ``snippet`` keys. URLs wrapped
    in DDG's ``/l/?uddg=`` redirect are decoded. Protocol-relative URLs are
    promoted to ``https://``.
    """
    if not html:
        return []

    hits: list[dict[str, str]] = []
    for block_match in _RESULT_BLOCK_RE.finditer(html):
        block = block_match.group("body")

        link_match = _RESULT_LINK_RE.search(block)
        if not link_match:
            continue
        raw_href = link_match.group(1).strip()
        title = _strip_inline_html(link_match.group(2))
        url = _unwrap_ddg_redirect(raw_href)

        snippet_match = _RESULT_SNIPPET_RE.search(block)
        snippet = _strip_inline_html(snippet_match.group(1)) if snippet_match else ""

        if not title or not url:
            continue

        hits.append({"title": title, "url": url, "snippet": snippet})
        if len(hits) >= limit:
            break

    return hits


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------

class WebSearchTool:
    """Zero-config web search with provider fallback.

    Priority (highest first): Serper → Tavily → Brave → DuckDuckGo IA → DDG HTML.
    With no API keys configured, the DuckDuckGo tiers keep the tool usable
    out of the box — at the cost of a scrapy HTML fallback — while any of the
    paid providers produces richer structured results when their key is set.
    """

    name = "WebSearch"
    capabilities = Capability.NETWORK_EGRESS
    description = (
        "Search the web for a query. Uses Serper/Tavily/Brave when their API "
        "keys are set, otherwise falls back to DuckDuckGo (no key required)."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
        },
        "required": ["query"],
    }

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    # -- dispatch ---------------------------------------------------------

    async def call(
        self, input: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        query = input.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(output="query is required", is_error=True)
        query = query.strip()

        serper = os.environ.get("SERPER_API_KEY", "").strip()
        if serper:
            return await self._search_serper(query, serper)

        tavily = os.environ.get("TAVILY_API_KEY", "").strip()
        if tavily:
            return await self._search_tavily(query, tavily)

        brave = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        if brave:
            return await self._search_brave(query, brave)

        # No paid key → zero-config DuckDuckGo. Instant Answer first; if it
        # returns an empty payload, fall through to the HTML scrape.
        ia_result = await self._search_ddg_instant(query)
        if ia_result.is_error:
            return ia_result
        if ia_result.metadata.get("empty"):
            return await self._search_ddg_html(query)
        return ia_result

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}

    # -- providers --------------------------------------------------------

    async def _search_serper(self, query: str, api_key: str) -> ToolResult:
        timeout = _get_timeout()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await client.post(
                    _SERPER_ENDPOINT,
                    headers={
                        "X-API-KEY": api_key,
                        "Content-Type": "application/json",
                    },
                    json={"q": query, "num": _MAX_RESULTS},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            return ToolResult(
                output=_taint(f"Serper request timeout after {timeout}s"),
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                output=_taint(f"Serper API error: {exc}"),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001 - last-ditch guard
            return ToolResult(
                output=_taint(f"Serper unexpected error: {exc}"),
                is_error=True,
            )

        hits: list[dict[str, str]] = []
        for item in (data.get("organic") or [])[:_MAX_RESULTS]:
            hits.append({
                "title": str(item.get("title", "")),
                "url": str(item.get("link", "")),
                "snippet": str(item.get("snippet", "")),
            })

        return ToolResult(
            output=_taint(_format_results(query, hits)),
            metadata={"provider": "serper", "result_count": len(hits)},
        )

    async def _search_tavily(self, query: str, api_key: str) -> ToolResult:
        timeout = _get_timeout()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await client.post(
                    _TAVILY_ENDPOINT,
                    headers={"Content-Type": "application/json"},
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": _MAX_RESULTS,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            return ToolResult(
                output=_taint(f"Tavily request timeout after {timeout}s"),
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                output=_taint(f"Tavily API error: {exc}"),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                output=_taint(f"Tavily unexpected error: {exc}"),
                is_error=True,
            )

        hits: list[dict[str, str]] = []
        for item in (data.get("results") or [])[:_MAX_RESULTS]:
            hits.append({
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("content", "")),
            })

        return ToolResult(
            output=_taint(_format_results(query, hits)),
            metadata={"provider": "tavily", "result_count": len(hits)},
        )

    async def _search_brave(self, query: str, api_key: str) -> ToolResult:
        timeout = _get_timeout()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await client.get(
                    _BRAVE_ENDPOINT,
                    params={"q": query, "count": _MAX_RESULTS},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": api_key,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            return ToolResult(
                output=_taint(f"Brave request timeout after {timeout}s"),
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                output=_taint(f"Brave API error: {exc}"),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                output=_taint(f"Brave unexpected error: {exc}"),
                is_error=True,
            )

        hits: list[dict[str, str]] = []
        web_results = ((data.get("web") or {}).get("results") or [])
        for item in web_results[:_MAX_RESULTS]:
            hits.append({
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("description", "")),
            })

        return ToolResult(
            output=_taint(_format_results(query, hits)),
            metadata={"provider": "brave", "result_count": len(hits)},
        )

    async def _search_ddg_instant(self, query: str) -> ToolResult:
        """Call the DuckDuckGo Instant Answer JSON API.

        Returns a result with ``metadata["empty"] = True`` (not an error!) when
        the API responds but has nothing to say about *query*, so callers know
        to fall through to the HTML scrape.
        """
        timeout = _get_timeout()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await client.get(
                    _DDG_IA_ENDPOINT,
                    params={
                        "q": query,
                        "format": "json",
                        "no_html": "1",
                        "skip_disambig": "1",
                    },
                    headers={
                        "User-Agent": _BROWSER_UA,
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            return ToolResult(
                output=_taint(
                    f"DuckDuckGo Instant Answer request timed out after {timeout}s"
                ),
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                output=_taint(f"DuckDuckGo Instant Answer error: {exc}"),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                output=_taint(
                    f"DuckDuckGo Instant Answer unexpected error: {exc}"
                ),
                is_error=True,
            )

        hits: list[dict[str, str]] = []

        abstract = (data.get("AbstractText") or "").strip()
        abstract_url = (data.get("AbstractURL") or "").strip()
        source = (data.get("AbstractSource") or "").strip()
        if abstract:
            hits.append({
                "title": source or "DuckDuckGo Abstract",
                "url": abstract_url,
                "snippet": abstract,
            })

        for topic in (data.get("RelatedTopics") or [])[: _MAX_RESULTS * 2]:
            # RelatedTopics entries are either leaf items with FirstURL/Text
            # or grouped {"Name": ..., "Topics": [...]}; we only take leaves.
            if not isinstance(topic, dict):
                continue
            url = (topic.get("FirstURL") or "").strip()
            text = (topic.get("Text") or "").strip()
            if not url or not text:
                continue
            # Text is often "<title> - <snippet>"; split on first " - " when present.
            if " - " in text:
                title, snippet = text.split(" - ", 1)
            else:
                title, snippet = text, ""
            hits.append({
                "title": title.strip(),
                "url": url,
                "snippet": snippet.strip(),
            })
            if len(hits) >= _MAX_RESULTS:
                break

        if not hits:
            return ToolResult(
                output=_taint(_format_results(query, [])),
                metadata={"provider": "duckduckgo_instant", "empty": True},
            )

        return ToolResult(
            output=_taint(_format_results(query, hits)),
            metadata={"provider": "duckduckgo_instant", "result_count": len(hits)},
        )

    async def _search_ddg_html(self, query: str) -> ToolResult:
        """Scrape DDG's HTML search page. Fallback when IA is empty.

        DDG actively refuses obvious bot user agents; we impersonate a real
        desktop Chrome so the request goes through.
        """
        timeout = _get_timeout()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    _DDG_HTML_ENDPOINT,
                    params={"q": query},
                    headers={
                        "User-Agent": _BROWSER_UA,
                        "Accept": (
                            "text/html,application/xhtml+xml,"
                            "application/xml;q=0.9,*/*;q=0.8"
                        ),
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                response.raise_for_status()
                html_text = response.text
        except httpx.TimeoutException:
            return ToolResult(
                output=_taint(
                    f"DuckDuckGo HTML request timed out after {timeout}s"
                ),
                is_error=True,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                output=_taint(f"DuckDuckGo HTML error: {exc}"),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                output=_taint(f"DuckDuckGo HTML unexpected error: {exc}"),
                is_error=True,
            )

        hits = _parse_ddg_html(html_text, limit=_MAX_RESULTS)
        return ToolResult(
            output=_taint(_format_results(query, hits)),
            metadata={"provider": "duckduckgo_html", "result_count": len(hits)},
        )
