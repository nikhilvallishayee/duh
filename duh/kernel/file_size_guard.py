"""Guard against reading files that would blow the model's context window.

When a file is larger than 50% of the model's context window (estimated by
char → token conversion), refuse the read and surface a clear message.
This prevents accidental context overflow on smaller models and keeps tool
output predictable across SDK / REPL / TUI.

The same coarse heuristic (1 token ≈ 4 chars) that powers
``USAGE_DELTA_CHARS_PER_TOKEN`` in ``engine.py`` is reused here — we are
intentionally conservative: a real tokenizer would generally produce fewer
tokens than this estimate for English prose, so any file we refuse under
this rule would almost certainly be an unwise read under a real tokenizer
too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from duh.kernel.model_caps import get_capabilities
from duh.kernel.tokens import CHARS_PER_TOKEN

# ``CHARS_PER_TOKEN`` is re-exported so existing imports of
# ``duh.kernel.file_size_guard.CHARS_PER_TOKEN`` (including tests) keep
# working. The single source of truth lives in ``duh.kernel.tokens``.
__all__ = ["CHARS_PER_TOKEN", "MAX_FILE_FRACTION", "FileSizeDecision", "check_file_size"]

MAX_FILE_FRACTION = 0.5  # skip files > 50% of context window


@dataclass(frozen=True)
class FileSizeDecision:
    """Verdict on whether a file is safe to read under a model's context budget."""

    allowed: bool
    reason: str  # human-readable; empty if allowed
    estimated_tokens: int
    budget_tokens: int  # `fraction` × context_window


def check_file_size(
    file_path: str,
    model: str,
    *,
    size_bytes: int | None = None,
    fraction: float = MAX_FILE_FRACTION,
) -> FileSizeDecision:
    """Return a decision on whether *file_path* can be safely read under
    *model*'s context budget.

    If *size_bytes* is ``None``, the file is ``stat``\\ ed to determine its
    size.  An ``OSError`` during ``stat`` is treated as "allow" — let the
    caller surface the real read error naturally rather than masking it with
    a size refusal.
    """
    caps = get_capabilities(model)
    budget = int(caps.context_window * fraction)

    if size_bytes is None:
        try:
            size_bytes = Path(file_path).stat().st_size
        except OSError:
            # If we can't stat, let the caller handle the actual read error.
            return FileSizeDecision(True, "", 0, budget)

    estimated = size_bytes // CHARS_PER_TOKEN
    if estimated > budget:
        reason = (
            f"File is ~{estimated:,} tokens (estimated from {size_bytes:,} bytes), "
            f"which exceeds {fraction * 100:.0f}% of the {caps.context_window:,}-token "
            f"context window for {model}. Use offset/limit to read a slice, "
            f"or switch to a model with a larger context."
        )
        return FileSizeDecision(False, reason, estimated, budget)

    return FileSizeDecision(True, "", estimated, budget)
