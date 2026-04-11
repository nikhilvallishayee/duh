"""Sandbox policy abstraction -- platform-independent confinement rules.

A SandboxPolicy describes WHAT is allowed (writable paths, readable paths,
network access). The SandboxCommand builder translates that into HOW to
enforce it on the current platform (Seatbelt on macOS, Landlock on Linux).

    policy = SandboxPolicy(
        writable_paths=[cwd, "/tmp", "~/.duh"],
        network_allowed=False,
    )
    cmd = SandboxCommand.build("npm install", policy, detect_sandbox_type())
    # cmd.argv is now ["sandbox-exec", "-f", "/tmp/duh_xxx.sb", "bash", "-c", "npm install"]
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def deduplicate_paths(paths: list[str]) -> list[str]:
    """Deduplicate a list of paths while preserving order.

    Used by both Seatbelt and Landlock adapters when merging
    policy paths with always-writable defaults.
    """
    seen: set[str] = set()
    result: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


class SandboxType(Enum):
    """Supported sandboxing backends."""
    NONE = "none"
    MACOS_SEATBELT = "macos_seatbelt"
    LINUX_LANDLOCK = "linux_landlock"


@dataclass
class SandboxPolicy:
    """Platform-independent sandbox policy.

    Describes what the confined process is allowed to do.
    The SandboxCommand builder translates this into platform-native rules.
    """
    writable_paths: list[str] = field(default_factory=list)
    readable_paths: list[str] = field(default_factory=list)
    network_allowed: bool = True
    env_vars: dict[str, str] = field(default_factory=dict)


def _landlock_available() -> bool:
    """Check if Landlock is available on this Linux kernel."""
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        # landlock_create_ruleset syscall number on x86_64
        # If the syscall exists and returns a valid fd or ENOSYS, we know.
        import struct
        # Try ABI v1: struct landlock_ruleset_attr { __u64 handled_access_fs; }
        attr = struct.pack("Q", 0)  # empty access mask
        SYS_landlock_create_ruleset = 444  # x86_64
        result = libc.syscall(SYS_landlock_create_ruleset, attr, len(attr), 0)
        if result >= 0:
            os.close(result)
            return True
        # Check errno -- ENOSYS means not available, EINVAL means available
        # but bad args (which is fine, it exists)
        errno = ctypes.get_errno()
        return errno != 38  # ENOSYS = 38
    except Exception:
        return False


def detect_sandbox_type() -> SandboxType:
    """Auto-detect the best available sandbox for this platform.

    Returns SandboxType.NONE if no sandbox is available.
    """
    if sys.platform == "darwin":
        if shutil.which("sandbox-exec"):
            return SandboxType.MACOS_SEATBELT
        return SandboxType.NONE

    if sys.platform == "linux":
        if _landlock_available():
            return SandboxType.LINUX_LANDLOCK
        return SandboxType.NONE

    # Windows, FreeBSD, etc. -- no sandbox support yet
    return SandboxType.NONE


@dataclass
class SandboxCommand:
    """A command wrapped with sandbox enforcement.

    Use SandboxCommand.build() to create one from a policy.
    The .argv list is ready to pass to asyncio.create_subprocess_exec.
    """
    command: str
    argv: list[str]
    profile_path: str | None = None
    env: dict[str, str] | None = None

    @classmethod
    def build(
        cls,
        command: str,
        policy: SandboxPolicy,
        sandbox_type: SandboxType,
    ) -> SandboxCommand:
        """Build a sandboxed command from a policy.

        For SandboxType.NONE, returns the command as-is.
        For MACOS_SEATBELT, generates an .sb profile and wraps with sandbox-exec.
        For LINUX_LANDLOCK, generates a landlock wrapper script.
        """
        if sandbox_type == SandboxType.NONE:
            return cls(
                command=command,
                argv=["bash", "-c", command],
                profile_path=None,
            )

        if sandbox_type == SandboxType.MACOS_SEATBELT:
            from duh.adapters.sandbox.seatbelt import generate_profile
            profile_content = generate_profile(policy)
            # Write profile to a temp file
            fd, profile_path = tempfile.mkstemp(suffix=".sb", prefix="duh_")
            try:
                os.write(fd, profile_content.encode("utf-8"))
            finally:
                os.close(fd)
            return cls(
                command=command,
                argv=["sandbox-exec", "-f", profile_path, "bash", "-c", command],
                profile_path=profile_path,
            )

        if sandbox_type == SandboxType.LINUX_LANDLOCK:
            from duh.adapters.sandbox.landlock import build_landlock_argv
            argv, env = build_landlock_argv(command, policy)
            return cls(
                command=command,
                argv=argv,
                profile_path=None,
                env=env,
            )

        return cls(
            command=command,
            argv=["bash", "-c", command],
            profile_path=None,
        )

    def cleanup(self) -> None:
        """Remove temporary profile files if any."""
        if self.profile_path:
            try:
                os.unlink(self.profile_path)
            except OSError:
                pass
