"""Unix-socket RPC client + server primitives for the duhwave host.

Wire format: newline-delimited JSON over a Unix-domain socket. The
default shape is **unary** — one request line, one response line — and
the :func:`call` helper covers it. The socket lives at
``<waves_root>/host.sock``; the host's PID is at
``<waves_root>/host.pid``.

A second, **streaming** shape exists for ``logs follow=true``:
:func:`stream_call` sends one request line and reads zero or more
``:``-prefixed JSON stream items, terminated by an unprefixed
``{"done": true}\\n`` (or by a clean socket close). See the
``_stream_logs`` method in :mod:`duh.duhwave.cli.daemon` for the
server-side framing contract.

Server side lives in :mod:`duh.duhwave.cli.daemon`; this module is
shared by both client subcommands and the daemon's request loop.
"""
from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any


class HostRPCError(RuntimeError):
    """Failed to talk to the host daemon."""


def host_socket_path(waves_root: Path) -> Path:
    return waves_root / "host.sock"


def host_pid_path(waves_root: Path) -> Path:
    return waves_root / "host.pid"


def is_daemon_running(waves_root: Path) -> bool:
    pid_path = host_pid_path(waves_root)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)  # signal 0: just check existence
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but isn't ours; treat as running.
        return True
    return host_socket_path(waves_root).exists()


def call(waves_root: Path, payload: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
    """Send one JSON request, read one JSON response."""
    sock_path = host_socket_path(waves_root)
    if not sock_path.exists():
        raise HostRPCError(f"no host socket at {sock_path}")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = bytearray()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
            if buf.endswith(b"\n"):
                break
        if not buf:
            raise HostRPCError("empty response")
        return json.loads(buf.decode("utf-8"))
    except (OSError, socket.timeout) as e:
        raise HostRPCError(f"rpc transport error: {e}") from e
    except json.JSONDecodeError as e:
        raise HostRPCError(f"rpc decode error: {e}") from e
    finally:
        s.close()


def stream_call(
    waves_root: Path,
    payload: dict[str, Any],
    on_line: Callable[[dict[str, Any]], None],
    *,
    connect_timeout: float = 5.0,
) -> None:
    """Open a streaming RPC against the host and pump items to ``on_line``.

    The wire shape is the one documented at the top of this module: a
    single newline-delimited JSON request, followed by zero or more
    ``:``-prefixed JSON stream items, terminated by an unprefixed
    ``{"done": true}`` line *or* a clean socket close.

    Each ``:``-prefixed line is decoded and handed to ``on_line``. The
    leading ``:`` is stripped before decoding. The function returns
    when:

    * the server emits ``{"done": true}``;
    * the server emits an unprefixed ``{"error": ...}`` (raised as
      :class:`HostRPCError` — server-side dispatch errors land here);
    * the socket closes cleanly with no terminator (treated as a clean
      end-of-stream);
    * a ``KeyboardInterrupt`` propagates from ``on_line`` (the socket
      is closed cleanly and the exception re-raises).

    The caller is responsible for catching ``KeyboardInterrupt`` if it
    wants to swallow Ctrl-C; the function itself just guarantees a
    clean disconnect on the way out.

    ``connect_timeout`` only governs the initial socket connect — the
    streaming socket itself runs in blocking mode so the client can
    park on ``recv`` between heartbeats without spurious timeouts.
    """
    sock_path = host_socket_path(waves_root)
    if not sock_path.exists():
        raise HostRPCError(f"no host socket at {sock_path}")

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(connect_timeout)
    try:
        s.connect(str(sock_path))
    except (OSError, socket.timeout) as e:
        s.close()
        raise HostRPCError(f"rpc transport error: {e}") from e

    # After the connect handshake, drop back to blocking I/O so the
    # client can sit on heartbeats without raising socket.timeout.
    s.settimeout(None)

    try:
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    except OSError as e:
        s.close()
        raise HostRPCError(f"rpc transport error: {e}") from e

    buf = bytearray()
    try:
        while True:
            try:
                chunk = s.recv(4096)
            except KeyboardInterrupt:
                # Clean disconnect on Ctrl-C; surface to the caller.
                raise
            except OSError as e:
                raise HostRPCError(f"rpc transport error: {e}") from e
            if not chunk:
                # Server closed without a terminator. Treat as clean EOF.
                # Drain any partial line in the buffer first.
                if buf:
                    _process_stream_buffer(buf, on_line)
                return
            buf.extend(chunk)
            done = _process_stream_buffer(buf, on_line)
            if done:
                return
    finally:
        try:
            s.close()
        except OSError:
            pass


def _process_stream_buffer(
    buf: bytearray,
    on_line: Callable[[dict[str, Any]], None],
) -> bool:
    """Pull complete lines out of ``buf``; dispatch each to ``on_line``.

    Returns ``True`` when a terminal frame has been seen — either the
    sentinel ``{"done": true}`` or an unprefixed ``{"error": ...}``
    (which raises :class:`HostRPCError` instead of just signalling
    done). The caller should stop reading on ``True``.

    The function mutates ``buf`` in place: complete lines are removed,
    a trailing partial line stays for the next ``recv`` chunk. ``:``-
    prefixed lines are streamed to ``on_line`` after stripping the
    prefix; unprefixed lines are interpreted as terminal control
    frames.
    """
    while True:
        nl = buf.find(b"\n")
        if nl < 0:
            return False
        raw = bytes(buf[:nl])
        del buf[: nl + 1]
        if not raw:
            continue
        if raw.startswith(b":"):
            try:
                payload = json.loads(raw[1:].decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise HostRPCError(f"rpc decode error: {e}") from e
            on_line(payload)
            continue
        # Unprefixed: terminal frame.
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise HostRPCError(f"rpc decode error: {e}") from e
        if isinstance(payload, dict) and payload.get("done") is True:
            return True
        if isinstance(payload, dict) and "error" in payload:
            raise HostRPCError(str(payload["error"]))
        # Some other unprefixed frame — treat as end of stream so we
        # don't hang waiting for more.
        return True
