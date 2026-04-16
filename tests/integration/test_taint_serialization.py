"""Integration tests: taint survives FileStore save -> load round-trip.

Addresses clarifying question #1 from QE Analysis #8 report 06:
    "If a Message contains UntrustedStr content and is persisted to disk
    via FileStore, is the taint tag preserved on reload?"

Background
----------
UntrustedStr is a str subclass (see ``duh.kernel.untrusted``). A plain
``json.dumps(UntrustedStr(...))`` emits a bare JSON string with no tag,
so the ``_source`` attribute is lost on serialize. The issue is not
obvious because the loaded value is still a *valid* str for rendering
purposes; it simply no longer carries the provenance the policy gate
needs to make confirmation decisions.

What should happen
~~~~~~~~~~~~~~~~~~
Taint metadata must round-trip. A Message persisted with tainted
content, when reloaded on the next turn, must still be recognisable as
MODEL_OUTPUT / TOOL_OUTPUT / FILE_CONTENT / MCP_OUTPUT / NETWORK so the
security policy can refuse dangerous tool calls originating from stored
untrusted text.

What used to happen (pre-fix)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``json.dumps`` serialized UntrustedStr as a plain string; on reload
``json.loads`` produced a plain ``str`` with no taint. Any resumed
session had its taint forgotten, which silently weakens the
confirmation gate.

The fix (ADR-054 taint persistence, workstream 7.1)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``file_store._encode_taint`` / ``_decode_taint`` box UntrustedStr values
as ``{"__duh_taint__": "<source>", "value": "<str>"}`` before json.dumps
and rebuild UntrustedStr on load. Plain strings pass through unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from duh.adapters.file_store import FileStore
from duh.kernel.messages import Message, TextBlock
from duh.kernel.untrusted import TaintSource, UntrustedStr


# ---------------------------------------------------------------------------
# Round-trip: simple string content
# ---------------------------------------------------------------------------

class TestTaintSurvivesStringContent:
    async def test_model_output_taint_preserved(self, tmp_path: Path) -> None:
        """An assistant message whose ``content`` is an UntrustedStr tagged
        MODEL_OUTPUT must come back tainted after save/load."""
        store = FileStore(base_dir=tmp_path)
        tainted = UntrustedStr("rm -rf /", TaintSource.MODEL_OUTPUT)
        msg = Message(
            role="assistant",
            content=tainted,
            id="m-1",
            timestamp="2025-01-01T00:00:00+00:00",
        )
        await store.save("sess-taint-1", [msg])

        loaded = await store.load("sess-taint-1")
        assert loaded is not None
        assert len(loaded) == 1
        reloaded_content = loaded[0]["content"]

        assert isinstance(reloaded_content, UntrustedStr), (
            "taint was lost on disk round-trip: got plain str, "
            "policy gate can no longer detect MODEL_OUTPUT provenance"
        )
        assert reloaded_content.source == TaintSource.MODEL_OUTPUT
        assert reloaded_content.is_tainted()
        assert str(reloaded_content) == "rm -rf /"

    async def test_tool_output_taint_preserved(self, tmp_path: Path) -> None:
        """TOOL_OUTPUT is distinct from MODEL_OUTPUT; the source tag must
        round-trip exactly, not collapse to a default."""
        store = FileStore(base_dir=tmp_path)
        tool_out = UntrustedStr("file: /etc/passwd", TaintSource.TOOL_OUTPUT)
        msg = Message(
            role="user",
            content=tool_out,
            id="m-tool",
            timestamp="2025-01-01T00:00:00+00:00",
        )
        await store.save("sess-taint-2", [msg])

        loaded = await store.load("sess-taint-2")
        assert loaded is not None
        reloaded = loaded[0]["content"]
        assert isinstance(reloaded, UntrustedStr)
        assert reloaded.source == TaintSource.TOOL_OUTPUT

    async def test_untainted_string_passes_through(self, tmp_path: Path) -> None:
        """A plain str (no taint) must not be falsely upgraded to UntrustedStr."""
        store = FileStore(base_dir=tmp_path)
        msg = Message(
            role="user", content="plain safe text",
            id="m-plain", timestamp="2025-01-01T00:00:00+00:00",
        )
        await store.save("sess-plain", [msg])

        loaded = await store.load("sess-plain")
        assert loaded is not None
        content = loaded[0]["content"]
        assert isinstance(content, str)
        assert not isinstance(content, UntrustedStr)
        assert content == "plain safe text"


# ---------------------------------------------------------------------------
# Round-trip: nested content (list of TextBlock-like dicts)
# ---------------------------------------------------------------------------

class TestTaintSurvivesNestedContent:
    async def test_text_block_nested_taint_preserved(self, tmp_path: Path) -> None:
        """Tainted text inside a TextBlock inside a content list must
        survive ``dataclasses.asdict`` + json round-trip."""
        store = FileStore(base_dir=tmp_path)
        tainted = UntrustedStr("hello from model", TaintSource.MODEL_OUTPUT)
        msg = Message(
            role="assistant",
            content=[TextBlock(text=tainted)],
            id="m-nest",
            timestamp="2025-01-01T00:00:00+00:00",
        )
        await store.save("sess-nest", [msg])

        loaded = await store.load("sess-nest")
        assert loaded is not None
        block = loaded[0]["content"][0]
        assert block["type"] == "text"
        text = block["text"]
        assert isinstance(text, UntrustedStr)
        assert text.source == TaintSource.MODEL_OUTPUT

    async def test_mixed_tainted_and_plain_blocks(self, tmp_path: Path) -> None:
        """A content list with one tainted block and one plain block
        must reload with exactly that mixed shape."""
        store = FileStore(base_dir=tmp_path)
        msg = Message(
            role="assistant",
            content=[
                {"type": "text", "text": UntrustedStr(
                    "tainted", TaintSource.MODEL_OUTPUT,
                )},
                {"type": "text", "text": "plain"},
            ],
            id="m-mix",
            timestamp="2025-01-01T00:00:00+00:00",
        )
        await store.save("sess-mix", [msg])

        loaded = await store.load("sess-mix")
        assert loaded is not None
        blocks = loaded[0]["content"]
        assert isinstance(blocks[0]["text"], UntrustedStr)
        assert blocks[0]["text"].source == TaintSource.MODEL_OUTPUT
        assert not isinstance(blocks[1]["text"], UntrustedStr)
        assert blocks[1]["text"] == "plain"


# ---------------------------------------------------------------------------
# All TaintSource variants round-trip distinctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    TaintSource.USER_INPUT,
    TaintSource.MODEL_OUTPUT,
    TaintSource.TOOL_OUTPUT,
    TaintSource.FILE_CONTENT,
    TaintSource.MCP_OUTPUT,
    TaintSource.NETWORK,
    TaintSource.SYSTEM,
])
async def test_all_taint_sources_round_trip(
    tmp_path: Path, source: TaintSource,
) -> None:
    """Each TaintSource variant must be distinguishable after round-trip
    so the policy gate can honour per-source rules (UNTAINTED_SOURCES
    vs tainted sources)."""
    store = FileStore(base_dir=tmp_path)
    value = UntrustedStr("x", source)
    msg = Message(
        role="user", content=value,
        id=f"m-{source.value}", timestamp="2025-01-01T00:00:00+00:00",
    )
    await store.save(f"sess-{source.value}", [msg])

    loaded = await store.load(f"sess-{source.value}")
    assert loaded is not None
    reloaded = loaded[0]["content"]
    assert isinstance(reloaded, UntrustedStr)
    assert reloaded.source == source
