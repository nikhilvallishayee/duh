#!/usr/bin/env python3
"""04 — RLM replaces threshold-based compaction (philosophical jump).

Hermes Agent's ``context_compressor`` triggers at 50% of the context
window, summarises down to 20%, and protects head + tail messages.
This is a sensible engineering compromise — but it is **lossy**.
Bytes that get summarised are gone; the agent reasons about a
paraphrase from then on.

duhwave's ADR-028 RLM substrate makes a different jump: bulk content
lives in a sandboxed REPL as a named handle. The agent's working
context stays small because it addresses bytes by reference
(``Peek`` / ``Search`` / ``Slice``) rather than by inclusion. Nothing
ever gets summarised; nothing ever gets dropped.

This script binds 500,000 characters to a handle and demonstrates
that byte 499,950 — well past where any threshold-based compactor
would have summarised — is still there, byte-exact, addressable.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_hermes/04_rlm_replaces_compaction.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.rlm.repl import RLMRepl  # noqa: E402


# ---- pretty output -------------------------------------------------------


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def step(msg: str) -> None:
    print(f"  → {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


# ---- demo content --------------------------------------------------------


def build_payload(total_chars: int) -> str:
    """Synthetic 500 KB payload with embedded position markers.

    Every 100 chars we drop a marker like ``[POS=000400]`` so any peek
    can be cross-referenced against the byte position it claims.
    """
    parts: list[str] = []
    written = 0
    while written < total_chars:
        marker = f"[POS={written:06d}]"
        parts.append(marker)
        written += len(marker)
        # Pad with deterministic filler.
        filler_len = max(0, min(100 - len(marker), total_chars - written))
        if filler_len:
            parts.append("." * filler_len)
            written += filler_len
    payload = "".join(parts)
    return payload[:total_chars]


# ---- the demo ------------------------------------------------------------


async def main() -> int:
    section("04 — RLM replaces compaction (Hermes → ADR-028 substitution)")
    print()
    print("  Hermes:  context_compressor — summarise at 50% full,")
    print("           target 20%, protect first 3 + last 20 messages.")
    print("           Lossy: bytes outside the protected window get paraphrased.")
    print()
    print("  duhwave: bind bulk content as a REPL handle. The agent's")
    print("           context window stays small because it reads through")
    print("           Peek / Search / Slice — bytes are addressed by")
    print("           reference, never by inclusion. Loss-free.")

    total_chars = 500_000
    section(f"1. Bind a {total_chars:,}-char payload to a single handle")
    payload = build_payload(total_chars)
    assert len(payload) == total_chars, f"payload len {len(payload)} != {total_chars}"

    repl = RLMRepl()
    await repl.start()
    try:
        step(f"bind('haystack', <{total_chars:,} chars>)")
        handle = await repl.bind("haystack", payload)
        ok(
            f"handle bound: chars={handle.total_chars:,}  "
            f"bytes={handle.total_bytes:,}  sha256={handle.sha256[:12]}…"
        )

        section("2. The compaction-vs-RLM contrast")
        print()
        print("  A threshold-based compactor would, by now, have summarised this")
        print("  500 KB blob into a few sentences. Anything beyond the 'protect")
        print("  tail' window would be unreadable.")
        print()
        print("  → compaction would summarise this. RLM keeps every byte addressable.")
        print()

        section("3. Peek bytes 499,950..500,000 — past the compaction horizon")
        peek_start = 499_950
        peek_end = 500_000
        step(f"peek('haystack', start={peek_start:,}, end={peek_end:,})")
        tail = await repl.peek("haystack", start=peek_start, end=peek_end)
        print(f"    bytes: {tail!r}")
        ok(f"RLM addresses byte {peek_start:,}: <peek>={tail!r}")

        section("4. Cross-check: peek the marker block at byte 499,900")
        # Each marker is 12 chars (e.g. "[POS=499900]") and they appear every 100 chars.
        # The marker for position 499,900 is exactly at byte 499,900.
        marker_start = 499_900
        marker_end = 499_912
        step(f"peek('haystack', start={marker_start:,}, end={marker_end:,})")
        marker_bytes = await repl.peek(
            "haystack", start=marker_start, end=marker_end
        )
        expected_marker = "[POS=499900]"
        print(f"    bytes:    {marker_bytes!r}")
        print(f"    expected: {expected_marker!r}")
        if marker_bytes != expected_marker:
            fail(
                f"expected marker {expected_marker!r} at byte {marker_start:,} "
                f"but got {marker_bytes!r} — RLM is not byte-exact at this offset."
            )
            return 1
        ok(f"marker {expected_marker!r} byte-exact at offset {marker_start:,} — no summarisation occurred")

        section("5. Search the whole handle for that marker by regex")
        hits = await repl.search("haystack", r"\[POS=499900\]", max_hits=5)
        if not hits:
            fail("regex search returned 0 hits — handle is not byte-addressable")
            return 1
        for h in hits:
            print(
                f"    line={h.get('line', '?'):>4}  "
                f"col={h.get('col', '?'):>3}  "
                f"snippet={h.get('snippet', '')!r}"
            )
        ok(f"search recovered {len(hits)} hit(s) — full {total_chars:,} chars remain searchable")

        section("Summary")
        ok(f"RLM addresses byte {peek_start:,}: {tail!r}")
        return 0
    finally:
        await repl.shutdown()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
