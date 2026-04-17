"""Output style configuration for D.U.H. (ADR-062, ADR-073).

:class:`OutputStyle` is the user-facing verbosity selector.

:class:`OutputTruncationPolicy` is the single source of truth for *how much*
tool output to display — consulted by both the REPL :class:`RichRenderer`
and the TUI :class:`ToolCallWidget` so that a given style renders the same
characters in either frontend.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OutputStyle(str, Enum):
    DEFAULT = "default"      # Full markdown, tool panels
    CONCISE = "concise"      # Minimal output, status only for tools
    VERBOSE = "verbose"      # Full tool output, thinking visible


@dataclass(frozen=True)
class OutputTruncationPolicy:
    """Single source of truth for how much tool output to show.

    ``max_chars`` and ``max_lines`` are both honoured — the more restrictive
    of the two wins.  ``max_chars = 0`` suppresses the output entirely (used
    by :attr:`OutputStyle.CONCISE` for successful tool calls where only the
    status indicator is shown).
    """

    max_chars: int
    max_lines: int

    @classmethod
    def for_style(
        cls, style: OutputStyle, is_error: bool = False
    ) -> "OutputTruncationPolicy":
        """Return the truncation policy for *style* × *is_error*.

        Matrix (chars / lines):

        ================ ============ ============
        Style            Error        Success
        ================ ============ ============
        CONCISE          200 / 5      0 / 0
        DEFAULT          300 / 10     120 / 3
        VERBOSE          2000 / 30    2000 / 60
        ================ ============ ============
        """
        if style is OutputStyle.CONCISE:
            if is_error:
                return cls(max_chars=200, max_lines=5)
            return cls(max_chars=0, max_lines=0)
        if style is OutputStyle.VERBOSE:
            if is_error:
                return cls(max_chars=2000, max_lines=30)
            return cls(max_chars=2000, max_lines=60)
        # DEFAULT
        if is_error:
            return cls(max_chars=300, max_lines=10)
        return cls(max_chars=120, max_lines=3)

    def apply(self, text: str) -> tuple[str, bool]:
        """Truncate *text* per the policy.

        Returns ``(truncated_text, was_truncated)``.  Respects both
        :attr:`max_chars` and :attr:`max_lines`; whichever limit hits first
        decides the cut.
        """
        if text is None:
            return "", False
        if self.max_chars <= 0 or self.max_lines <= 0:
            return "", bool(text)

        was_truncated = False
        # Line cap first so we don't count bytes from lines we'd drop anyway.
        lines = text.split("\n")
        if len(lines) > self.max_lines:
            text = "\n".join(lines[: self.max_lines])
            was_truncated = True

        # Then char cap.
        if len(text) > self.max_chars:
            text = text[: self.max_chars]
            was_truncated = True

        return text, was_truncated
