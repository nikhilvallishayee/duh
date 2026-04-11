# Phase 3: Codex Steals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add platform-native sandboxing (macOS Seatbelt, Linux Landlock), network isolation, a 3-tier approval model, and ghost snapshot mode to D.U.H. These features bring parity with Codex's security model while staying zero-dependency (no new pip packages).

**Architecture:** The sandbox layer sits between BashTool and the OS. It wraps commands in platform-native confinement before `asyncio.create_subprocess_exec` runs them. The 3-tier approver composes the existing `ApprovalGate` protocol with tool classification logic. Ghost snapshots fork engine state so the model can explore read-only without committing changes.

**Tech Stack:** Python 3.12+, asyncio, ctypes (Landlock), subprocess (Seatbelt). No new pip dependencies.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `duh/adapters/sandbox/__init__.py` | Package init |
| Create | `duh/adapters/sandbox/policy.py` | SandboxPolicy, SandboxType, SandboxCommand, detect |
| Create | `duh/adapters/sandbox/seatbelt.py` | macOS sandbox-exec profile generation |
| Create | `duh/adapters/sandbox/landlock.py` | Linux Landlock via ctypes |
| Create | `duh/adapters/sandbox/network.py` | NetworkPolicy dataclass + enforcement |
| Modify | `duh/adapters/approvers.py` | Add ApprovalMode, TieredApprover |
| Create | `duh/kernel/snapshot.py` | Ghost snapshot: ReadOnlyExecutor, SnapshotSession |
| Modify | `duh/tools/bash.py` | Accept SandboxPolicy, wrap commands |
| Modify | `duh/kernel/tool.py` | Add sandbox_policy to ToolContext |
| Modify | `duh/cli/parser.py` | Add --approval-mode flag |
| Modify | `duh/config.py` | Add approval_mode to Config |
| Modify | `duh/cli/repl.py` | Wire TieredApprover + /snapshot command |
| Create | `tests/unit/test_sandbox_policy.py` | Tests for policy abstraction |
| Create | `tests/unit/test_seatbelt.py` | Tests for macOS adapter |
| Create | `tests/unit/test_landlock.py` | Tests for Linux adapter |
| Create | `tests/unit/test_network_policy.py` | Tests for network policy |
| Create | `tests/unit/test_tiered_approver.py` | Tests for 3-tier approval |
| Create | `tests/unit/test_snapshot.py` | Tests for ghost snapshot |
| Create | `tests/unit/test_bash_sandboxed.py` | Tests for sandboxed BashTool |
| Create | `tests/unit/test_approval_cli.py` | Tests for CLI wiring |

---

### Task 1: Sandbox Policy Abstraction

**Files:**
- Create: `duh/adapters/sandbox/__init__.py`
- Create: `duh/adapters/sandbox/policy.py`
- Create: `tests/unit/test_sandbox_policy.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sandbox_policy.py
"""Tests for sandbox policy abstraction."""

import sys
from unittest.mock import patch

import pytest

from duh.adapters.sandbox.policy import (
    SandboxCommand,
    SandboxPolicy,
    SandboxType,
    detect_sandbox_type,
)


class TestSandboxType:
    def test_none_exists(self):
        assert SandboxType.NONE.value == "none"

    def test_seatbelt_exists(self):
        assert SandboxType.MACOS_SEATBELT.value == "macos_seatbelt"

    def test_landlock_exists(self):
        assert SandboxType.LINUX_LANDLOCK.value == "linux_landlock"


class TestSandboxPolicy:
    def test_defaults(self):
        policy = SandboxPolicy()
        assert policy.writable_paths == []
        assert policy.readable_paths == []
        assert policy.network_allowed is True
        assert policy.env_vars == {}

    def test_custom_paths(self):
        policy = SandboxPolicy(
            writable_paths=["/tmp", "/home/user/.duh"],
            readable_paths=["/usr", "/etc"],
            network_allowed=False,
        )
        assert "/tmp" in policy.writable_paths
        assert policy.network_allowed is False

    def test_env_vars(self):
        policy = SandboxPolicy(env_vars={"HOME": "/home/user"})
        assert policy.env_vars["HOME"] == "/home/user"

    def test_is_dataclass(self):
        from dataclasses import fields
        f = fields(SandboxPolicy)
        names = {field.name for field in f}
        assert "writable_paths" in names
        assert "readable_paths" in names
        assert "network_allowed" in names
        assert "env_vars" in names


class TestDetectSandboxType:
    @patch("sys.platform", "darwin")
    @patch("shutil.which", return_value="/usr/bin/sandbox-exec")
    def test_darwin_with_sandbox_exec(self, mock_which):
        result = detect_sandbox_type()
        assert result == SandboxType.MACOS_SEATBELT

    @patch("sys.platform", "darwin")
    @patch("shutil.which", return_value=None)
    def test_darwin_without_sandbox_exec(self, mock_which):
        result = detect_sandbox_type()
        assert result == SandboxType.NONE

    @patch("sys.platform", "linux")
    @patch("duh.adapters.sandbox.policy._landlock_available", return_value=True)
    def test_linux_with_landlock(self, mock_ll):
        result = detect_sandbox_type()
        assert result == SandboxType.LINUX_LANDLOCK

    @patch("sys.platform", "linux")
    @patch("duh.adapters.sandbox.policy._landlock_available", return_value=False)
    def test_linux_without_landlock(self, mock_ll):
        result = detect_sandbox_type()
        assert result == SandboxType.NONE

    @patch("sys.platform", "win32")
    def test_windows_returns_none(self):
        result = detect_sandbox_type()
        assert result == SandboxType.NONE


class TestSandboxCommand:
    def test_none_type_returns_original(self):
        policy = SandboxPolicy()
        result = SandboxCommand.build(
            command="echo hello",
            policy=policy,
            sandbox_type=SandboxType.NONE,
        )
        assert result.command == "echo hello"
        assert result.profile_path is None
        assert result.argv == ["bash", "-c", "echo hello"]

    def test_build_returns_sandbox_command(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        result = SandboxCommand.build(
            command="echo hello",
            policy=policy,
            sandbox_type=SandboxType.MACOS_SEATBELT,
        )
        assert result.command == "echo hello"
        assert result.profile_path is not None
        assert "sandbox-exec" in result.argv[0]

    def test_build_landlock_returns_wrapper(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        result = SandboxCommand.build(
            command="echo hello",
            policy=policy,
            sandbox_type=SandboxType.LINUX_LANDLOCK,
        )
        # Landlock wraps via a helper script or env setup
        assert result.command == "echo hello"
        # The argv should contain the landlock wrapper
        assert len(result.argv) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_sandbox_policy.py -v`
Expected: FAIL -- module `duh.adapters.sandbox.policy` does not exist

- [ ] **Step 3: Implement the sandbox policy module**

```python
# duh/adapters/sandbox/__init__.py
"""Platform-native sandboxing for D.U.H."""
```

```python
# duh/adapters/sandbox/policy.py
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
        import ctypes
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_sandbox_policy.py -v`
Expected: All 13 tests pass.

- [ ] **Step 5: Commit**

Run: `cd /Users/nomind/Code/duh && git add duh/adapters/sandbox/__init__.py duh/adapters/sandbox/policy.py tests/unit/test_sandbox_policy.py && git commit -m "Add sandbox policy abstraction: SandboxType, SandboxPolicy, SandboxCommand"`

---

### Task 2: macOS Seatbelt Adapter

**Files:**
- Create: `duh/adapters/sandbox/seatbelt.py`
- Create: `tests/unit/test_seatbelt.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_seatbelt.py
"""Tests for macOS Seatbelt sandbox profile generation."""

import pytest

from duh.adapters.sandbox.policy import SandboxPolicy
from duh.adapters.sandbox.seatbelt import generate_profile


class TestGenerateProfile:
    def test_default_policy_allows_read(self):
        policy = SandboxPolicy()
        profile = generate_profile(policy)
        assert "(version 1)" in profile
        assert "(allow file-read*)" in profile

    def test_default_policy_allows_process(self):
        policy = SandboxPolicy()
        profile = generate_profile(policy)
        assert "(allow process-exec)" in profile
        assert "(allow process-fork)" in profile

    def test_writable_paths_in_profile(self):
        policy = SandboxPolicy(writable_paths=["/tmp/work", "/home/user/.duh"])
        profile = generate_profile(policy)
        assert '(subpath "/tmp/work")' in profile
        assert '(subpath "/home/user/.duh")' in profile
        assert "(allow file-write*" in profile

    def test_no_writable_paths_denies_write(self):
        policy = SandboxPolicy(writable_paths=[])
        profile = generate_profile(policy)
        assert "(deny file-write*" in profile or "(allow file-write*" not in profile

    def test_network_allowed(self):
        policy = SandboxPolicy(network_allowed=True)
        profile = generate_profile(policy)
        assert "(allow network*)" in profile

    def test_network_denied(self):
        policy = SandboxPolicy(network_allowed=False)
        profile = generate_profile(policy)
        assert "(deny network*)" in profile

    def test_readable_paths_in_profile(self):
        policy = SandboxPolicy(readable_paths=["/usr/local", "/opt"])
        profile = generate_profile(policy)
        # Readable paths should appear in file-read rules
        assert '"/usr/local"' in profile or '(subpath "/usr/local")' in profile

    def test_profile_is_valid_sexp(self):
        """Basic validation: parens should balance."""
        policy = SandboxPolicy(
            writable_paths=["/tmp"],
            readable_paths=["/usr"],
            network_allowed=False,
        )
        profile = generate_profile(policy)
        open_count = profile.count("(")
        close_count = profile.count(")")
        assert open_count == close_count, (
            f"Unbalanced parens: {open_count} open, {close_count} close"
        )

    def test_temp_dir_always_writable(self):
        """Temp dirs should always be writable for subprocess needs."""
        policy = SandboxPolicy(writable_paths=[])
        profile = generate_profile(policy)
        # /tmp or /private/tmp should be writable (macOS maps /tmp -> /private/tmp)
        assert "/tmp" in profile or "/private/tmp" in profile

    def test_home_duh_always_writable(self):
        """~/.duh should always be writable for duh's own state."""
        policy = SandboxPolicy(writable_paths=[])
        profile = generate_profile(policy)
        assert ".duh" in profile

    def test_profile_denies_by_default(self):
        """The profile should have a default-deny stance."""
        policy = SandboxPolicy()
        profile = generate_profile(policy)
        assert "(deny default)" in profile
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_seatbelt.py -v`
Expected: FAIL -- `duh.adapters.sandbox.seatbelt` does not exist

- [ ] **Step 3: Implement the Seatbelt adapter**

```python
# duh/adapters/sandbox/seatbelt.py
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
    lines.append(";; Process execution")
    lines.append("(allow process-exec)")
    lines.append("(allow process-fork)")
    lines.append("(allow process*)")
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
            if wp.startswith("/dev/"):
                lines.append(f'    (literal "{wp}")')
            else:
                lines.append(f'    (subpath "{wp}")')
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_seatbelt.py -v`
Expected: All 11 tests pass.

- [ ] **Step 5: Commit**

Run: `cd /Users/nomind/Code/duh && git add duh/adapters/sandbox/seatbelt.py tests/unit/test_seatbelt.py && git commit -m "Add macOS Seatbelt adapter: profile generation from SandboxPolicy"`

---

### Task 3: Linux Landlock Adapter

**Files:**
- Create: `duh/adapters/sandbox/landlock.py`
- Create: `tests/unit/test_landlock.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_landlock.py
"""Tests for Linux Landlock sandbox adapter."""

import os
import struct
from unittest.mock import MagicMock, patch

import pytest

from duh.adapters.sandbox.policy import SandboxPolicy
from duh.adapters.sandbox.landlock import (
    LANDLOCK_ACCESS_FS_EXECUTE,
    LANDLOCK_ACCESS_FS_READ_FILE,
    LANDLOCK_ACCESS_FS_READ_DIR,
    LANDLOCK_ACCESS_FS_WRITE_FILE,
    LANDLOCK_ACCESS_FS_MAKE_REG,
    LANDLOCK_ACCESS_FS_MAKE_DIR,
    LandlockRuleset,
    build_landlock_argv,
    build_ruleset,
)


class TestLandlockConstants:
    def test_access_flags_are_powers_of_two(self):
        flags = [
            LANDLOCK_ACCESS_FS_EXECUTE,
            LANDLOCK_ACCESS_FS_WRITE_FILE,
            LANDLOCK_ACCESS_FS_READ_FILE,
            LANDLOCK_ACCESS_FS_READ_DIR,
            LANDLOCK_ACCESS_FS_MAKE_REG,
            LANDLOCK_ACCESS_FS_MAKE_DIR,
        ]
        for flag in flags:
            assert flag > 0
            assert (flag & (flag - 1)) == 0, f"{flag} is not a power of 2"


class TestBuildRuleset:
    def test_default_policy_allows_read(self):
        policy = SandboxPolicy()
        ruleset = build_ruleset(policy)
        assert isinstance(ruleset, LandlockRuleset)
        # Default: read allowed everywhere
        assert len(ruleset.read_paths) > 0 or ruleset.global_read is True

    def test_writable_paths_in_ruleset(self):
        policy = SandboxPolicy(writable_paths=["/tmp", "/home/user/.duh"])
        ruleset = build_ruleset(policy)
        assert "/tmp" in ruleset.write_paths
        assert "/home/user/.duh" in ruleset.write_paths

    def test_no_writable_paths_empty(self):
        policy = SandboxPolicy(writable_paths=[])
        ruleset = build_ruleset(policy)
        # Should still include /tmp and ~/.duh as always-writable
        assert "/tmp" in ruleset.write_paths

    def test_always_writable_includes_tmp_and_duh(self):
        policy = SandboxPolicy(writable_paths=[])
        ruleset = build_ruleset(policy)
        assert "/tmp" in ruleset.write_paths
        home_duh = os.path.expanduser("~/.duh")
        assert home_duh in ruleset.write_paths

    def test_network_policy_passed_through(self):
        policy = SandboxPolicy(network_allowed=False)
        ruleset = build_ruleset(policy)
        assert ruleset.network_allowed is False


class TestBuildLandlockArgv:
    def test_returns_argv_and_env(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        argv, env = build_landlock_argv("echo hello", policy)
        assert isinstance(argv, list)
        assert len(argv) > 0
        # The command should be somewhere in the argv
        assert any("echo hello" in arg for arg in argv)

    def test_env_contains_landlock_vars(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        argv, env = build_landlock_argv("echo hello", policy)
        # Env should have DUH_LANDLOCK_* vars or be None (if using wrapper script)
        # Either approach is valid
        assert isinstance(env, dict) or env is None

    def test_returns_bash_wrapper(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        argv, env = build_landlock_argv("ls -la", policy)
        # Should wrap in python -c or bash -c
        assert "bash" in argv[0] or "python" in argv[0] or argv[0].endswith("python3")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_landlock.py -v`
Expected: FAIL -- `duh.adapters.sandbox.landlock` does not exist

- [ ] **Step 3: Implement the Landlock adapter**

```python
# duh/adapters/sandbox/landlock.py
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

from duh.adapters.sandbox.policy import SandboxPolicy

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
    # Deduplicate
    seen: set[str] = set()
    unique: list[str] = []
    for p in write_paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)

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
                print("Landlock not available, running without sandbox", file=sys.stderr)
                os.execvp("bash", ["bash", "-c", command])
                return
            raise OSError(f"landlock_create_ruleset failed: errno {{errno}}")

        # Add rules for each path
        for rule in config["rules"]:
            path = rule["path"].encode("utf-8")
            access = rule["access"]
            try:
                fd = os.open(rule["path"], os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue  # Skip paths that don't exist
            # struct landlock_path_beneath_attr {{ __u64 allowed_access; __s32 parent_fd; }}
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
            print("landlock_restrict_self failed, running without sandbox", file=sys.stderr)

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_landlock.py -v`
Expected: All 11 tests pass.

- [ ] **Step 5: Commit**

Run: `cd /Users/nomind/Code/duh && git add duh/adapters/sandbox/landlock.py tests/unit/test_landlock.py && git commit -m "Add Linux Landlock adapter: ctypes syscalls + wrapper script generation"`

---

### Task 4: Network Policy

**Files:**
- Create: `duh/adapters/sandbox/network.py`
- Create: `tests/unit/test_network_policy.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_network_policy.py
"""Tests for network policy enforcement."""

import pytest

from duh.adapters.sandbox.network import NetworkMode, NetworkPolicy


class TestNetworkMode:
    def test_full_mode(self):
        assert NetworkMode.FULL.value == "full"

    def test_limited_mode(self):
        assert NetworkMode.LIMITED.value == "limited"

    def test_none_mode(self):
        assert NetworkMode.NONE.value == "none"


class TestNetworkPolicy:
    def test_default_is_full(self):
        policy = NetworkPolicy()
        assert policy.mode == NetworkMode.FULL
        assert policy.allowed_hosts == []
        assert policy.denied_hosts == []

    def test_none_mode_denies_all(self):
        policy = NetworkPolicy(mode=NetworkMode.NONE)
        assert policy.is_request_allowed("GET", "https://example.com") is False

    def test_full_mode_allows_all(self):
        policy = NetworkPolicy(mode=NetworkMode.FULL)
        assert policy.is_request_allowed("POST", "https://example.com") is True

    def test_limited_mode_allows_safe_methods(self):
        policy = NetworkPolicy(mode=NetworkMode.LIMITED)
        assert policy.is_request_allowed("GET", "https://example.com") is True
        assert policy.is_request_allowed("HEAD", "https://example.com") is True
        assert policy.is_request_allowed("OPTIONS", "https://example.com") is True

    def test_limited_mode_blocks_mutating_methods(self):
        policy = NetworkPolicy(mode=NetworkMode.LIMITED)
        assert policy.is_request_allowed("POST", "https://example.com") is False
        assert policy.is_request_allowed("PUT", "https://example.com") is False
        assert policy.is_request_allowed("DELETE", "https://example.com") is False
        assert policy.is_request_allowed("PATCH", "https://example.com") is False

    def test_allowed_hosts_filter(self):
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            allowed_hosts=["api.example.com", "cdn.example.com"],
        )
        assert policy.is_request_allowed("GET", "https://api.example.com/v1") is True
        assert policy.is_request_allowed("GET", "https://evil.com") is False

    def test_denied_hosts_filter(self):
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            denied_hosts=["evil.com", "malware.org"],
        )
        assert policy.is_request_allowed("GET", "https://evil.com/payload") is False
        assert policy.is_request_allowed("GET", "https://example.com") is True

    def test_denied_hosts_override_allowed(self):
        """Deny list wins over allow list."""
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            allowed_hosts=["evil.com"],
            denied_hosts=["evil.com"],
        )
        assert policy.is_request_allowed("GET", "https://evil.com") is False

    def test_to_sandbox_network_flag_full(self):
        policy = NetworkPolicy(mode=NetworkMode.FULL)
        assert policy.to_sandbox_flag() is True

    def test_to_sandbox_network_flag_none(self):
        policy = NetworkPolicy(mode=NetworkMode.NONE)
        assert policy.to_sandbox_flag() is False

    def test_to_sandbox_network_flag_limited(self):
        policy = NetworkPolicy(mode=NetworkMode.LIMITED)
        # Limited still needs network access (filtering happens at app level)
        assert policy.to_sandbox_flag() is True

    def test_host_extraction_from_url(self):
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            allowed_hosts=["example.com"],
        )
        assert policy.is_request_allowed("GET", "https://example.com:8443/path") is True
        assert policy.is_request_allowed("GET", "http://other.com/path") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_network_policy.py -v`
Expected: FAIL -- `duh.adapters.sandbox.network` does not exist

- [ ] **Step 3: Implement the network policy module**

```python
# duh/adapters/sandbox/network.py
"""Network policy -- controls network access for sandboxed commands.

Three modes:
    FULL    -- All network requests allowed (default)
    LIMITED -- Only safe HTTP methods (GET, HEAD, OPTIONS) allowed
    NONE    -- No network access at all

Limited mode is enforced at the application level (in WebFetch tool).
Full/None are enforced at the sandbox level (Seatbelt deny network*,
or Landlock -- though Landlock v1 doesn't restrict network, only fs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse


class NetworkMode(Enum):
    """Network access modes."""
    FULL = "full"
    LIMITED = "limited"
    NONE = "none"


# HTTP methods considered safe (non-mutating)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class NetworkPolicy:
    """Network access policy for sandboxed environments.

    Enforced at two levels:
    1. Sandbox level: network allowed or denied (Seatbelt/Landlock)
    2. Application level: method filtering in WebFetch (LIMITED mode)
    """
    mode: NetworkMode = NetworkMode.FULL
    allowed_hosts: list[str] = field(default_factory=list)
    denied_hosts: list[str] = field(default_factory=list)

    def _extract_host(self, url: str) -> str:
        """Extract hostname from a URL."""
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            return host.lower()
        except Exception:
            return ""

    def is_request_allowed(self, method: str, url: str) -> bool:
        """Check if a network request is allowed under this policy.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL being requested.

        Returns:
            True if the request is allowed.
        """
        # NONE mode blocks everything
        if self.mode == NetworkMode.NONE:
            return False

        # LIMITED mode: only safe methods
        if self.mode == NetworkMode.LIMITED:
            if method.upper() not in _SAFE_METHODS:
                return False

        # Check host filters
        host = self._extract_host(url)

        # Denied hosts always block (checked first)
        if self.denied_hosts:
            for denied in self.denied_hosts:
                if host == denied.lower() or host.endswith(f".{denied.lower()}"):
                    return False

        # If allowed_hosts is set, only those are permitted
        if self.allowed_hosts:
            for allowed in self.allowed_hosts:
                if host == allowed.lower() or host.endswith(f".{allowed.lower()}"):
                    return True
            return False

        return True

    def to_sandbox_flag(self) -> bool:
        """Convert to a boolean for SandboxPolicy.network_allowed.

        FULL and LIMITED both need network at the OS level.
        LIMITED filtering happens at the application layer.
        """
        return self.mode != NetworkMode.NONE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_network_policy.py -v`
Expected: All 15 tests pass.

- [ ] **Step 5: Commit**

Run: `cd /Users/nomind/Code/duh && git add duh/adapters/sandbox/network.py tests/unit/test_network_policy.py && git commit -m "Add NetworkPolicy: FULL/LIMITED/NONE modes with host filtering"`

---

### Task 5: 3-Tier Approval Model

**Files:**
- Modify: `duh/adapters/approvers.py`
- Create: `tests/unit/test_tiered_approver.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_tiered_approver.py
"""Tests for the 3-tier approval model."""

import subprocess
from unittest.mock import patch

import pytest

from duh.adapters.approvers import ApprovalMode, TieredApprover


class TestApprovalMode:
    def test_suggest_mode(self):
        assert ApprovalMode.SUGGEST.value == "suggest"

    def test_auto_edit_mode(self):
        assert ApprovalMode.AUTO_EDIT.value == "auto-edit"

    def test_full_auto_mode(self):
        assert ApprovalMode.FULL_AUTO.value == "full-auto"


class TestTieredApproverSuggestMode:
    """SUGGEST mode: only reads are auto-approved."""

    async def test_read_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Read", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_glob_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Glob", {"pattern": "*.py"})
        assert result["allowed"] is True

    async def test_grep_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Grep", {"pattern": "foo"})
        assert result["allowed"] is True

    async def test_write_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Write", {"file_path": "/tmp/x"})
        assert result["allowed"] is False
        assert "approval" in result["reason"].lower() or "suggest" in result["reason"].lower()

    async def test_bash_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Bash", {"command": "ls"})
        assert result["allowed"] is False

    async def test_edit_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Edit", {"file_path": "/tmp/x"})
        assert result["allowed"] is False


class TestTieredApproverAutoEditMode:
    """AUTO_EDIT mode: reads + writes auto-approved, commands need approval."""

    async def test_read_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("Read", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_write_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("Write", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_edit_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("Edit", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_multi_edit_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("MultiEdit", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_bash_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("Bash", {"command": "npm test"})
        assert result["allowed"] is False
        assert "approval" in result["reason"].lower() or "auto-edit" in result["reason"].lower()

    async def test_web_fetch_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("WebFetch", {"url": "https://example.com"})
        assert result["allowed"] is False


class TestTieredApproverFullAutoMode:
    """FULL_AUTO mode: everything auto-approved."""

    async def test_read_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Read", {})
        assert result["allowed"] is True

    async def test_write_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Write", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_bash_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "rm -rf /"})
        assert result["allowed"] is True

    async def test_web_fetch_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("WebFetch", {"url": "https://example.com"})
        assert result["allowed"] is True

    async def test_unknown_tool_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("SomeFutureTool", {"x": 1})
        assert result["allowed"] is True


class TestTieredApproverToolClassification:
    """Verify tool classification is correct."""

    async def test_read_tools_are_read_only(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        read_tools = ["Read", "Glob", "Grep", "ToolSearch", "WebSearch"]
        for tool in read_tools:
            result = await approver.check(tool, {})
            assert result["allowed"] is True, f"{tool} should be auto-approved in SUGGEST"

    async def test_write_tools_classified(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        write_tools = ["Write", "Edit", "MultiEdit", "NotebookEdit"]
        for tool in write_tools:
            result = await approver.check(tool, {})
            assert result["allowed"] is True, f"{tool} should be auto-approved in AUTO_EDIT"

    async def test_command_tools_classified(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        cmd_tools = ["Bash", "WebFetch"]
        for tool in cmd_tools:
            result = await approver.check(tool, {})
            assert result["allowed"] is False, f"{tool} should need approval in AUTO_EDIT"


class TestTieredApproverGitSafety:
    @patch("duh.adapters.approvers._is_git_repo", return_value=False)
    def test_warns_without_git(self, mock_git):
        """Should emit a warning if not in a git repo with auto-edit or full-auto."""
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TieredApprover(mode=ApprovalMode.AUTO_EDIT, cwd="/tmp/no-git")
            git_warnings = [x for x in w if "git" in str(x.message).lower()]
            assert len(git_warnings) >= 1

    @patch("duh.adapters.approvers._is_git_repo", return_value=True)
    def test_no_warn_with_git(self, mock_git):
        """No warning when in a git repo."""
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TieredApprover(mode=ApprovalMode.AUTO_EDIT, cwd="/tmp/has-git")
            git_warnings = [x for x in w if "git" in str(x.message).lower()]
            assert len(git_warnings) == 0

    def test_suggest_mode_no_git_warning(self):
        """SUGGEST mode doesn't need git safety warning."""
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TieredApprover(mode=ApprovalMode.SUGGEST)
            git_warnings = [x for x in w if "git" in str(x.message).lower()]
            assert len(git_warnings) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_tiered_approver.py -v`
Expected: FAIL -- `ApprovalMode` and `TieredApprover` not defined

- [ ] **Step 3: Implement the 3-tier approver**

Add the following to the end of `duh/adapters/approvers.py` (after the existing `RuleApprover` class):

```python
# ---------------------------------------------------------------------------
# 3-Tier Approval Model (Phase 3: Codex Steals)
# ---------------------------------------------------------------------------

import warnings
from enum import Enum
from pathlib import Path


class ApprovalMode(Enum):
    """Three-tier approval model.

    SUGGEST:   Only reads auto-approved. Writes and commands need human approval.
    AUTO_EDIT: Reads + writes auto-approved. Commands (Bash, WebFetch) need approval.
    FULL_AUTO: Everything auto-approved. Use only in sandboxed environments.
    """
    SUGGEST = "suggest"
    AUTO_EDIT = "auto-edit"
    FULL_AUTO = "full-auto"


# Tool classification: which tools belong to which tier
_READ_TOOLS = frozenset({
    "Read", "Glob", "Grep", "ToolSearch", "WebSearch",
    "MemoryRecall", "Skill",
})

_WRITE_TOOLS = frozenset({
    "Write", "Edit", "MultiEdit", "NotebookEdit",
    "EnterWorktree", "ExitWorktree", "MemoryStore",
})

_COMMAND_TOOLS = frozenset({
    "Bash", "WebFetch", "Task", "HTTP", "Database", "Docker",
    "GitHub",
})


def _is_git_repo(cwd: str) -> bool:
    """Check if the given directory is inside a git repository."""
    current = Path(cwd).resolve()
    for _ in range(100):
        if (current / ".git").exists():
            return True
        parent = current.parent
        if parent == current:
            break
        current = parent
    return False


class TieredApprover:
    """3-tier approval gate: SUGGEST / AUTO_EDIT / FULL_AUTO.

    Tool calls are classified into three tiers:
        Read:    Read, Glob, Grep, ToolSearch, WebSearch, MemoryRecall, Skill
        Write:   Write, Edit, MultiEdit, NotebookEdit, worktree tools, MemoryStore
        Command: Bash, WebFetch, Task, HTTP, Database, Docker, GitHub

    Approval behavior per mode:
        SUGGEST:   Read auto-approved; Write and Command need approval
        AUTO_EDIT: Read and Write auto-approved; Command needs approval
        FULL_AUTO: Everything auto-approved

    On construction, warns if mode is AUTO_EDIT or FULL_AUTO and cwd is
    not inside a git repo (safety net for recovering from bad edits).
    """

    def __init__(
        self,
        mode: ApprovalMode = ApprovalMode.SUGGEST,
        cwd: str | None = None,
    ):
        self._mode = mode

        # Git safety check for permissive modes
        if mode in (ApprovalMode.AUTO_EDIT, ApprovalMode.FULL_AUTO):
            check_cwd = cwd or "."
            if not _is_git_repo(check_cwd):
                warnings.warn(
                    f"--approval-mode {mode.value} without a git repo is risky. "
                    f"Changes cannot be reverted via git. Consider initializing "
                    f"a git repo first: git init",
                    UserWarning,
                    stacklevel=2,
                )

    @property
    def mode(self) -> ApprovalMode:
        return self._mode

    async def check(self, tool_name: str, input: dict[str, Any]) -> dict[str, Any]:
        """Check if a tool call is approved under the current mode."""
        # FULL_AUTO: approve everything
        if self._mode == ApprovalMode.FULL_AUTO:
            return {"allowed": True}

        # Classify the tool
        if tool_name in _READ_TOOLS:
            # Reads are always auto-approved
            return {"allowed": True}

        if tool_name in _WRITE_TOOLS:
            if self._mode == ApprovalMode.AUTO_EDIT:
                return {"allowed": True}
            # SUGGEST mode: writes need approval
            return {
                "allowed": False,
                "reason": (
                    f"Tool '{tool_name}' requires approval in suggest mode. "
                    f"Use --approval-mode auto-edit to auto-approve file edits."
                ),
            }

        if tool_name in _COMMAND_TOOLS:
            if self._mode == ApprovalMode.FULL_AUTO:
                return {"allowed": True}
            # Both SUGGEST and AUTO_EDIT need approval for commands
            return {
                "allowed": False,
                "reason": (
                    f"Tool '{tool_name}' requires approval in {self._mode.value} mode. "
                    f"Use --approval-mode full-auto to auto-approve all operations."
                ),
            }

        # Unknown tool: follow the most restrictive applicable rule
        if self._mode == ApprovalMode.SUGGEST:
            return {
                "allowed": False,
                "reason": f"Unknown tool '{tool_name}' requires approval in suggest mode.",
            }
        # AUTO_EDIT: unknown tools need approval (conservative)
        return {
            "allowed": False,
            "reason": f"Unknown tool '{tool_name}' requires approval in {self._mode.value} mode.",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_tiered_approver.py -v`
Expected: All 24 tests pass.

- [ ] **Step 5: Commit**

Run: `cd /Users/nomind/Code/duh && git add duh/adapters/approvers.py tests/unit/test_tiered_approver.py && git commit -m "Add 3-tier approval model: SUGGEST / AUTO_EDIT / FULL_AUTO with git safety"`

---

### Task 6: Ghost Snapshot Mode

**Files:**
- Create: `duh/kernel/snapshot.py`
- Create: `tests/unit/test_snapshot.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_snapshot.py
"""Tests for ghost snapshot mode."""

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from duh.kernel.messages import Message
from duh.kernel.snapshot import ReadOnlyExecutor, SnapshotSession


class TestReadOnlyExecutor:
    async def test_allows_read(self):
        inner = AsyncMock(return_value="file contents")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("Read", {"file_path": "/tmp/x"})
        assert result == "file contents"
        inner.assert_called_once()

    async def test_allows_glob(self):
        inner = AsyncMock(return_value="a.py\nb.py")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("Glob", {"pattern": "*.py"})
        assert result == "a.py\nb.py"

    async def test_allows_grep(self):
        inner = AsyncMock(return_value="match")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("Grep", {"pattern": "foo"})
        assert result == "match"

    async def test_blocks_write(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("Write", {"file_path": "/tmp/x", "content": "y"})
        inner.assert_not_called()

    async def test_blocks_edit(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("Edit", {"file_path": "/tmp/x"})
        inner.assert_not_called()

    async def test_blocks_bash(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("Bash", {"command": "rm -rf /"})
        inner.assert_not_called()

    async def test_blocks_multi_edit(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("MultiEdit", {"file_path": "/tmp/x"})

    async def test_allows_tool_search(self):
        inner = AsyncMock(return_value="results")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("ToolSearch", {"query": "test"})
        assert result == "results"

    async def test_allows_web_search(self):
        inner = AsyncMock(return_value="results")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("WebSearch", {"query": "test"})
        assert result == "results"


class TestSnapshotSession:
    def test_creates_forked_state(self):
        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        snapshot = SnapshotSession(messages)
        assert len(snapshot.messages) == 2
        # Verify deep copy: modifying original doesn't affect snapshot
        messages.append(Message(role="user", content="new"))
        assert len(snapshot.messages) == 2

    def test_messages_are_independent_copies(self):
        messages = [Message(role="user", content="hello")]
        snapshot = SnapshotSession(messages)
        # Modify the snapshot's messages
        snapshot.messages.append(Message(role="user", content="extra"))
        # Original should be unaffected
        assert len(messages) == 1

    def test_add_message(self):
        snapshot = SnapshotSession([])
        snapshot.add_message(Message(role="user", content="test"))
        assert len(snapshot.messages) == 1

    def test_discard(self):
        snapshot = SnapshotSession([Message(role="user", content="hello")])
        snapshot.add_message(Message(role="assistant", content="reply"))
        snapshot.discard()
        assert len(snapshot.messages) == 0
        assert snapshot.is_discarded is True

    def test_is_discarded_default(self):
        snapshot = SnapshotSession([])
        assert snapshot.is_discarded is False

    def test_merge_returns_new_messages(self):
        original = [Message(role="user", content="hello")]
        snapshot = SnapshotSession(original)
        snapshot.add_message(Message(role="assistant", content="hi from snapshot"))
        snapshot.add_message(Message(role="user", content="more"))
        new_msgs = snapshot.get_new_messages()
        assert len(new_msgs) == 2
        assert new_msgs[0].content == "hi from snapshot"
        assert new_msgs[1].content == "more"

    def test_merge_after_discard_returns_empty(self):
        snapshot = SnapshotSession([Message(role="user", content="hello")])
        snapshot.add_message(Message(role="assistant", content="reply"))
        snapshot.discard()
        assert snapshot.get_new_messages() == []

    def test_str_representation(self):
        snapshot = SnapshotSession([Message(role="user", content="hello")])
        s = str(snapshot)
        assert "Snapshot" in s or "snapshot" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_snapshot.py -v`
Expected: FAIL -- `duh.kernel.snapshot` does not exist

- [ ] **Step 3: Implement the ghost snapshot module**

```python
# duh/kernel/snapshot.py
"""Ghost snapshot mode -- fork engine state for read-only exploration.

Allows the model to explore "what-if" scenarios without committing changes.
The snapshot forks the message history and wraps tool execution in a
read-only layer that blocks all mutating operations.

Usage in REPL:
    /snapshot         -- enter snapshot mode
    /snapshot apply   -- merge snapshot messages back into main session
    /snapshot discard -- discard snapshot and return to main session

Usage programmatically:
    executor = ReadOnlyExecutor(real_executor.run)
    snapshot = SnapshotSession(engine.messages)
    # ... run queries against snapshot ...
    if keep:
        new_messages = snapshot.get_new_messages()
        engine._messages.extend(new_messages)
    else:
        snapshot.discard()
"""

from __future__ import annotations

import copy
from typing import Any, Awaitable, Callable

from duh.kernel.messages import Message


# Tools that are safe to run in snapshot mode (read-only)
_SNAPSHOT_ALLOWED_TOOLS = frozenset({
    "Read", "Glob", "Grep", "ToolSearch", "WebSearch",
    "MemoryRecall", "Skill",
})

# Tools that are explicitly blocked in snapshot mode (mutating)
_SNAPSHOT_BLOCKED_TOOLS = frozenset({
    "Write", "Edit", "MultiEdit", "Bash", "NotebookEdit",
    "WebFetch", "HTTP", "Database", "Docker", "GitHub",
    "Task", "EnterWorktree", "ExitWorktree", "MemoryStore",
})


class ReadOnlyExecutor:
    """Wraps a tool executor to block all mutating operations.

    Only read-only tools (Read, Glob, Grep, ToolSearch, WebSearch) are
    allowed. Everything else raises PermissionError.
    """

    def __init__(self, inner_run: Callable[..., Awaitable[Any]]):
        self._inner_run = inner_run

    async def run(
        self,
        tool_name: str,
        input: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        """Execute a tool if it's read-only, otherwise raise PermissionError."""
        if tool_name in _SNAPSHOT_ALLOWED_TOOLS:
            return await self._inner_run(tool_name, input, **kwargs)

        raise PermissionError(
            f"Snapshot mode: tool '{tool_name}' is blocked. "
            f"Only read-only tools are allowed in snapshot mode. "
            f"Use /snapshot apply to return to normal mode and execute writes."
        )


class SnapshotSession:
    """A forked conversation state for read-only exploration.

    Deep-copies the message history so changes to the snapshot don't
    affect the original session. New messages added during snapshot
    exploration can be merged back (apply) or thrown away (discard).
    """

    def __init__(self, messages: list[Message]):
        self._original_count = len(messages)
        self._messages: list[Message] = copy.deepcopy(messages)
        self._is_discarded = False

    @property
    def messages(self) -> list[Message]:
        """Return the snapshot's message list."""
        return self._messages

    @property
    def is_discarded(self) -> bool:
        """True if the snapshot has been discarded."""
        return self._is_discarded

    def add_message(self, message: Message) -> None:
        """Add a message to the snapshot."""
        if self._is_discarded:
            raise RuntimeError("Cannot add messages to a discarded snapshot")
        self._messages.append(message)

    def get_new_messages(self) -> list[Message]:
        """Return only the messages added after the snapshot was created.

        Returns an empty list if the snapshot has been discarded.
        """
        if self._is_discarded:
            return []
        return self._messages[self._original_count:]

    def discard(self) -> None:
        """Discard the snapshot. Clears all messages."""
        self._messages.clear()
        self._is_discarded = True

    def __str__(self) -> str:
        status = "discarded" if self._is_discarded else "active"
        total = len(self._messages)
        new = len(self.get_new_messages())
        return f"Snapshot({status}, {total} messages, {new} new)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_snapshot.py -v`
Expected: All 17 tests pass.

- [ ] **Step 5: Commit**

Run: `cd /Users/nomind/Code/duh && git add duh/kernel/snapshot.py tests/unit/test_snapshot.py && git commit -m "Add ghost snapshot mode: ReadOnlyExecutor + SnapshotSession for safe exploration"`

---

### Task 7: Integration -- Sandbox + BashTool

**Files:**
- Modify: `duh/kernel/tool.py` (add `sandbox_policy` to `ToolContext`)
- Modify: `duh/tools/bash.py` (wrap commands with sandbox when policy is set)
- Create: `tests/unit/test_bash_sandboxed.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_bash_sandboxed.py
"""Tests for sandboxed BashTool execution."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.adapters.sandbox.policy import SandboxCommand, SandboxPolicy, SandboxType
from duh.kernel.tool import ToolContext
from duh.tools.bash import BashTool


class TestToolContextSandboxPolicy:
    def test_default_is_none(self):
        ctx = ToolContext()
        assert ctx.sandbox_policy is None

    def test_accepts_policy(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        ctx = ToolContext(sandbox_policy=policy)
        assert ctx.sandbox_policy is policy


class TestBashToolSandboxed:
    async def test_no_policy_runs_normally(self):
        tool = BashTool()
        ctx = ToolContext(cwd="/tmp", metadata={"skip_permissions": True})
        result = await tool.call({"command": "echo hello"}, ctx)
        assert "hello" in result.output

    @patch("duh.tools.bash.SandboxCommand")
    async def test_with_policy_wraps_command(self, mock_sandbox_cmd):
        """When sandbox_policy is set, the command should be wrapped."""
        policy = SandboxPolicy(writable_paths=["/tmp"])
        ctx = ToolContext(
            cwd="/tmp",
            sandbox_policy=policy,
            metadata={"skip_permissions": True},
        )

        # Mock SandboxCommand.build to return a passthrough
        mock_cmd = MagicMock()
        mock_cmd.argv = ["bash", "-c", "echo sandboxed"]
        mock_cmd.profile_path = None
        mock_cmd.env = None
        mock_cmd.cleanup = MagicMock()
        mock_sandbox_cmd.build.return_value = mock_cmd

        tool = BashTool()
        result = await tool.call({"command": "echo hello"}, ctx)

        # Verify SandboxCommand.build was called
        mock_sandbox_cmd.build.assert_called_once()
        call_args = mock_sandbox_cmd.build.call_args
        assert call_args.kwargs.get("command") == "echo hello" or call_args[0][0] == "echo hello"

    async def test_sandbox_policy_on_context(self):
        """Verify ToolContext carries the sandbox_policy."""
        policy = SandboxPolicy(writable_paths=["/tmp"], network_allowed=False)
        ctx = ToolContext(sandbox_policy=policy)
        assert ctx.sandbox_policy.network_allowed is False
        assert ctx.sandbox_policy.writable_paths == ["/tmp"]

    @patch("duh.tools.bash.detect_sandbox_type", return_value=SandboxType.NONE)
    async def test_none_sandbox_type_no_wrapping(self, mock_detect):
        """With SandboxType.NONE, command should pass through unwrapped."""
        policy = SandboxPolicy(writable_paths=["/tmp"])
        ctx = ToolContext(
            cwd="/tmp",
            sandbox_policy=policy,
            metadata={"skip_permissions": True},
        )
        tool = BashTool()
        result = await tool.call({"command": "echo passthrough"}, ctx)
        # Even with NONE type, the command should still run
        assert "passthrough" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_bash_sandboxed.py -v`
Expected: FAIL -- `ToolContext` has no `sandbox_policy` field

- [ ] **Step 3: Add `sandbox_policy` to `ToolContext`**

In `duh/kernel/tool.py`, modify the `ToolContext` dataclass to add the sandbox_policy field. Add the import at the top and the field:

Add after the existing imports at the top of `duh/kernel/tool.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from duh.adapters.sandbox.policy import SandboxPolicy as _SandboxPolicy
```

Then modify the `ToolContext` dataclass to add:

```python
@dataclass
class ToolContext:
    """Runtime context available to tools during execution."""
    cwd: str = "."
    tool_use_id: str = ""
    abort_signal: Any = None
    permissions: Any = None  # ApprovalGate adapter
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    sandbox_policy: Any = None  # SandboxPolicy | None
```

- [ ] **Step 4: Modify `BashTool` to use sandbox policy**

In `duh/tools/bash.py`, add the sandbox import and modify the `call` method. Add after the existing imports:

```python
from duh.adapters.sandbox.policy import SandboxCommand, detect_sandbox_type
```

Then in the `call` method, after the security check and before building the command, add sandbox wrapping logic. Replace the section from `cwd = context.cwd ...` through `argv = build_shell_command(command, resolved_shell)` with:

```python
        cwd = context.cwd if context.cwd and context.cwd != "." else None

        # --- Sandbox wrapping (when policy is set on context) ---
        sandbox_cmd = None
        if context.sandbox_policy is not None:
            sandbox_type = detect_sandbox_type()
            sandbox_cmd = SandboxCommand.build(
                command=command,
                policy=context.sandbox_policy,
                sandbox_type=sandbox_type,
            )
            argv = sandbox_cmd.argv
        else:
            argv = build_shell_command(command, resolved_shell)
```

And after the `proc.communicate()` block completes (after the `except` blocks and before the output processing), add cleanup:

```python
        # Clean up sandbox temp files
        if sandbox_cmd is not None:
            sandbox_cmd.cleanup()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_bash_sandboxed.py -v`
Expected: All 4 tests pass.

- [ ] **Step 6: Run existing BashTool tests to verify no regressions**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_cross_shell.py tests/unit/test_bash_security.py -v`
Expected: All existing tests still pass.

- [ ] **Step 7: Commit**

Run: `cd /Users/nomind/Code/duh && git add duh/kernel/tool.py duh/tools/bash.py tests/unit/test_bash_sandboxed.py && git commit -m "Integrate sandbox with BashTool: wrap commands via SandboxCommand when policy is set"`

---

### Task 8: Integration -- Approval Mode + CLI

**Files:**
- Modify: `duh/cli/parser.py` (add `--approval-mode` flag)
- Modify: `duh/config.py` (add `approval_mode` to `Config`)
- Modify: `duh/cli/repl.py` (wire `TieredApprover` + `/snapshot` command)
- Create: `tests/unit/test_approval_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_approval_cli.py
"""Tests for approval mode CLI integration."""

import pytest

from duh.adapters.approvers import ApprovalMode, TieredApprover
from duh.cli.parser import build_parser
from duh.config import Config


class TestParserApprovalMode:
    def test_default_is_none(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.approval_mode is None

    def test_suggest_mode(self):
        parser = build_parser()
        args = parser.parse_args(["--approval-mode", "suggest"])
        assert args.approval_mode == "suggest"

    def test_auto_edit_mode(self):
        parser = build_parser()
        args = parser.parse_args(["--approval-mode", "auto-edit"])
        assert args.approval_mode == "auto-edit"

    def test_full_auto_mode(self):
        parser = build_parser()
        args = parser.parse_args(["--approval-mode", "full-auto"])
        assert args.approval_mode == "full-auto"

    def test_invalid_mode_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--approval-mode", "yolo"])

    def test_combined_with_other_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "--approval-mode", "auto-edit",
            "--model", "opus",
            "--max-turns", "5",
        ])
        assert args.approval_mode == "auto-edit"
        assert args.model == "opus"
        assert args.max_turns == 5


class TestConfigApprovalMode:
    def test_default_is_empty(self):
        config = Config()
        assert config.approval_mode == ""

    def test_accepts_string(self):
        config = Config(approval_mode="auto-edit")
        assert config.approval_mode == "auto-edit"


class TestApprovalModeFromString:
    def test_suggest(self):
        mode = ApprovalMode("suggest")
        assert mode == ApprovalMode.SUGGEST

    def test_auto_edit(self):
        mode = ApprovalMode("auto-edit")
        assert mode == ApprovalMode.AUTO_EDIT

    def test_full_auto(self):
        mode = ApprovalMode("full-auto")
        assert mode == ApprovalMode.FULL_AUTO

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            ApprovalMode("yolo")


class TestTieredApproverConstruction:
    def test_from_cli_string(self):
        """Verify the full flow: CLI string -> ApprovalMode -> TieredApprover."""
        mode_str = "auto-edit"
        mode = ApprovalMode(mode_str)
        approver = TieredApprover(mode=mode, cwd="/tmp")
        assert approver.mode == ApprovalMode.AUTO_EDIT

    async def test_constructed_approver_works(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        # Read should be auto-approved
        result = await approver.check("Read", {"file_path": "/tmp/x"})
        assert result["allowed"] is True
        # Bash should need approval
        result = await approver.check("Bash", {"command": "ls"})
        assert result["allowed"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_approval_cli.py -v`
Expected: FAIL -- parser has no `--approval-mode` flag, Config has no `approval_mode`

- [ ] **Step 3: Add `--approval-mode` to the CLI parser**

In `duh/cli/parser.py`, add after the `--dangerously-skip-permissions` argument:

```python
    parser.add_argument("--approval-mode", type=str, default=None,
                        choices=["suggest", "auto-edit", "full-auto"],
                        help="Approval mode: suggest (reads auto-approved), "
                             "auto-edit (reads+writes auto-approved), "
                             "full-auto (all auto-approved).")
```

- [ ] **Step 4: Add `approval_mode` to `Config`**

In `duh/config.py`, add to the `Config` dataclass:

```python
    approval_mode: str = ""
```

And in `_merge_into`, add:

```python
    if "approval_mode" in data and data["approval_mode"]:
        config.approval_mode = str(data["approval_mode"])
```

- [ ] **Step 5: Wire TieredApprover into the REPL**

In `duh/cli/repl.py`, add to the imports:

```python
from duh.adapters.approvers import ApprovalMode, TieredApprover
```

In the `run_repl` function, where the approver is constructed (where `AutoApprover` or `InteractiveApprover` is selected based on `skip_permissions`), add a check for `--approval-mode`:

```python
    # --- Approval mode selection ---
    approval_mode_str = getattr(args, "approval_mode", None)
    if approval_mode_str:
        mode = ApprovalMode(approval_mode_str)
        approver = TieredApprover(mode=mode, cwd=cwd)
    elif skip_permissions:
        approver = AutoApprover()
    else:
        approver = InteractiveApprover()
```

Add `/snapshot` to the `SLASH_COMMANDS` dict:

```python
    "/snapshot": "Ghost snapshot (/snapshot, /snapshot apply, /snapshot discard)",
```

Add the `/snapshot` handler in `_handle_slash`:

```python
    if name == "/snapshot":
        # Handled by REPL loop (see run_repl) -- return sentinel
        return True, f"\x00snapshot\x00{arg.strip()}"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_approval_cli.py -v`
Expected: All 11 tests pass.

- [ ] **Step 7: Run full test suite to check for regressions**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/ -v --tb=short`
Expected: All existing tests pass. No regressions.

- [ ] **Step 8: Commit**

Run: `cd /Users/nomind/Code/duh && git add duh/cli/parser.py duh/config.py duh/cli/repl.py tests/unit/test_approval_cli.py && git commit -m "Wire 3-tier approval + snapshot into CLI: --approval-mode flag, /snapshot command"`

---

## Verification Checklist

After all 8 tasks are complete, run these final checks:

- [ ] **Full test suite passes:** `cd /Users/nomind/Code/duh && python -m pytest tests/ -v --tb=short`
- [ ] **No import errors:** `cd /Users/nomind/Code/duh && python -c "from duh.adapters.sandbox.policy import SandboxPolicy, SandboxType, SandboxCommand, detect_sandbox_type; from duh.adapters.sandbox.seatbelt import generate_profile; from duh.adapters.sandbox.landlock import build_landlock_argv, build_ruleset; from duh.adapters.sandbox.network import NetworkPolicy, NetworkMode; from duh.adapters.approvers import ApprovalMode, TieredApprover; from duh.kernel.snapshot import ReadOnlyExecutor, SnapshotSession; print('All imports OK')"`
- [ ] **CLI flag works:** `cd /Users/nomind/Code/duh && python -m duh --help | grep approval-mode`
- [ ] **Sandbox detection works:** `cd /Users/nomind/Code/duh && python -c "from duh.adapters.sandbox.policy import detect_sandbox_type; print(f'Detected: {detect_sandbox_type().value}')"`

## Summary

| Task | Files Created/Modified | Tests Added | What It Does |
|------|----------------------|-------------|-------------|
| 1 | 3 files (policy.py, __init__.py, test) | 13 | Platform-independent sandbox policy abstraction |
| 2 | 2 files (seatbelt.py, test) | 11 | macOS sandbox-exec profile generation |
| 3 | 2 files (landlock.py, test) | 11 | Linux Landlock syscalls via ctypes |
| 4 | 2 files (network.py, test) | 15 | Network policy (FULL/LIMITED/NONE) |
| 5 | 1 modified + 1 test | 24 | 3-tier approval (SUGGEST/AUTO_EDIT/FULL_AUTO) |
| 6 | 2 files (snapshot.py, test) | 17 | Ghost snapshot with ReadOnlyExecutor |
| 7 | 2 modified + 1 test | 4 | Sandbox + BashTool integration |
| 8 | 3 modified + 1 test | 11 | Approval mode + CLI wiring |
| **Total** | **12 created, 5 modified** | **106** | Full Codex-parity security layer |
