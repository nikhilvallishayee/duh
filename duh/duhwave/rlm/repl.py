"""Sandboxed Python REPL substrate for the RLM context engine (ADR-028).

Wire encoding: messages are JSON. ``str`` values pass through; ``bytes``
values are wrapped as ``{"_b64": "<base64>"}`` and rehydrated on the
bootstrap side.

A long-running ``python3 -I`` subprocess holds the conversation's bulky
inputs as named variables. The parent communicates over stdin/stdout
with a minimal JSON wire protocol::

    {"op": "exec", "code": "..."}            -> {"ok": true, "stdout": "..."}
    {"op": "eval", "code": "x"}              -> {"ok": true, "result": "..."}
    {"op": "bind", "name": "x", "value": ...}-> {"ok": true, "handle": {...}}
    {"op": "shutdown"}                       -> {"ok": true}

Sandboxing constraints:

- isolated mode (``-I``) — no user site-packages, no PYTHONPATH
- no network: ``socket`` monkey-patched to raise on connect
- no shell: ``os.system`` and ``subprocess`` removed
- memory ceiling via ``resource.RLIMIT_AS`` (default 512 MB)
- curated stdlib subset

This module exposes the host-side controller. The REPL bootstrap
script lives at ``duh/duhwave/rlm/_bootstrap.py``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
import time


def _b64encode_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from duh.duhwave.rlm.handles import Handle, HandleStore


#: Type of a host-attached recurse runner. Receives
#: ``(handle, instruction, depth, lineage)`` and returns the child
#: model call's final text response.
RecurseRunner = Callable[[str, str, int, tuple[str, ...]], Awaitable[str]]


_BOOTSTRAP_PATH = Path(__file__).parent / "_bootstrap.py"


class RLMReplError(RuntimeError):
    """The REPL subprocess raised, crashed, or violated the sandbox."""


@dataclass(slots=True)
class _ReplResponse:
    ok: bool
    payload: dict[str, Any]


class RLMRepl:
    """Host-side controller for a sandboxed Python REPL subprocess.

    Lifecycle::

        repl = RLMRepl()
        await repl.start()
        await repl.bind("codebase", load_directory("/path"))
        result = await repl.peek("codebase", start=0, end=4096)
        await repl.shutdown()
    """

    DEFAULT_MEM_MB = 512
    DEFAULT_OP_TIMEOUT = 30.0

    def __init__(
        self,
        *,
        mem_mb: int = DEFAULT_MEM_MB,
        op_timeout: float = DEFAULT_OP_TIMEOUT,
    ) -> None:
        self._mem_mb = mem_mb
        self._op_timeout = op_timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._handles = HandleStore()
        self._lock = asyncio.Lock()
        self._recurse_runner: RecurseRunner | None = None
        self._recurse_seq: int = 0
        # Defense-in-depth (ADR-028 §"Recursion bounds"): the wire-side
        # validator in ``_bootstrap.op_recurse_validate`` only sees the
        # ``lineage`` array the host passes it. A buggy or malicious
        # runner could pass an empty lineage on every call and induce
        # infinite recursion. Track the active call stack here so the
        # host enforces cycle detection independently of the runner's
        # cooperation.
        self._active_recursions: set[str] = set()

    @property
    def handles(self) -> HandleStore:
        return self._handles

    async def start(self) -> None:
        if self._proc is not None:
            raise RLMReplError("repl already started")
        env = {
            "PYTHONIOENCODING": "utf-8",
            "DUHWAVE_RLM_MEM_MB": str(self._mem_mb),
            "PATH": os.environ.get("PATH", ""),
        }
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(_BOOTSTRAP_PATH),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        # Wait for the ready handshake.
        ready = await self._read_one()
        if ready.payload.get("op") != "ready":
            raise RLMReplError(f"repl bootstrap failed: {ready.payload}")

    async def shutdown(self) -> None:
        if self._proc is None:
            return
        try:
            await self._send({"op": "shutdown"})
        except Exception:
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()
        self._proc = None

    # ---- the five RLM operations -----------------------------------

    async def bind(self, name: str, value: str | bytes) -> Handle:
        """Bind a value to a name in the REPL; return a Handle record.

        ``bytes`` values cross the JSON wire as ``{"_b64": ...}`` and are
        rehydrated to ``bytes`` on the bootstrap side.
        """
        if isinstance(value, bytes):
            sha = hashlib.sha256(value).hexdigest()
            kind = "bytes"
            chars = 0
            lines = 0
            length = len(value)
            wire_value: object = {"_b64": _b64encode_bytes(value)}
        else:
            data = value
            sha = hashlib.sha256(data.encode()).hexdigest()
            kind = "str"
            chars = len(data)
            lines = data.count("\n") + (1 if data and not data.endswith("\n") else 0)
            length = len(data.encode())
            wire_value = value
        resp = await self._send({"op": "bind", "name": name, "value": wire_value})
        if not resp.ok:
            raise RLMReplError(resp.payload.get("error", "bind failed"))
        handle = Handle(
            name=name,
            kind=kind,
            total_chars=chars,
            total_lines=lines,
            total_bytes=length,
            sha256=sha,
            bound_at=time.time(),
            bound_by="user",
        )
        self._handles.bind(handle)
        return handle

    async def peek(
        self,
        handle: str,
        *,
        start: int = 0,
        end: int = 4096,
        mode: str = "chars",
    ) -> str:
        """Return a slice of a bound variable. ADR-028 Peek tool."""
        h = self._handles.get(handle)
        if h is None:
            raise RLMReplError(f"unknown handle: {handle}")
        resp = await self._send(
            {"op": "peek", "name": handle, "start": start, "end": end, "mode": mode}
        )
        if not resp.ok:
            raise RLMReplError(resp.payload.get("error", "peek failed"))
        return resp.payload["slice"]

    async def search(
        self,
        handle: str,
        pattern: str,
        *,
        max_hits: int = 50,
    ) -> list[dict[str, Any]]:
        """Regex-search a bound variable. ADR-028 Search tool."""
        if self._handles.get(handle) is None:
            raise RLMReplError(f"unknown handle: {handle}")
        resp = await self._send(
            {
                "op": "search",
                "name": handle,
                "pattern": pattern,
                "max_hits": max_hits,
            }
        )
        if not resp.ok:
            raise RLMReplError(resp.payload.get("error", "search failed"))
        return resp.payload["hits"]

    async def slice(
        self,
        source: str,
        start: int,
        end: int,
        bind_as: str,
    ) -> Handle:
        """Bind a sub-region as a new handle. ADR-028 Slice tool."""
        h = self._handles.get(source)
        if h is None:
            raise RLMReplError(f"unknown handle: {source}")
        resp = await self._send(
            {
                "op": "slice",
                "source": source,
                "start": start,
                "end": end,
                "bind_as": bind_as,
            }
        )
        if not resp.ok:
            raise RLMReplError(resp.payload.get("error", "slice failed"))
        meta = resp.payload["handle"]
        new_handle = Handle(
            name=bind_as,
            kind=h.kind,
            total_chars=meta.get("total_chars", end - start),
            total_lines=meta.get("total_lines", 0),
            total_bytes=meta.get("total_bytes", end - start),
            sha256=meta.get("sha256", ""),
            bound_at=time.time(),
            bound_by=f"slice:{source}",
        )
        self._handles.bind(new_handle)
        return new_handle

    async def exec_code(self, code: str) -> str:
        """Run arbitrary Python in the REPL — escape hatch.

        Used by the agent's freeform `Code` action when the five tools
        aren't enough. Sandbox still applies; if the model writes
        ``os.system(...)`` it raises in the subprocess.
        """
        resp = await self._send({"op": "exec", "code": code})
        if not resp.ok:
            raise RLMReplError(resp.payload.get("error", "exec failed"))
        return resp.payload.get("stdout", "")

    # ---- Recurse (ADR-028 §"Five tools") ---------------------------

    def attach_recurse_runner(self, runner: RecurseRunner) -> None:
        """Attach the host-side runner that drives a child model call.

        ADR-028 splits Recurse into validation (bootstrap, sandboxed)
        and execution (host, can hit the model). The runner is the
        execution half. It receives ``(handle, instruction, depth,
        lineage)`` and must return the child's final synthesis as a
        string. Implementations live in the agent loop, not here.
        """
        self._recurse_runner = runner

    async def recurse(
        self,
        handle: str,
        *,
        instruction: str,
        depth: int = 0,
        lineage: tuple[str, ...] = (),
        max_depth: int = 4,
    ) -> str:
        """Spawn a child model call against a slice; return its synthesis.

        Validates the recursion bound + cycle via the REPL bootstrap,
        then invokes the host's attached :class:`RecurseRunner`. The
        result is bound back as a new handle named
        ``<handle>__recurse_<seq>`` so the parent can address the
        child's output going forward.

        Per ADR-028 §"Recursion bounds": soft cap 4, cycle detection
        rejects when ``handle`` is in the caller's lineage.

        Defense-in-depth: the wire validator
        (:func:`_bootstrap.op_recurse_validate`) only sees the lineage
        array we pass it, so a buggy or malicious runner could thread
        an empty lineage on every recursive call and bypass the cycle
        check. To close that gap, the host independently enforces both
        invariants *before* the wire validate call:

        1. ``handle`` already on ``self._active_recursions`` → reject
           as a cycle, regardless of the lineage tuple's contents.
        2. ``depth >= max_depth`` → reject as depth-cap violation,
           regardless of whether the bootstrap got the same numbers.

        The ``lineage`` tuple still flows through for observability and
        as a second-line check inside the sandboxed bootstrap.
        """
        # 1a. Host-side cycle check (defense-in-depth). The bootstrap's
        #     lineage-based check is correct *if* the caller threads
        #     lineage faithfully. We don't trust that — track the
        #     handles currently in our own call stack here.
        if handle in self._active_recursions:
            raise RLMReplError(
                f"cycle detected: handle {handle} already on the stack"
            )

        # 1b. Host-side depth cap (defense-in-depth). Mirrors the
        #     bootstrap's ``depth >= max_depth`` check so a bypassed
        #     bootstrap can't turn into runaway recursion.
        if depth >= max_depth:
            raise RLMReplError(
                f"max recursion depth {max_depth} exceeded"
            )

        # 2. Ask the bootstrap to validate. The bootstrap holds the
        #    handle store + applies the depth + cycle checks. It does
        #    *not* invoke the model — that's why the validation step
        #    can stay inside the sandboxed subprocess.
        self._active_recursions.add(handle)
        try:
            resp = await self._send(
                {
                    "op": "recurse_validate",
                    "handle": handle,
                    "depth": depth,
                    "max_depth": max_depth,
                    "lineage": list(lineage),
                }
            )
            if not resp.ok:
                raise RLMReplError(resp.payload.get("error", "recurse failed"))

            # 3. Validation passed. Now invoke the host-attached runner.
            #    This is the half that can talk to the model. If no
            #    runner is attached this is a programming error, not a
            #    sandbox error — surface it clearly.
            if self._recurse_runner is None:
                raise RLMReplError(
                    "no recurse runner attached: call "
                    "RLMRepl.attach_recurse_runner(...) before recurse()"
                )

            new_depth = int(resp.payload.get("depth", depth + 1))
            new_lineage = lineage + (handle,)
            result = await self._recurse_runner(
                handle, instruction, new_depth, new_lineage
            )

            # 4. Bind the child's output as a new handle so subsequent
            #    operations can address it without re-running the recursion.
            new_name = f"{handle}__recurse_{self._recurse_seq}"
            self._recurse_seq += 1
            await self.bind(new_name, result)
            return result
        finally:
            # Always pop, even on exceptions raised in the runner — a
            # leaked entry would falsely flag legitimate later calls
            # against the same handle as cycles.
            self._active_recursions.discard(handle)

    # ---- snapshot / restore (ADR-058 resume parity) ----------------

    async def snapshot(self, path: Path) -> None:
        """Pickle the REPL's namespace to ``path``. Used at turn boundaries."""
        resp = await self._send({"op": "snapshot", "path": str(path)})
        if not resp.ok:
            raise RLMReplError(resp.payload.get("error", "snapshot failed"))

    async def restore(self, path: Path) -> None:
        """Restore from a prior snapshot. Used on session resume."""
        resp = await self._send({"op": "restore", "path": str(path)})
        if not resp.ok:
            raise RLMReplError(resp.payload.get("error", "restore failed"))
        # Re-hydrate the metadata store from the bootstrap's report.
        for h_dict in resp.payload.get("handles", []):
            self._handles.rebind(Handle(**h_dict))

    # ---- wire protocol ---------------------------------------------

    async def _send(self, msg: dict[str, Any]) -> _ReplResponse:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise RLMReplError("repl not started")
        async with self._lock:
            line = (json.dumps(msg) + "\n").encode("utf-8")
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
            try:
                return await asyncio.wait_for(self._read_one(), self._op_timeout)
            except asyncio.TimeoutError as e:
                raise RLMReplError(
                    f"repl op timed out after {self._op_timeout}s: {msg.get('op')}"
                ) from e

    async def _read_one(self) -> _ReplResponse:
        assert self._proc is not None and self._proc.stdout is not None
        line = await self._proc.stdout.readline()
        if not line:
            raise RLMReplError("repl subprocess closed unexpectedly")
        try:
            payload = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise RLMReplError(f"repl returned non-JSON: {line!r}") from e
        return _ReplResponse(ok=bool(payload.get("ok", False)), payload=payload)
