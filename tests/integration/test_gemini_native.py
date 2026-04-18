"""Integration test for the native GeminiProvider (ADR-075).

Gated on GEMINI_API_KEY / GOOGLE_API_KEY. Skipped otherwise. Confirms that a
real round-trip through google-genai streams back tokens and a `done` event
with usage metadata.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("google.genai")

from duh.kernel.messages import Message  # noqa: E402

pytestmark = [pytest.mark.integration]


def _has_key() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


@pytest.mark.skipif(not _has_key(), reason="GEMINI_API_KEY / GOOGLE_API_KEY not set")
@pytest.mark.asyncio
async def test_gemini_hello_world_stream():
    from duh.adapters.gemini import GeminiProvider

    provider = GeminiProvider(model="gemini-2.5-flash")
    events: list[dict] = []
    async for ev in provider.stream(
        messages=[Message(role="user", content="say hi in 3 words")],
        max_tokens=32,
    ):
        events.append(ev)
        if len(events) > 200:  # safety guard
            break

    # Streaming produced text
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert text_events, f"no text_delta events; got: {[e['type'] for e in events]}"

    # done event with usage metadata
    done = [e for e in events if e["type"] == "done"]
    assert done, "no done event"
    usage = done[0].get("usage", {})
    assert usage.get("input_tokens", 0) > 0
    assert usage.get("output_tokens", 0) > 0

    # Final assistant message
    assistant = [e for e in events if e["type"] == "assistant"]
    assert assistant
    assert assistant[0]["message"].text
