"""MCP tool description Unicode normalization (ADR-054, 7.6).

Rejects descriptions containing invisible characters used in GlassWorm-style
prompt injection: zero-width chars, bidi overrides, tag characters,
variation selectors. NFKC normalization catches confusable characters.

Unicode character sets detected:
- Zero-width characters (U+200B, U+200C, U+200D, U+FEFF): invisible spacing
  that can conceal instructions from human readers.
- Bidi override characters (Cf category): can reverse display order to hide
  malicious content (e.g. RLO attacks).
- Tag Characters (U+E0000..U+E007F): designed for language tagging but
  completely invisible; used to smuggle instructions through embedding.
- Variation Selectors (U+FE00-U+FE0F, U+E0100-U+E01EF): alter glyph
  appearance while leaving the base character unchanged; exploited to
  hide data in seemingly-normal text.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize_mcp_description"]

# Unicode category "Cf" = Format characters (invisible, zero-width, bidi etc.)
_REJECT_CATEGORIES: frozenset[str] = frozenset({"Cf"})

# Unicode Tag Characters block (U+E0000..U+E007F)
_TAG_BLOCK = re.compile(r"[\U000E0000-\U000E007F]")

# Variation Selectors: VS1-VS16 (U+FE00-U+FE0F) and VS17-VS256 (U+E0100-U+E01EF)
_VS = re.compile(r"[\uFE00-\uFE0F\U000E0100-\U000E01EF]")


def normalize_mcp_description(text: str) -> tuple[str, list[str]]:
    """Normalize an MCP tool description and report any security concerns.

    Applies NFKC normalization and scans for invisible/hostile Unicode.

    Args:
        text: The raw description string from an MCP tool manifest.

    Returns:
        A (normalized_text, issues) tuple. ``issues`` is an empty list when
        the description is clean. Each entry in ``issues`` is a human-readable
        reason string suitable for logging or raising as an error.
    """
    issues: list[str] = []

    # NFKC normalization: collapses compatibility equivalents (ligatures,
    # full-width forms, etc.) to their canonical forms.
    nfkc = unicodedata.normalize("NFKC", text)
    nfc = unicodedata.normalize("NFC", text)
    # Only flag as suspicious if NFKC differs AND the change is not just
    # canonical composition (NFC).  Canonical composition (e.g. a + combining
    # grave → à) is legitimate; compatibility equivalents (e.g. ﬁ → fi) are
    # the security-relevant case.
    if nfkc != text and nfkc != nfc:
        issues.append("NFKC normalization changed the text (confusable characters)")

    # Scan for format-class characters (bidi overrides, zero-width joiners,
    # soft hyphens, etc.). Tag Characters and Variation Selectors are also
    # Cf or have dedicated blocks — check those separately for precise messages.
    for ch in text:
        if _TAG_BLOCK.match(ch):
            # Tag Characters are checked below as a block; skip here
            continue
        if _VS.match(ch):
            # Variation Selectors are checked below; skip here
            continue
        cat = unicodedata.category(ch)
        if cat in _REJECT_CATEGORIES:
            issues.append(f"format-class char: U+{ord(ch):04X}")

    # Unicode Tag Characters — completely invisible, used to exfiltrate data
    if _TAG_BLOCK.search(text):
        issues.append("contains Unicode Tag Characters (U+E0000..U+E007F)")

    # Variation Selectors — alter glyph appearance, used to hide data
    if _VS.search(text):
        issues.append("contains invisible variation selectors")

    return nfkc, issues
