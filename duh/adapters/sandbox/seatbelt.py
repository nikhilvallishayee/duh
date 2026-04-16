"""macOS Seatbelt (sandbox-exec) adapter.

Generates Apple Sandbox Profile Language (.sb) files from a SandboxPolicy.
The generated profile is used with: sandbox-exec -f profile.sb bash -c "command"

Profile language reference:
    https://reverse.put.as/wp-content/uploads/2011/09/Apple-Sandbox-Guide-v1.0.pdf

The generated profile follows a default-deny model:
    1. Deny everything by default
    2. Allow file reads ONLY for an explicit set of paths (project root + cwd
       + macOS temp dirs + system Python/shared libs needed for subprocess)
    3. Allow file writes ONLY to specified paths + /tmp + ~/.duh
    4. Allow or deny network based on policy
    5. Allow process execution (fork/exec needed for bash -c)

Historically this profile contained ``(allow file-read*)`` which granted
unrestricted read of the entire filesystem (SEC-MEDIUM-4).  That has been
replaced with explicit ``(allow file-read* (subpath ...))`` rules so that
sandboxed commands cannot exfiltrate arbitrary files from the user's home
directory.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

from duh.adapters.sandbox.policy import SandboxPolicy, deduplicate_paths


def _home_duh_path() -> str:
    """Return the expanded path to ~/.duh."""
    return str(Path.home() / ".duh")


def _default_read_paths() -> list[str]:
    """Compute the minimum read-path set required for a working shell.

    Includes:

    * /usr, /bin, /sbin, /System -- system binaries and frameworks
    * /Library -- shared frameworks (e.g. Python.framework)
    * /opt/homebrew, /usr/local -- Homebrew prefixes
    * /private/etc, /private/var/db -- name resolution, locale data
    * /tmp, /private/tmp, /private/var/tmp, /var/folders -- temp dirs
    * sys.prefix / sys.base_prefix -- the active Python install
    * sysconfig stdlib and platstdlib -- Python's standard library
    * /dev/null, /dev/urandom (literal) -- common device reads
    """
    paths: list[str] = [
        "/usr",
        "/bin",
        "/sbin",
        "/System",
        "/Library",
        "/opt",
        "/private/etc",
        "/private/var/db",
        "/private/var/folders",
        "/var/folders",
        "/tmp",
        "/private/tmp",
        "/private/var/tmp",
    ]
    # Python install + stdlib (covers virtualenv-Python's resolved interpreter)
    for attr in ("prefix", "base_prefix", "exec_prefix", "base_exec_prefix"):
        val = getattr(sys, attr, None)
        if val:
            paths.append(val)
    for key in ("stdlib", "platstdlib", "platlib", "purelib"):
        try:
            p = sysconfig.get_path(key)
        except Exception:  # pragma: no cover - defensive
            p = None
        if p:
            paths.append(p)
    return paths


def generate_profile(
    policy: SandboxPolicy,
    *,
    allowed_read_paths: list[str] | None = None,
) -> str:
    """Generate a Seatbelt .sb profile from a SandboxPolicy.

    Args:
        policy: Platform-independent sandbox policy.
        allowed_read_paths: Optional override for the default read-path set.
            When provided, replaces the built-in defaults entirely (the caller
            is responsible for including any system paths needed for
            subprocess execution).  When ``None`` (default), the union of
            :func:`_default_read_paths` and ``policy.readable_paths`` is used.

    Returns the profile as a string ready to write to a file.
    """
    lines: list[str] = []

    # Header
    lines.append("(version 1)")
    lines.append("")
    lines.append(";; D.U.H. sandbox profile -- auto-generated")
    lines.append(";; Default deny, then selective allow")
    lines.append("")

    # Default deny
    lines.append("(deny default)")
    lines.append("")

    # Read access (explicit allow-list — no global file-read*).
    if allowed_read_paths is None:
        read_paths = _default_read_paths() + list(policy.readable_paths)
    else:
        read_paths = list(allowed_read_paths)
    # Always include the user-cwd if the project hasn't supplied one.
    cwd = os.getcwd()
    if cwd not in read_paths:
        read_paths.append(cwd)
    unique_read_paths = deduplicate_paths(read_paths)

    lines.append(";; File read access (explicit allow-list, not global)")
    lines.append("(allow file-read*")
    for rp in unique_read_paths:
        safe_rp = rp.replace("\\", "\\\\").replace('"', '\\"')
        if safe_rp.startswith("/dev/"):
            lines.append(f'    (literal "{safe_rp}")')
        else:
            lines.append(f'    (subpath "{safe_rp}")')
    lines.append(")")
    # A handful of /dev nodes always need to be readable for normal POSIX I/O.
    lines.append('(allow file-read* (literal "/dev/null"))')
    lines.append('(allow file-read* (literal "/dev/urandom"))')
    lines.append('(allow file-read* (literal "/dev/random"))')
    lines.append('(allow file-read-metadata)')
    lines.append("")

    # Process execution (required for bash -c)
    # Only allow exec and fork — NOT the process* wildcard which grants
    # process-info, process-codesign, and other unnecessary capabilities.
    lines.append(";; Process execution")
    lines.append("(allow process-exec)")
    lines.append("(allow process-fork)")
    lines.append("")

    # Signals, sysctl (needed for normal operation)
    lines.append(";; Basic system operations")
    lines.append("(allow signal)")
    lines.append("(allow sysctl-read)")
    lines.append("(allow mach-lookup)")
    lines.append("(allow ipc-posix-shm-read*)")
    lines.append("")

    # File writes: always include /tmp and ~/.duh
    always_writable = [
        "/tmp",
        "/private/tmp",
        "/private/var/tmp",
        "/dev/null",
        "/dev/tty",
        _home_duh_path(),
    ]

    write_paths = list(policy.writable_paths) + always_writable
    unique_write_paths = deduplicate_paths(write_paths)

    if unique_write_paths:
        lines.append(";; File write access (restricted to specific paths)")
        lines.append("(allow file-write*")
        for wp in unique_write_paths:
            # Escape quotes and backslashes to prevent profile injection
            safe_wp = wp.replace("\\", "\\\\").replace('"', '\\"')
            if safe_wp.startswith("/dev/"):
                lines.append(f'    (literal "{safe_wp}")')
            else:
                lines.append(f'    (subpath "{safe_wp}")')
        lines.append(")")
    else:  # pragma: no cover - always_writable keeps this branch unreachable
        lines.append("(deny file-write*)")

    lines.append("")

    # policy.readable_paths is already folded into the explicit
    # file-read* allow-list above; no extra rule needed here.
    lines.append("")

    # Network
    if policy.network_allowed:
        lines.append(";; Network access: allowed")
        lines.append("(allow network*)")
    else:
        lines.append(";; Network access: denied")
        lines.append("(deny network*)")

    lines.append("")

    return "\n".join(lines)
