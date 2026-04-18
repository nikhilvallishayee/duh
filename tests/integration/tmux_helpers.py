"""Tmux helpers for ADR-074 Tier C integration tests.

Thin wrappers around :mod:`libtmux` for starting the real ``duh`` binary
inside a real tmux pane, exercising multi-pane / scroll-history / resize
scenarios that neither Pilot nor PTY-pyte can cover.

Every helper here is defensive:

* Sessions are created with ``attach=False`` so they run headless in CI.
* Session names embed ``os.getpid()`` and a caller-supplied suffix so
  parallel tests never collide.
* ``cleanup()`` swallows exceptions — leaking a tmux session leaks a
  subprocess, so tests must always call it in a ``finally``.
"""

from __future__ import annotations

import os
import shlex
import sys
import time
from typing import Iterable

import libtmux


DEFAULT_BOOT_DELAY = 0.5  # seconds — how long to wait for duh to paint its prompt


def _build_session_name(suffix: str | None) -> str:
    base = f"duh-test-{os.getpid()}"
    if suffix:
        # tmux dislikes dots in session names; use dashes
        suffix_clean = suffix.replace(".", "-").replace(":", "-")
        return f"{base}-{suffix_clean}"
    return base


def start_duh_in_tmux(
    cmd: list[str] | None = None,
    width: int = 120,
    height: int = 40,
    session_name: str | None = None,
    env_extra: dict[str, str] | None = None,
    boot_delay: float = DEFAULT_BOOT_DELAY,
) -> tuple[libtmux.Server, libtmux.Session, libtmux.Pane, str]:
    """Start a tmux session with duh running.

    Returns ``(server, session, pane, session_name)``. The caller is
    responsible for invoking :func:`cleanup` on ``session_name`` in a
    ``finally`` block.
    """

    server = libtmux.Server()
    resolved_name = _build_session_name(session_name)

    # Kill any stale session with this name (rare, but can happen after a
    # previous crash).
    try:
        server.kill_session(target_session=resolved_name)
    except Exception:  # noqa: BLE001
        pass

    # Resolve to the *current* interpreter so the spawned shell (which does
    # NOT inherit virtualenv activation) still imports the right ``duh``.
    shell_parts = cmd or [sys.executable, "-m", "duh"]
    shell_cmd = " ".join(shlex.quote(p) for p in shell_parts)

    env_pairs = {"DUH_STUB_PROVIDER": "1"}
    if env_extra:
        env_pairs.update(env_extra)
    env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env_pairs.items())
    full_cmd = f"{env_prefix} {shell_cmd}"

    session = server.new_session(
        session_name=resolved_name,
        window_command=full_cmd,
        x=width,
        y=height,
        attach=False,
    )

    # Give tmux a beat to attach the window + spawn duh *before* we ask for
    # the pane handle. libtmux queries the server synchronously; if we race
    # it, tmux reports "no server running" because the window spawned + died
    # before the list-windows call lands. A sleep here is load-bearing.
    time.sleep(boot_delay)
    pane = session.windows[0].panes[0]
    return server, session, pane, resolved_name


def send_keys(pane: libtmux.Pane, keys: str, enter: bool = True, settle: float = 0.2) -> None:
    """Send keys to a pane.

    ``settle`` adds a small debounce so downstream ``capture_pane`` sees the
    effect of the keystroke. Callers that want tighter polling should use
    :func:`wait_for_text` instead.
    """

    pane.send_keys(keys, enter=enter)
    if settle > 0:
        time.sleep(settle)


def send_raw(pane: libtmux.Pane, keys: str) -> None:
    """Send raw key literals (e.g. ``C-c``, ``Escape``) without enter."""
    pane.send_keys(keys, enter=False, literal=False)


def capture_pane(pane: libtmux.Pane, start: int | None = None) -> str:
    """Capture pane text, optionally including scrollback history.

    ``start=-1000`` pulls up to 1000 lines of scrollback. ``start=None``
    captures only the visible screen.
    """

    kwargs: dict[str, int] = {}
    if start is not None:
        kwargs["start"] = start
    return "\n".join(pane.capture_pane(**kwargs))


def wait_for_text(pane: libtmux.Pane, text: str, timeout: float = 5.0, poll: float = 0.1) -> bool:
    """Poll the visible pane until ``text`` appears or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if text in capture_pane(pane):
            return True
        time.sleep(poll)
    return False


def wait_for_any(pane: libtmux.Pane, texts: Iterable[str], timeout: float = 5.0, poll: float = 0.1) -> str | None:
    """Poll until any of ``texts`` appears; return which one, or None on timeout."""
    deadline = time.monotonic() + timeout
    options = list(texts)
    while time.monotonic() < deadline:
        buf = capture_pane(pane)
        for opt in options:
            if opt in buf:
                return opt
        time.sleep(poll)
    return None


def cleanup(server: libtmux.Server, session_name: str) -> None:
    """Kill a tmux session by name. Safe to call multiple times."""
    try:
        server.kill_session(target_session=session_name)
    except Exception:  # noqa: BLE001
        pass
