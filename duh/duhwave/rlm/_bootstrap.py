"""REPL subprocess bootstrap. Runs in isolated mode (``python3 -I``).

Reads JSON ops from stdin, writes JSON responses to stdout. Stays
inside a curated sandbox.

Wire ops::

    {"op": "exec",      "code": str}                  -> {"ok", "stdout"}
    {"op": "eval",      "code": str}                  -> {"ok", "result"}
    {"op": "bind",      "name": str, "value": any}    -> {"ok", "handle"}
    {"op": "peek",      "name": str, "start": int, "end": int, "mode": str}
    {"op": "search",    "name": str, "pattern": str, "max_hits": int}
    {"op": "slice",     "source": str, "start", "end", "bind_as": str}
    {"op": "snapshot",  "path": str}
    {"op": "restore",   "path": str}
    {"op": "recurse_validate", "handle": str, "depth": int,
                               "max_depth": int, "lineage": [str, ...]}
                                                      -> {"ok", "ready", "handle", "depth"}
    {"op": "shutdown"}

Note on the ``recurse_validate`` op (ADR-028 Recurse semantics):

    The bootstrap is a *pure validator*. The actual recursion — calling
    back into a model with the slice as input — happens in the host
    process; the bootstrap cannot make network/model calls and that
    boundary is intentional. The bootstrap only checks (a) the handle
    exists, (b) ``depth <= max_depth``, (c) the handle is not in the
    caller's lineage (cycle detection). On success it returns a
    ``ready`` payload with the incremented depth. The host's
    :class:`RLMRepl.recurse` reads that payload, then invokes a
    host-attached runner.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import pickle
import re
import resource
import sys
import traceback
from contextlib import redirect_stdout
from typing import Any


# ---- module constants ------------------------------------------------

#: ADR-028 §"Recursion bounds" soft cap. Hard cap is 8 (enforced at
#: the policy layer; the bootstrap honours whatever ``max_depth`` the
#: caller passes, capped to this value as the default).
RECURSE_MAX_DEPTH = 4


# ---- sandbox boot ----------------------------------------------------

def _apply_sandbox() -> None:
    mem_mb = int(os.environ.get("DUHWAVE_RLM_MEM_MB", "512"))
    try:
        resource.setrlimit(
            resource.RLIMIT_AS,
            (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024),
        )
    except (ValueError, OSError):
        # Some platforms (macOS) restrict RLIMIT_AS adjustments; best effort.
        pass

    # Ban network. Imports happen lazily, so wrap socket on-import.
    import socket as _socket

    def _no_connect(*_a, **_kw):  # pragma: no cover - guard
        raise PermissionError("network access disabled in duhwave RLM sandbox")

    _socket.socket.connect = _no_connect  # type: ignore[method-assign]
    _socket.socket.connect_ex = _no_connect  # type: ignore[method-assign]
    _socket.create_connection = _no_connect  # type: ignore[assignment]

    # Ban shell.
    os.system = _no_connect  # type: ignore[assignment]
    if "subprocess" in sys.modules:  # pragma: no cover
        del sys.modules["subprocess"]
    sys.modules["subprocess"] = None  # type: ignore[assignment]


_apply_sandbox()


# ---- the REPL namespace ---------------------------------------------

NS: dict[str, Any] = {
    "__name__": "rlm",
    "__builtins__": __builtins__,
    "re": re,
    "json": json,
}


def _summarise(value: Any) -> dict[str, Any]:
    """Compute the size + sha256 metadata returned by ``bind`` / ``slice``."""
    if isinstance(value, str):
        return {
            "kind": "str",
            "total_chars": len(value),
            "total_lines": value.count("\n") + (1 if value and not value.endswith("\n") else 0),
            "total_bytes": len(value.encode()),
            "sha256": hashlib.sha256(value.encode()).hexdigest(),
        }
    if isinstance(value, bytes):
        return {
            "kind": "bytes",
            "total_chars": 0,
            "total_lines": 0,
            "total_bytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    return {
        "kind": type(value).__name__,
        "total_chars": 0,
        "total_lines": 0,
        "total_bytes": 0,
        "sha256": "",
    }


# ---- op handlers -----------------------------------------------------

def op_exec(msg: dict[str, Any]) -> dict[str, Any]:
    code = msg["code"]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            exec(compile(code, "<rlm>", "exec"), NS)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
    return {"ok": True, "stdout": buf.getvalue()}


def op_eval(msg: dict[str, Any]) -> dict[str, Any]:
    code = msg["code"]
    try:
        result = eval(compile(code, "<rlm>", "eval"), NS)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "result": repr(result)}


def op_bind(msg: dict[str, Any]) -> dict[str, Any]:
    name = msg["name"]
    if not name.isidentifier():
        return {"ok": False, "error": f"invalid handle name: {name!r}"}
    raw = msg["value"]
    # Bytes cross the wire as {"_b64": "<base64>"}.
    if isinstance(raw, dict) and set(raw.keys()) == {"_b64"}:
        import base64
        value: Any = base64.b64decode(raw["_b64"])
    else:
        value = raw
    NS[name] = value
    return {"ok": True, "handle": {"name": name, **_summarise(value)}}


def op_peek(msg: dict[str, Any]) -> dict[str, Any]:
    name = msg["name"]
    if name not in NS:
        return {"ok": False, "error": f"unknown handle: {name}"}
    value = NS[name]
    start = int(msg.get("start", 0))
    end = int(msg.get("end", 4096))
    mode = msg.get("mode", "chars")
    if mode == "chars":
        if isinstance(value, (str, bytes)):
            sliced = value[start:end]
        else:
            return {"ok": False, "error": f"cannot peek non-string handle in 'chars' mode"}
    elif mode == "lines":
        if not isinstance(value, str):
            return {"ok": False, "error": "lines mode requires str handle"}
        lines = value.split("\n")
        sliced = "\n".join(lines[start:end])
    else:
        return {"ok": False, "error": f"unknown mode: {mode}"}
    if isinstance(sliced, bytes):
        sliced = sliced.decode("utf-8", errors="replace")
    return {
        "ok": True,
        "slice": sliced,
        "meta": {"total_chars": len(value) if isinstance(value, str) else 0},
    }


def op_search(msg: dict[str, Any]) -> dict[str, Any]:
    name = msg["name"]
    if name not in NS:
        return {"ok": False, "error": f"unknown handle: {name}"}
    value = NS[name]
    if not isinstance(value, str):
        return {"ok": False, "error": "search requires str handle"}
    try:
        rx = re.compile(msg["pattern"])
    except re.error as e:
        return {"ok": False, "error": f"bad regex: {e}"}
    max_hits = int(msg.get("max_hits", 50))
    hits = []
    for m in rx.finditer(value):
        if len(hits) >= max_hits:
            break
        # Find line number + column.
        prefix = value[: m.start()]
        line = prefix.count("\n") + 1
        col = m.start() - prefix.rfind("\n") - 1 if "\n" in prefix else m.start()
        # Snippet: 40 chars on each side.
        snip_start = max(0, m.start() - 40)
        snip_end = min(len(value), m.end() + 40)
        hits.append(
            {
                "line": line,
                "col": col,
                "snippet": value[snip_start:snip_end],
                "span": [m.start(), m.end()],
            }
        )
    return {"ok": True, "hits": hits}


def op_slice(msg: dict[str, Any]) -> dict[str, Any]:
    source = msg["source"]
    if source not in NS:
        return {"ok": False, "error": f"unknown handle: {source}"}
    value = NS[source]
    if not isinstance(value, (str, bytes)):
        return {"ok": False, "error": "slice requires str/bytes handle"}
    bind_as = msg["bind_as"]
    if not bind_as.isidentifier():
        return {"ok": False, "error": f"invalid bind_as: {bind_as!r}"}
    new_value = value[int(msg["start"]):int(msg["end"])]
    NS[bind_as] = new_value
    return {"ok": True, "handle": {"name": bind_as, **_summarise(new_value)}}


def op_snapshot(msg: dict[str, Any]) -> dict[str, Any]:
    path = msg["path"]
    # Only pickle simple types: skip modules, callables, etc.
    snap = {
        k: v
        for k, v in NS.items()
        if not k.startswith("__")
        and not callable(v)
        and not isinstance(v, type(re))
    }
    try:
        with open(path, "wb") as f:
            pickle.dump(snap, f)
    except Exception as e:
        return {"ok": False, "error": f"snapshot write failed: {e}"}
    return {"ok": True, "count": len(snap)}


def op_restore(msg: dict[str, Any]) -> dict[str, Any]:
    path = msg["path"]
    try:
        with open(path, "rb") as f:
            snap = pickle.load(f)
    except Exception as e:
        return {"ok": False, "error": f"restore failed: {e}"}
    handles = []
    for name, value in snap.items():
        NS[name] = value
        handles.append({"name": name, **_summarise(value), "bound_at": 0.0, "bound_by": "restore"})
    return {"ok": True, "handles": handles}


def op_recurse_validate(msg: dict[str, Any]) -> dict[str, Any]:
    """Validate a Recurse request without making any model call.

    The bootstrap is sandboxed and cannot reach the network or invoke
    a model — that's the host's job. This op exists so the host can
    push the depth + cycle invariants through the same wire boundary
    every other op crosses, keeping the validation logic colocated
    with the handle store.

    Returns ``{"ok": True, "ready": True, "handle": <meta>,
    "depth": depth + 1}`` on success; ``{"ok": False, "error": ...}``
    on any of the three rejection cases.
    """
    handle = msg.get("handle")
    if not isinstance(handle, str) or not handle:
        return {"ok": False, "error": "recurse: missing handle"}
    if handle not in NS:
        return {"ok": False, "error": f"unknown handle: {handle}"}

    try:
        depth = int(msg.get("depth", 0))
        max_depth = int(msg.get("max_depth", RECURSE_MAX_DEPTH))
    except (TypeError, ValueError) as e:
        return {"ok": False, "error": f"recurse: bad depth ints: {e}"}

    if depth >= max_depth:
        return {
            "ok": False,
            "error": f"max recursion depth {max_depth} exceeded",
        }

    raw_lineage = msg.get("lineage", [])
    if not isinstance(raw_lineage, list):
        return {"ok": False, "error": "recurse: lineage must be a list"}
    if handle in raw_lineage:
        return {
            "ok": False,
            "error": f"cycle detected: handle {handle} is in caller's lineage",
        }

    value = NS[handle]
    return {
        "ok": True,
        "ready": True,
        "handle": {"name": handle, **_summarise(value)},
        "depth": depth + 1,
    }


OP_HANDLERS = {
    "exec": op_exec,
    "eval": op_eval,
    "bind": op_bind,
    "peek": op_peek,
    "search": op_search,
    "slice": op_slice,
    "snapshot": op_snapshot,
    "restore": op_restore,
    "recurse_validate": op_recurse_validate,
}


# ---- main loop -------------------------------------------------------

def main() -> None:
    sys.stdout.write(json.dumps({"ok": True, "op": "ready"}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps({"ok": False, "error": f"bad json: {e}"}) + "\n")
            sys.stdout.flush()
            continue
        op = msg.get("op")
        if op == "shutdown":
            sys.stdout.write(json.dumps({"ok": True, "op": "shutdown"}) + "\n")
            sys.stdout.flush()
            return
        handler = OP_HANDLERS.get(op)
        if handler is None:
            resp = {"ok": False, "error": f"unknown op: {op}"}
        else:
            try:
                resp = handler(msg)
            except Exception as e:
                resp = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
