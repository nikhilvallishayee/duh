"""WebSearchTool — web search (requires API key configuration)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability

_STUB_MESSAGE = (
    "WebSearch requires configuration. "
    "Set SERPER_API_KEY or TAVILY_API_KEY in your environment, "
    "or set up a search MCP server in .duh/settings.json."
)


class WebSearchTool:
    """Search the web for a query.

    Currently a stub unless SERPER_API_KEY or TAVILY_API_KEY is set.
    """

    name = "WebSearch"
    capabilities = Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS
    description = "Search the web. Requires SERPER_API_KEY or TAVILY_API_KEY."
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

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        query = input.get("query", "").strip()

        if not query:
            return ToolResult(output="query is required", is_error=True)

        serper_key = os.environ.get("SERPER_API_KEY", "")
        tavily_key = os.environ.get("TAVILY_API_KEY", "")

        if serper_key:
            return await self._search_serper(query, serper_key)
        elif tavily_key:
            return await self._search_tavily(query, tavily_key)
        else:
            return ToolResult(output=_STUB_MESSAGE)

    async def _search_serper(self, query: str, api_key: str) -> ToolResult:
        """Search via Serper (Google Search API)."""

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
                response = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                    json={"q": query, "num": 5},
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return ToolResult(output=f"Serper API error: {exc}", is_error=True)

        results: list[str] = []
        for item in data.get("organic", [])[:5]:
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            results.append(f"**{title}**\n{link}\n{snippet}")

        if not results:
            return ToolResult(output=f"No results found for: {query}")

        return ToolResult(
            output="\n\n".join(results),
            metadata={"provider": "serper", "result_count": len(results)},
        )

    async def _search_tavily(self, query: str, api_key: str) -> ToolResult:
        """Search via Tavily Search API."""

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": api_key, "query": query, "max_results": 5},
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return ToolResult(output=f"Tavily API error: {exc}", is_error=True)

        results: list[str] = []
        for item in data.get("results", [])[:5]:
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            results.append(f"**{title}**\n{url}\n{content}")

        if not results:
            return ToolResult(output=f"No results found for: {query}")

        return ToolResult(
            output="\n\n".join(results),
            metadata={"provider": "tavily", "result_count": len(results)},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
