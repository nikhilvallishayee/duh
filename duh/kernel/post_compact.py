"""Post-compact file state rebuild (ADR-058).

After compaction, re-read the most recently accessed files so the model
retains file context.  The function appends file-content messages for
each file that (a) still exists on disk and (b) fits within the
per-file token budget.

    from duh.kernel.post_compact import rebuild_post_compact_context

    messages = await rebuild_post_compact_context(
        messages, file_tracker, max_files=5, max_tokens_per_file=5000,
    )
"""

from __future__ import annotations

import logging
import os
from typing import Any

from duh.kernel.messages import Message

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_FILES = 5
DEFAULT_MAX_TOKENS_PER_FILE = 5_000

# Rough chars-per-token multiplier (same heuristic used elsewhere in D.U.H.)
_CHARS_PER_TOKEN = 4


async def rebuild_post_compact_context(
    messages: list[Any],
    file_tracker: Any,
    max_files: int = DEFAULT_MAX_FILES,
    max_tokens_per_file: int = DEFAULT_MAX_TOKENS_PER_FILE,
) -> list[Any]:
    """Append file-content messages for recently-read files after compaction.

    Walks the file tracker's operation list in reverse to find the most
    recently accessed *unique* paths (up to ``max_files``).  For each
    file that still exists, reads its content (truncated to
    ``max_tokens_per_file`` tokens) and appends a system message
    carrying the content.

    Args:
        messages: The post-compaction message list (modified copy returned).
        file_tracker: A ``FileTracker`` instance (or any object with an
            ``ops`` attribute yielding objects with a ``.path`` attribute).
            May be ``None``, in which case the input is returned as-is.
        max_files: Maximum number of files to re-inject.
        max_tokens_per_file: Per-file token budget (chars / 4).

    Returns:
        A new list with zero or more appended system messages carrying
        file contents.
    """
    if file_tracker is None:
        return list(messages)

    ops = file_tracker.ops if hasattr(file_tracker, "ops") else []
    if not ops:
        return list(messages)

    # Collect unique recent paths (most-recent first)
    seen: set[str] = set()
    recent_paths: list[str] = []
    for op in reversed(ops):
        path = op.path if hasattr(op, "path") else str(op)
        if path not in seen:
            seen.add(path)
            recent_paths.append(path)
        if len(recent_paths) >= max_files:
            break

    if not recent_paths:
        return list(messages)

    result = list(messages)
    max_chars = max_tokens_per_file * _CHARS_PER_TOKEN
    injected = 0

    for path in recent_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", errors="replace") as f:
                content = f.read(max_chars)
            if not content.strip():
                continue
            if len(content) >= max_chars:
                content = content[:max_chars - 3] + "..."
            restore_msg = Message(
                role="system",
                content=(
                    f"[Post-compaction file restore: {path}]\n"
                    f"```\n{content}\n```"
                ),
                metadata={
                    "subtype": "post_compact_file_restore",
                    "path": path,
                },
            )
            result.append(restore_msg)
            injected += 1
        except (OSError, PermissionError):
            logger.debug("Could not read file for post-compact restore: %s", path)
            continue

    if injected:
        logger.info(
            "Post-compact file rebuild: injected %d file(s) into context",
            injected,
        )

    return result
