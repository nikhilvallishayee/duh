"""PTY test helpers for Tier B TUI E2E tests (ADR-074).

Spawn the real `duh` binary inside a pseudo-TTY, feed its ANSI output into a
`pyte` screen emulator, and expose helpers for polling the rendered grid.

Design notes:
- `pexpect.spawn` accepts ``dimensions=(rows, cols)``. ``pyte.Screen`` takes
  ``(columns, lines)``. We normalize by taking a ``size=(cols, rows)`` tuple
  in :func:`spawn_duh` (pyte order) and reversing when passing to pexpect.
- ``encoding=None`` yields raw bytes so callers can byte-inspect the stream
  for CSI leaks (e.g. ``b"\\x1b["``).
- ``read_nonblocking`` + polling avoids fixed ``sleep`` calls; we honour a
  wall-clock budget via :func:`time.monotonic`.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Iterable

import pexpect
import pyte


_DEFAULT_TIMEOUT = 5


def spawn_duh(
    args: Iterable[str] | None = None,
    size: tuple[int, int] = (120, 40),
    timeout: int = _DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> tuple[pexpect.spawn, pyte.Screen, pyte.ByteStream]:
    """Spawn ``python -m duh`` in a PTY and return (child, screen, stream).

    Parameters
    ----------
    args:
        Extra CLI args (e.g. ``["--tui"]`` or ``["--help"]``).
    size:
        ``(cols, rows)`` — pyte order.  Pexpect wants ``(rows, cols)``.
    timeout:
        Default pexpect read timeout in seconds.
    env:
        Extra environment variables merged over the parent env.  The stub
        provider is always enabled.
    """
    full_env = os.environ.copy()
    full_env["DUH_STUB_PROVIDER"] = "1"
    if env:
        full_env.update(env)

    cmd = [sys.executable, "-m", "duh"] + list(args or [])
    cols, rows = size
    child = pexpect.spawn(
        cmd[0],
        cmd[1:],
        env=full_env,
        dimensions=(rows, cols),
        timeout=timeout,
        encoding=None,
    )
    screen = pyte.Screen(cols, rows)
    stream = pyte.ByteStream(screen)
    return child, screen, stream


def read_screen(
    child: pexpect.spawn,
    stream: pyte.ByteStream,
    duration: float = 0.5,
) -> bytes:
    """Read bytes from *child* for up to *duration* seconds, feed *stream*.

    Returns the raw bytes that were read (so the caller can byte-inspect
    them for CSI leakage etc).  Stops early on EOF.
    """
    collected = bytearray()
    end = time.monotonic() + duration
    while time.monotonic() < end:
        try:
            chunk = child.read_nonblocking(size=8192, timeout=0.05)
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            break
        if not chunk:
            break
        collected.extend(chunk)
        stream.feed(chunk)
    return bytes(collected)


def screen_text(screen: pyte.Screen) -> str:
    """Return the current rendered screen as stripped-line text."""
    return "\n".join(line.rstrip() for line in screen.display).strip("\n")


def wait_for_text(
    child: pexpect.spawn,
    stream: pyte.ByteStream,
    screen: pyte.Screen,
    text: str,
    timeout: float = 5.0,
) -> bool:
    """Poll the screen until *text* appears or *timeout* elapses."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        read_screen(child, stream, duration=0.2)
        if text in screen_text(screen):
            return True
    return False


def drain_until_eof(child: pexpect.spawn, timeout: float = 5.0) -> bytes:
    """Read everything up to EOF and return the raw bytes.

    Useful for once-shot commands like ``duh --help``.
    """
    try:
        child.expect(pexpect.EOF, timeout=timeout)
    except pexpect.TIMEOUT:
        pass
    out = child.before or b""
    # Combine with anything in the buffer after match (usually empty for EOF).
    return out if isinstance(out, (bytes, bytearray)) else out.encode("utf-8", "replace")


def screen_display_hash(screen: pyte.Screen) -> int:
    """Hash the visible display lines for rough visual-equality checks."""
    return hash(tuple(line for line in screen.display))
