"""Output style configuration for D.U.H. (ADR-062)."""

from __future__ import annotations

from enum import Enum


class OutputStyle(str, Enum):
    DEFAULT = "default"      # Full markdown, tool panels
    CONCISE = "concise"      # Minimal output, status only for tools
    VERBOSE = "verbose"      # Full tool output, thinking visible
