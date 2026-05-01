"""Tool-call argument repair (Hermes-pattern, ADR-028).

Open-weights and locally-fine-tuned models routinely emit
*almost-valid* JSON in their tool-call arguments. Common breakage:

- Trailing commas (``{"path": "x.py",}``)
- Python literals where JSON wants lowercase (``True``/``False``/``None``)
- Unescaped control characters in string values
- Smart-quote substitutions (``"x"`` → ``"x"``)
- Stray prose wrapper around the JSON body

Strict ``json.loads()`` rejects all of these. The model gets the
parser error, retries with the same broken pattern, and the agent
loop spirals.

This module provides :func:`repair_tool_arguments` — a Hermes-style
permissive parser that recovers structured args from the common
failure modes before strict JSON parsing kicks in. Returns ``None``
when the input is genuinely unrecoverable so callers can surface a
real error rather than silently masking malformed output.

Pattern adopted from ``NousResearch/hermes-agent``'s
``_repair_tool_call_arguments()``. Non-destructive — never deletes
content; only fixes shape.
"""

from __future__ import annotations

import json
import re
from typing import Any


# ---- single-pass repairs (run in order, idempotent) -----------------

def _strip_prose_wrapper(text: str) -> str:
    """Pull out the first balanced ``{...}`` or ``[...]`` block.

    Models often wrap JSON in narration: ``Here is the call: {…}.``.
    """
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        return text
    # Find first { or [ then brace-count to the close.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text


def _strip_trailing_commas(text: str) -> str:
    """Remove ``,}`` and ``,]`` patterns. Repeated until stable."""
    pattern = re.compile(r",(\s*[}\]])")
    prev = None
    while prev != text:
        prev = text
        text = pattern.sub(r"\1", text)
    return text


_PYTHON_LITERAL_PATTERNS = [
    # Match the bare token only when it's used as a value: after :, [, , or
    # at array-start. Avoid replacing ``"True"`` (quoted strings are fine).
    (re.compile(r"(?<=[:\[,\s])True(?=\s*[,\]\}])"),  "true"),
    (re.compile(r"(?<=[:\[,\s])False(?=\s*[,\]\}])"), "false"),
    (re.compile(r"(?<=[:\[,\s])None(?=\s*[,\]\}])"),  "null"),
]


def _normalize_python_literals(text: str) -> str:
    """Lowercase ``True``/``False``/``None`` to JSON booleans / null."""
    out = text
    for pat, repl in _PYTHON_LITERAL_PATTERNS:
        out = pat.sub(repl, out)
    return out


_SMART_QUOTES = {
    "\u201c": '"',  # left double quote
    "\u201d": '"',  # right double quote
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote
}


def _normalize_smart_quotes(text: str) -> str:
    out = text
    for src, dst in _SMART_QUOTES.items():
        out = out.replace(src, dst)
    return out


def _escape_lone_control_chars_in_strings(text: str) -> str:
    """Replace bare newline/tab inside JSON strings with their escapes.

    Walks character-by-character, tracking string state. Anything inside
    a string that's a raw ``\\n`` / ``\\t`` / ``\\r`` gets escaped.
    Leaves out-of-string whitespace (between tokens) untouched.
    """
    out: list[str] = []
    in_str = False
    esc = False
    for c in text:
        if in_str:
            if esc:
                esc = False
                out.append(c)
                continue
            if c == "\\":
                esc = True
                out.append(c)
                continue
            if c == '"':
                in_str = False
                out.append(c)
                continue
            if c == "\n":
                out.append("\\n"); continue
            if c == "\t":
                out.append("\\t"); continue
            if c == "\r":
                out.append("\\r"); continue
            out.append(c)
            continue
        if c == '"':
            in_str = True
        out.append(c)
    return "".join(out)


# ---- public entry point ---------------------------------------------

def repair_tool_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """Best-effort recovery of a tool-call arguments dict.

    Returns ``None`` when *raw* is empty/None/unrecoverable. A successful
    return is always a dict (possibly empty), suitable for direct use
    as a tool-call ``input``.

    Pipeline (each step idempotent):

    1. Strip prose wrappers and isolate the first ``{...}`` block.
    2. Normalise smart quotes to ASCII quotes.
    3. Lowercase Python literals (``True``/``False``/``None``).
    4. Escape bare newlines/tabs/CRs inside strings.
    5. Remove trailing commas before ``}`` / ``]``.
    6. Strict ``json.loads``.

    Any step that succeeds short-circuits — strict JSON is tried first.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text:
        return {}

    # Fast path — already valid JSON.
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass

    # Apply repairs in order; try parsing after each pass.
    candidate = text
    for step in (
        _strip_prose_wrapper,
        _normalize_smart_quotes,
        _normalize_python_literals,
        _escape_lone_control_chars_in_strings,
        _strip_trailing_commas,
    ):
        candidate = step(candidate)
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            continue

    # Final try after all repairs combined.
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return None
