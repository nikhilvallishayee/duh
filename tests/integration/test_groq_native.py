"""Integration test for the native Groq adapter (ADR-075).

Gated on the ``GROQ_API_KEY`` environment variable. Confirms that a real
Groq API call round-trips through the native adapter producing the expected
D.U.H. event sequence: at least one ``text_delta``, followed by a ``done``
event with populated usage + rate-limit metadata, and a terminal ``assistant``
message.

Skipped automatically when the key is absent or the ``groq`` SDK isn't
installed.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("groq")

from duh.kernel.messages import Message  # noqa: E402
from duh.kernel.untrusted import UntrustedStr  # noqa: E402


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set; skipping live Groq API test",
)
async def test_groq_native_roundtrip_says_hi() -> None:
    from duh.adapters.groq import GroqProvider

    provider = GroqProvider(model="llama-3.3-70b-versatile")
    events = []
    async for event in provider.stream(
        messages=[Message(role="user", content="Say hi in 3 words. No punctuation.")],
        max_tokens=32,
    ):
        events.append(event)

    types = [e["type"] for e in events]
    # Must have at least one text_delta and a done event, ending with assistant.
    assert any(t == "text_delta" for t in types), f"no text_delta in {types}"
    assert "done" in types, f"no done event in {types}"
    assert types[-1] == "assistant", f"expected assistant last, got {types[-1]}"

    # Tainted output enforcement.
    td = next(e for e in events if e["type"] == "text_delta")
    assert isinstance(td["text"], UntrustedStr)

    # Usage populated.
    done = next(e for e in events if e["type"] == "done")
    assert done["usage"].get("input_tokens", 0) > 0
    assert done["usage"].get("output_tokens", 0) > 0

    # At least one rate-limit header came through.
    assert done["rate_limit"], "expected rate-limit headers in done event"
