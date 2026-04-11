"""macOS Seatbelt (sandbox-exec) adapter.

Generates Apple Sandbox Profile Language (.sb) files from a SandboxPolicy.
The generated profile is used with: sandbox-exec -f profile.sb bash -c "command"

Profile language reference:
    https://reverse.put.as/wp-content/uploads/2011/09/Apple-Sandbox-Guide-v1.0.pdf

The generated profile follows a default-deny model:
    1. Deny everything by default
    2. Allow file reads globally (needed for bash, libraries, etc.)
    3. Allow file writes ONLY to specified paths + /tmp + ~/.duh
    4. Allow or deny network based on policy
    5. Allow process execution (fork/exec needed for bash -c)
"""

from __future__ import annotations

import os
from pathlib import Path

from duh.adapters.sandbox.policy import SandboxPolicy


def _home_duh_path() -> str:
    """Return the expanded path to ~/.duh."""
    return str(Path.home() / ".duh")


def generate_profile(policy: SandboxPolicy) -> str:
    """Generate a Seatbelt .sb profile from a SandboxPolicy.

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

    # Always allow: reading files (bash, shared libs, etc.)
    lines.append(";; Global read access (required for shell execution)")
    lines.append("(allow file-read*)")
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
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_write_paths: list[str] = []
    for p in write_paths:
        if p not in seen:
            seen.add(p)
            unique_write_paths.append(p)

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
    else:
        lines.append("(deny file-write*)")

    lines.append("")

    # Readable paths (additional, beyond global read-all)
    if policy.readable_paths:
        lines.append(";; Additional readable paths (explicitly listed)")
        for rp in policy.readable_paths:
            lines.append(f';; readable: (subpath "{rp}")')
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
