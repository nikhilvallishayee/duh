"""Live end-to-end WebSearch test.

Makes a single real DuckDuckGo call (no API key required) to confirm the
zero-config default path is wired up correctly from input → HTTP → parse →
ToolResult.

Marked ``@pytest.mark.slow`` so it is skipped by default (``pytest -m "not
slow"``). Run explicitly with::

    pytest tests/integration/test_web_search_live.py -m slow -v
"""

from __future__ import annotations

import os

import pytest

from duh.kernel.tool import ToolContext
from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.tools.web_search import WebSearchTool


@pytest.mark.slow
@pytest.mark.integration
async def test_live_duckduckgo_end_to_end(monkeypatch):
    # Drop any locally-set provider keys so we exercise the DDG default.
    for key in ("SERPER_API_KEY", "TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    # Skip in offline CI — any network problem manifests as a ToolResult with
    # is_error=True, which we treat as "infra unavailable" rather than a test
    # failure.
    if os.environ.get("DUH_OFFLINE_TESTS") == "1":
        pytest.skip("DUH_OFFLINE_TESTS=1 set; skipping live network test")

    tool = WebSearchTool()
    # A topic with a stable IA abstract, so IA normally succeeds; if not we
    # still get HTML scrape as a fallback.
    result = await tool.call(
        {"query": "Python programming language"},
        ToolContext(cwd="."),
    )

    if result.is_error:
        pytest.skip(f"Network unavailable for live test: {result.output}")

    assert 'Web search results for query: "Python programming language"' in result.output
    assert isinstance(result.output, UntrustedStr)
    assert result.output.source == TaintSource.NETWORK
    assert result.metadata["provider"] in {
        "duckduckgo_instant",
        "duckduckgo_html",
    }
