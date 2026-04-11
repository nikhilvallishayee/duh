"""Linux Landlock adapter -- filesystem sandboxing via kernel syscalls.

Landlock is a Linux security module (5.13+) that lets unprivileged
processes restrict their own filesystem access. We use ctypes to call
the landlock_create_ruleset, landlock_add_rule, and landlock_restrict_self
syscalls before exec-ing the target command.

Since we can't apply Landlock to an already-running subprocess from the
parent process, we generate a small Python wrapper script that:
    1. Creates a Landlock ruleset
    2. Adds rules for allowed paths
    3. Restricts itself
    4. exec-s the actual command

If Landlock is not available (kernel < 5.13), we log a warning and skip.

Reference:
    https://docs.kernel.org/userspace-api/landlock.html
"""

from __future__ import annotations

import json
import logging
import os
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from duh.adapters.sandbox.policy import SandboxPolicy, deduplicate_paths

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Landlock access flags (ABI v1, kernel 5.13+)
# ---------------------------------------------------------------------------

LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12

# Combined masks
_READ_MASK = LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR
_WRITE_MASK = (
    LANDLOCK_ACCESS_FS_WRITE_FILE
    | LANDLOCK_ACCESS_FS_REMOVE_DIR
    | LANDLOCK_ACCESS_FS_REMOVE_FILE
    | LANDLOCK_ACCESS_FS_MAKE_DIR
    | LANDLOCK_ACCESS_FS_MAKE_REG
    | LANDLOCK_ACCESS_FS_MAKE_SYM
)
_EXEC_MASK = LANDLOCK_ACCESS_FS_EXECUTE
_ALL_MASK = _READ_MASK | _WRITE_MASK | _EXEC_MASK

# Syscall numbers (x86_64)
_SYS_landlock_create_ruleset = 444
_SYS_landlock_add_rule = 445
_SYS_landlock_restrict_self = 446

# Rule types
_LANDLOCK_RULE_PATH_BENEATH = 1


# ---------------------------------------------------------------------------
# Ruleset dataclass
# ---------------------------------------------------------------------------

@dataclass
class LandlockRuleset:
    """Describes the Landlock rules to apply before executing a command."""
    read_paths: list[str] = field(default_factory=list)
    write_paths: list[str] = field(default_factory=list)
    exec_paths: list[str] = field(default_factory=list)
    global_read: bool = True
    network_allowed: bool = True


def build_ruleset(policy: SandboxPolicy) -> LandlockRuleset:
    """Build a LandlockRuleset from a SandboxPolicy.

    Always includes /tmp and ~/.duh as writable.
    Global read is always enabled (bash needs to read many paths).
    """
    home_duh = str(Path.home() / ".duh")
    always_writable = ["/tmp", "/var/tmp", home_duh]

    write_paths = list(policy.writable_paths) + always_writable
    unique = deduplicate_paths(write_paths)

    return LandlockRuleset(
        read_paths=list(policy.readable_paths),
        write_paths=unique,
        exec_paths=["/usr", "/bin", "/sbin", "/nix", "/opt"],
        global_read=True,
        network_allowed=policy.network_allowed,
    )


# ---------------------------------------------------------------------------
# Wrapper script generation
# ---------------------------------------------------------------------------

_LANDLOCK_WRAPPER_TEMPLATE = textwrap.dedent("""\
    import ctypes
    import ctypes.util
    import json
    import os
    import struct
    import sys

    # Landlock syscall numbers (x86_64)
    SYS_landlock_create_ruleset = 444
    SYS_landlock_add_rule = 445
    SYS_landlock_restrict_self = 446
    LANDLOCK_RULE_PATH_BENEATH = 1

    def main():
        config = json.loads(sys.argv[1])
        command = sys.argv[2]

        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

        # handled_access_fs: all filesystem operations
        handled = config["handled_access_fs"]
        attr = struct.pack("Q", handled)

        # Create ruleset
        ruleset_fd = libc.syscall(SYS_landlock_create_ruleset, attr, len(attr), 0)
        if ruleset_fd < 0:
            errno = ctypes.get_errno()
            if errno == 38:  # ENOSYS
                print("SANDBOX_UNAVAILABLE", file=sys.stderr)
                sys.exit(198)  # Fail-closed: do NOT run unsandboxed
            raise OSError(f"landlock_create_ruleset failed: errno {errno}")

        # Add rules for each path
        for rule in config["rules"]:
            path = rule["path"].encode("utf-8")
            access = rule["access"]
            try:
                fd = os.open(rule["path"], os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue  # Skip paths that don't exist
            # struct landlock_path_beneath_attr { __u64 allowed_access; __s32 parent_fd; }
            path_attr = struct.pack("Qi", access, fd)
            # Pad to expected size (add 4 bytes padding after the int)
            path_attr = struct.pack("Qi4x", access, fd)
            ret = libc.syscall(SYS_landlock_add_rule, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH, path_attr, 0)
            os.close(fd)
            if ret < 0:
                pass  # Best effort -- some paths may fail

        # Restrict self
        ret = libc.syscall(SYS_landlock_restrict_self, ruleset_fd, 0)
        os.close(ruleset_fd)
        if ret < 0:
            print("SANDBOX_UNAVAILABLE", file=sys.stderr)
            sys.exit(198)  # Fail-closed: do NOT run unsandboxed

        os.execvp("bash", ["bash", "-c", command])

    main()
""")


def build_landlock_argv(
    command: str,
    policy: SandboxPolicy,
) -> tuple[list[str], dict[str, str] | None]:
    """Build an argv list that applies Landlock before executing the command.

    Returns (argv, env) where argv runs a Python wrapper that applies
    Landlock rules then exec-s the command.
    """
    ruleset = build_ruleset(policy)

    # Build the config dict for the wrapper script
    read_access = _READ_MASK | _EXEC_MASK
    write_access = _WRITE_MASK | _READ_MASK | _EXEC_MASK

    rules: list[dict[str, Any]] = []

    # Global read: add root with read+exec
    if ruleset.global_read:
        rules.append({"path": "/", "access": read_access})

    # Write paths
    for wp in ruleset.write_paths:
        rules.append({"path": wp, "access": write_access})

    # Exec paths
    for ep in ruleset.exec_paths:
        rules.append({"path": ep, "access": read_access | _EXEC_MASK})

    config = {
        "handled_access_fs": _ALL_MASK,
        "rules": rules,
    }

    config_json = json.dumps(config)

    # Use sys.executable to run the wrapper script inline
    python = sys.executable or "python3"
    argv = [python, "-c", _LANDLOCK_WRAPPER_TEMPLATE, config_json, command]

    return argv, None
