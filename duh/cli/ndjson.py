"""NDJSON helpers for stream-json protocol (SDK compatibility).

Implements the Newline-Delimited JSON protocol used by the Claude Agent SDK
to communicate with CLI tools over stdin/stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR are valid JSON
# characters but break NDJSON receivers that split on newlines.
_LINE_SEPARATORS = str.maketrans({
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
})


def ndjson_write(obj: dict[str, Any], file: Any = None) -> None:
    """Write a single JSON object as one NDJSON line to stdout.

    Escapes U+2028/U+2029 to prevent line-splitting receivers from breaking.
    """
    out = file or sys.stdout
    line = json.dumps(obj, default=str).translate(_LINE_SEPARATORS)
    out.write(line + "\n")
    out.flush()


def ndjson_read_line(line: str) -> dict[str, Any] | None:
    """Parse a single NDJSON line. Returns None for blank/non-JSON lines."""
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    return json.loads(line)
