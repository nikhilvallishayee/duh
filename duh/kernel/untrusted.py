"""UntrustedStr — taint-propagating str subclass (ADR-054, workstream 7.1).

A str that remembers where its bytes came from. Every str-returning method is
overridden to produce an UntrustedStr with the same (or merged) TaintSource,
so a path like

    model_out = UntrustedStr(provider_stream, TaintSource.MODEL_OUTPUT)
    rendered  = f"prompt={model_out.upper()}"

carries the taint from the provider straight into rendered's source tag. The
policy resolver can then refuse dangerous tool calls that traced through a
tainted ancestor.

Environment variables:
  DUH_TAINT_DEBUG=1   — print every str op that preserves/merges taint
  DUH_TAINT_STRICT=1  — raise TaintLossError on any silent tag drop
"""

from __future__ import annotations

import os
import sys as _sys
from enum import Enum
from typing import Any

__all__ = [
    "TaintSource",
    "UNTAINTED_SOURCES",
    "UntrustedStr",
    "TaintLossError",
    "merge_source",
    "assert_no_tag_loss",
]


class TaintSource(str, Enum):
    USER_INPUT = "user_input"      # untainted — REPL, /continue, AskUserQuestion
    MODEL_OUTPUT = "model_output"  # tainted
    TOOL_OUTPUT = "tool_output"    # tainted
    FILE_CONTENT = "file_content"  # tainted
    MCP_OUTPUT = "mcp_output"      # tainted
    NETWORK = "network"            # tainted
    SYSTEM = "system"              # untainted — D.U.H. prompts, config, skills


UNTAINTED_SOURCES: frozenset[TaintSource] = frozenset(
    {TaintSource.USER_INPUT, TaintSource.SYSTEM}
)


class TaintLossError(RuntimeError):
    """Raised when DUH_TAINT_STRICT=1 and a str op silently drops taint."""


def _strict() -> bool:
    return os.environ.get("DUH_TAINT_STRICT", "") == "1"


def _debug() -> bool:
    return os.environ.get("DUH_TAINT_DEBUG", "") == "1"


def _record_preserve(op: str, src: TaintSource) -> None:
    if _debug():
        print(f"[taint] preserved {op} src={src.value}", file=_sys.stderr)


def _record_drop(op: str, expected_src: object) -> None:
    if _strict():
        raise TaintLossError(f"taint dropped by {op}; expected src={expected_src}")
    if _debug():
        print(f"[taint] DROPPED {op} expected={expected_src}", file=_sys.stderr)


def merge_source(a: Any, b: Any) -> TaintSource:
    """Combine two source tags; tainted wins over untainted."""
    a_src = getattr(a, "_source", TaintSource.SYSTEM)
    b_src = getattr(b, "_source", TaintSource.SYSTEM)
    if a_src in UNTAINTED_SOURCES and b_src in UNTAINTED_SOURCES:
        return a_src
    if a_src in UNTAINTED_SOURCES:
        return b_src
    if b_src in UNTAINTED_SOURCES:
        return a_src
    return a_src


def assert_no_tag_loss(value: object, op: str) -> None:
    """Call from test infrastructure: if value is a plain str but was expected
    to be UntrustedStr, raise TaintLossError under strict mode."""
    if isinstance(value, str) and not isinstance(value, UntrustedStr):
        _record_drop(op, "expected UntrustedStr, got plain str")


class UntrustedStr(str):
    """str subclass carrying a TaintSource tag.

    See module docstring for propagation semantics. This class intentionally
    defines __slots__ to avoid adding a __dict__ per instance — this keeps
    the per-operation overhead bounded.

    Methods inherited from str unchanged (return non-str, no taint to carry):
      __len__, __hash__, __bool__, __contains__, __iter__, __eq__, __ne__,
      __lt__, __le__, __gt__, __ge__, __repr__,
      count, startswith, endswith, find, rfind, index, rindex,
      isdigit, isalpha, isspace, istitle, isupper, islower,
      isnumeric, isdecimal, isalnum, isidentifier, isprintable, isascii.
    """

    __slots__ = ("_source",)

    _source: TaintSource

    def __new__(
        cls,
        value: object = "",
        source: TaintSource = TaintSource.MODEL_OUTPUT,
    ) -> "UntrustedStr":
        instance = super().__new__(cls, value)
        instance._source = source
        return instance

    @property
    def source(self) -> TaintSource:
        return self._source

    def is_tainted(self) -> bool:
        return self._source not in UNTAINTED_SOURCES

    # ------------------------------------------------------------------
    # Internal wrap helper
    # ------------------------------------------------------------------

    def _wrap(self, value: object, source: TaintSource | None = None) -> "UntrustedStr":
        src = source if source is not None else self._source
        return UntrustedStr(value, src)

    # ------------------------------------------------------------------
    # Concatenation operators (7.1.4)
    # ------------------------------------------------------------------

    def __add__(self, other: object) -> "UntrustedStr":
        result = super().__add__(other)  # type: ignore[arg-type]
        return self._wrap(result, merge_source(self, other))

    def __radd__(self, other: object) -> "UntrustedStr":
        result = other.__add__(self) if isinstance(other, str) else NotImplemented  # type: ignore[arg-type]
        if result is NotImplemented:
            return NotImplemented  # type: ignore[return-value]
        return self._wrap(result, merge_source(other, self))

    # ------------------------------------------------------------------
    # % formatting (7.1.5)
    # ------------------------------------------------------------------

    def __mod__(self, args: object) -> "UntrustedStr":
        result = super().__mod__(args)
        src = self._source
        if isinstance(args, tuple):
            for item in args:
                src = merge_source(UntrustedStr("", src), item)
        else:
            src = merge_source(UntrustedStr("", src), args)
        return UntrustedStr(result, src)

    # ------------------------------------------------------------------
    # Repetition (7.1.6)
    # ------------------------------------------------------------------

    def __mul__(self, n: int) -> "UntrustedStr":
        return UntrustedStr(super().__mul__(n), self._source)

    def __rmul__(self, n: int) -> "UntrustedStr":
        return UntrustedStr(super().__rmul__(n), self._source)

    # ------------------------------------------------------------------
    # format / format_map (7.1.7)
    # ------------------------------------------------------------------

    def format(self, *args: object, **kwargs: object) -> "UntrustedStr":  # type: ignore[override]
        result = super().format(*args, **kwargs)
        src = self._source
        for a in args:
            src = merge_source(UntrustedStr("", src), a)
        for v in kwargs.values():
            src = merge_source(UntrustedStr("", src), v)
        return UntrustedStr(result, src)

    def format_map(self, mapping: object) -> "UntrustedStr":  # type: ignore[override]
        result = super().format_map(mapping)
        src = self._source
        try:
            for v in mapping.values():  # type: ignore[attr-defined]
                src = merge_source(UntrustedStr("", src), v)
        except AttributeError:
            pass
        return UntrustedStr(result, src)

    # ------------------------------------------------------------------
    # join (7.1.8)
    # ------------------------------------------------------------------

    def join(self, iterable) -> "UntrustedStr":  # type: ignore[override]
        parts = list(iterable)
        result = super().join(parts)
        src = self._source
        for p in parts:
            src = merge_source(UntrustedStr("", src), p)
        return UntrustedStr(result, src)

    # ------------------------------------------------------------------
    # replace (7.1.9)
    # ------------------------------------------------------------------

    def replace(self, old, new, count=-1) -> "UntrustedStr":  # type: ignore[override]
        result = super().replace(old, new, count)
        src = merge_source(self, new)
        src = merge_source(UntrustedStr("", src), old)
        return UntrustedStr(result, src)

    # ------------------------------------------------------------------
    # strip family (7.1.10)
    # ------------------------------------------------------------------

    def strip(self, chars=None) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().strip(chars), self._source)

    def lstrip(self, chars=None) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().lstrip(chars), self._source)

    def rstrip(self, chars=None) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().rstrip(chars), self._source)

    # ------------------------------------------------------------------
    # split family (7.1.11)
    # ------------------------------------------------------------------

    def split(self, sep=None, maxsplit=-1) -> list["UntrustedStr"]:  # type: ignore[override]
        return [UntrustedStr(p, self._source) for p in super().split(sep, maxsplit)]

    def rsplit(self, sep=None, maxsplit=-1) -> list["UntrustedStr"]:  # type: ignore[override]
        return [UntrustedStr(p, self._source) for p in super().rsplit(sep, maxsplit)]

    def splitlines(self, keepends=False) -> list["UntrustedStr"]:  # type: ignore[override]
        return [UntrustedStr(p, self._source) for p in super().splitlines(keepends)]

    # ------------------------------------------------------------------
    # Case methods (7.1.12)
    # ------------------------------------------------------------------

    def lower(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().lower(), self._source)

    def upper(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().upper(), self._source)

    def title(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().title(), self._source)

    def casefold(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().casefold(), self._source)

    def capitalize(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().capitalize(), self._source)

    def swapcase(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().swapcase(), self._source)

    # ------------------------------------------------------------------
    # Padding methods (7.1.13)
    # ------------------------------------------------------------------

    def expandtabs(self, tabsize=8) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().expandtabs(tabsize), self._source)

    def center(self, width, fillchar=" ") -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().center(width, fillchar), self._source)

    def ljust(self, width, fillchar=" ") -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().ljust(width, fillchar), self._source)

    def rjust(self, width, fillchar=" ") -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().rjust(width, fillchar), self._source)

    def zfill(self, width) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().zfill(width), self._source)

    # ------------------------------------------------------------------
    # translate / removeprefix / removesuffix (7.1.14)
    # encode returns bytes — pass through unchanged
    # ------------------------------------------------------------------

    def translate(self, table) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().translate(table), self._source)

    def removeprefix(self, prefix) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().removeprefix(prefix), self._source)

    def removesuffix(self, suffix) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().removesuffix(suffix), self._source)

    # ------------------------------------------------------------------
    # Slicing (7.1.15)
    # ------------------------------------------------------------------

    def __getitem__(self, key) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().__getitem__(key), self._source)

    # ------------------------------------------------------------------
    # f-string / format() propagation (SEC-LOW-3 + INFO-2)
    # ------------------------------------------------------------------
    #
    # ``f"{tainted}"`` (and ``format(tainted, spec)``) calls
    # ``tainted.__format__(spec)``. The default ``str.__format__`` returns a
    # plain ``str``, which silently drops taint. We override to return an
    # ``UntrustedStr`` carrying the same source so single-variable f-strings
    # preserve taint end-to-end.
    #
    # Note: multi-part f-strings like ``f"pre {tainted} post"`` produce a
    # plain ``str`` because CPython's BUILD_STRING concatenates constant
    # literals with the format result — that path cannot be intercepted
    # from Python. For those cases, use ``UntrustedStr`` concatenation or
    # ``.format()`` directly, both of which DO preserve taint.

    def __format__(self, format_spec: str) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().__format__(format_spec), self._source)
