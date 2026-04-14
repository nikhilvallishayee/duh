"""Hook system -- lifecycle event hooks for D.U.H.

Hooks let users run shell commands or Python callbacks at key lifecycle
points (before/after tool use, session start/end, etc.).

Design principles (see ADR-013):
- Data-driven dispatch: one ``execute_hooks()`` function handles all events.
- Two hook types: shell commands and Python callables.
- Error isolation: one hook failing does not prevent others from running.
- No per-event boilerplate functions.

Usage::

    registry = HookRegistry()
    registry.register(HookConfig(
        event=HookEvent.PRE_TOOL_USE,
        hook_type=HookType.COMMAND,
        command="echo 'before tool'",
        matcher="Bash",
    ))
    results = await execute_hooks(
        registry, HookEvent.PRE_TOOL_USE,
        {"tool_name": "Bash", "input": {"command": "ls"}},
        matcher_value="Bash",
    )
"""

from __future__ import annotations

import asyncio
import builtins
import fnmatch
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class HookEvent(str, Enum):
    """Lifecycle events that can trigger hooks.

    Original 6 events from Phase 1, plus 22 new events added in Phase 2
    to match the Claude Code TS hook surface area.
    """

    # --- Original 6 ---
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    NOTIFICATION = "Notification"
    STOP = "Stop"

    # --- Phase 2: 22 new events ---
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    CONFIG_CHANGE = "ConfigChange"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"
    STATUS_LINE = "StatusLine"
    FILE_SUGGESTION = "FileSuggestion"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    SETUP = "Setup"
    TEAMMATE_IDLE = "TeammateIdle"

    # --- Phase 7: LLM security hardening events ---
    AUDIT = "audit"  # PEP 578 audit hook bridge (ADR-054, 7.5)


class HookType(str, Enum):
    """How a hook is executed."""

    COMMAND = "command"  # Shell subprocess
    FUNCTION = "function"  # Python callable


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

# Callback signature: (event, data) -> HookResult
HookCallback = Callable[
    [HookEvent, dict[str, Any]], Awaitable["HookResult"] | "HookResult"
]


@dataclass
class HookConfig:
    """Configuration for a single hook."""

    event: HookEvent
    hook_type: HookType
    name: str = ""
    matcher: str = ""  # Empty = match all
    command: str = ""  # For COMMAND hooks
    callback: HookCallback | None = None  # For FUNCTION hooks
    timeout: float = 30.0  # Seconds


@dataclass
class HookResult:
    """Result of executing a single hook."""

    hook_name: str
    success: bool
    output: str = ""
    error: str = ""
    exit_code: int | None = None


@dataclass
class HookResponse:
    """Parsed response from a blocking hook.

    Hooks can return JSON on stdout with these fields:
    - decision: "continue" (default) | "block" | "allow"
    - suppress_output: bool (default False) -- suppress tool output from model
    - message: str -- explanation for block/allow decision
    """

    decision: str = "continue"  # "continue" | "block" | "allow"
    suppress_output: bool = False
    message: str = ""

    @classmethod
    def from_json(cls, raw: str) -> "HookResponse":
        """Parse a HookResponse from JSON string.

        Falls back to continue on parse error.
        """
        if not raw or not raw.strip():
            return cls()
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return cls()
            return cls(
                decision=data.get("decision", "continue"),
                suppress_output=data.get("suppress_output", False),
                message=data.get("message", ""),
            )
        except (json.JSONDecodeError, TypeError):
            return cls()


# ---------------------------------------------------------------------------
# Glob matching helper
# ---------------------------------------------------------------------------


def _glob_match(pattern: str, value: str) -> bool:
    """Match a value against a glob pattern.

    Empty pattern matches everything. Supports *, ?, [seq] via fnmatch.
    """
    if not pattern:
        return True
    return fnmatch.fnmatch(value, pattern)


# ---------------------------------------------------------------------------
# Hook executors (one per hook type)
# ---------------------------------------------------------------------------


async def _execute_command_hook(
    hook: HookConfig,
    event: HookEvent,
    data: dict[str, Any],
    timeout: float,
) -> HookResult:
    """Execute a shell command hook.

    The hook's JSON input is passed via stdin. Stdout and stderr are captured.
    Exit code 0 = success, non-zero = error.
    The *event* parameter is accepted (but unused) so the signature matches
    the dispatch table shared with _execute_function_hook.
    """
    name = hook.name or hook.command
    json_input = json.dumps(data)

    try:
        env = dict(os.environ)
        env["TOOL_NAME"] = str(data.get("tool_name", ""))
        env["TOOL_INPUT"] = json.dumps(data.get("input", {}), default=str)
        env["SESSION_ID"] = str(data.get("session_id", ""))

        proc = await asyncio.create_subprocess_shell(
            hook.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=json_input.encode()),
            timeout=timeout,
        )
        stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        exit_code = proc.returncode or 0

        return HookResult(
            hook_name=name,
            success=(exit_code == 0),
            output=stdout,
            error=stderr,
            exit_code=exit_code,
        )

    except asyncio.TimeoutError:
        # Kill the process on timeout
        try:
            proc.kill()  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return HookResult(
            hook_name=name,
            success=False,
            error=f"Hook timed out after {timeout}s",
            exit_code=-1,
        )
    except Exception as exc:
        return HookResult(
            hook_name=name,
            success=False,
            error=str(exc),
            exit_code=-1,
        )


async def _execute_function_hook(
    hook: HookConfig,
    event: HookEvent,
    data: dict[str, Any],
    timeout: float,
) -> HookResult:
    """Execute a Python callback hook."""
    name = hook.name or "<function>"
    if hook.callback is None:
        return HookResult(
            hook_name=name,
            success=False,
            error="Function hook has no callback",
        )

    try:
        result = hook.callback(event, data)
        # Support both sync and async callbacks
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            result = await asyncio.wait_for(result, timeout=timeout)

        if isinstance(result, HookResult):
            return result

        # If callback returns something else, wrap it
        return HookResult(
            hook_name=name,
            success=True,
            output=str(result) if result is not None else "",
        )

    except asyncio.TimeoutError:
        return HookResult(
            hook_name=name,
            success=False,
            error=f"Function hook timed out after {timeout}s",
        )
    except Exception as exc:
        return HookResult(
            hook_name=name,
            success=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Executor dispatch table (one entry per HookType)
# ---------------------------------------------------------------------------

_EXECUTORS = {
    HookType.COMMAND: _execute_command_hook,
    HookType.FUNCTION: _execute_function_hook,
}


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------


class HookRegistry:
    """Registry of hooks, organized by event.

    Hooks are stored in registration order and executed sequentially.
    """

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookConfig]] = {}

    def register(self, hook: HookConfig) -> None:
        """Register a hook for its event."""
        self._hooks.setdefault(hook.event, []).append(hook)

    def unregister(self, hook: HookConfig) -> bool:
        """Remove a hook. Returns True if found and removed."""
        hooks = self._hooks.get(hook.event, [])
        try:
            hooks.remove(hook)
            return True
        except ValueError:
            return False

    def get_hooks(
        self,
        event: HookEvent,
        *,
        matcher_value: str | None = None,
    ) -> list[HookConfig]:
        """Get all hooks for an event, optionally filtered by matcher.

        A hook matches if:
        - Its matcher is empty (matches everything), OR
        - Its matcher equals the matcher_value
        """
        hooks = self._hooks.get(event, [])
        if matcher_value is None:
            return list(hooks)
        return [
            h
            for h in hooks
            if not h.matcher or _glob_match(h.matcher, matcher_value)
        ]

    def list_all(self) -> list[HookConfig]:
        """Return all registered hooks across all events."""
        result: list[HookConfig] = []
        for hooks in self._hooks.values():
            result.extend(hooks)
        return result

    def clear(self) -> None:
        """Remove all registered hooks."""
        self._hooks.clear()

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "HookRegistry":
        """Build a registry from a config dict.

        Expected format::

            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "echo hi"}
                            ]
                        }
                    ]
                }
            }
        """
        registry = cls()
        hooks_config = config.get("hooks", {})

        for event_name, matchers in hooks_config.items():
            try:
                event = HookEvent(event_name)
            except ValueError:
                logger.warning("Unknown hook event: %s", event_name)
                continue

            for matcher_block in matchers:
                matcher = matcher_block.get("matcher", "")
                for hook_def in matcher_block.get("hooks", []):
                    hook_type_str = hook_def.get("type", "command")
                    try:
                        hook_type = HookType(hook_type_str)
                    except ValueError:
                        logger.warning("Unknown hook type: %s", hook_type_str)
                        continue

                    hook = HookConfig(
                        event=event,
                        hook_type=hook_type,
                        name=hook_def.get("name", ""),
                        matcher=matcher,
                        command=hook_def.get("command", ""),
                        timeout=hook_def.get("timeout", 30.0),
                    )
                    registry.register(hook)

        return registry


# ---------------------------------------------------------------------------
# Main dispatch function -- the ONE function that handles all events
# ---------------------------------------------------------------------------


async def execute_hooks(
    registry: HookRegistry,
    event: HookEvent,
    data: dict[str, Any],
    *,
    matcher_value: str | None = None,
    timeout: float | None = None,
) -> list[HookResult]:
    """Execute all hooks registered for an event.

    Args:
        registry: The hook registry to query.
        event: The lifecycle event that triggered.
        data: Event-specific data dict (passed to hooks as JSON/args).
        matcher_value: Optional matcher to filter hooks (e.g., tool name).
        timeout: Override per-hook timeout (uses hook's own timeout if None).

    Returns:
        List of HookResult, one per executed hook. All hooks run even if
        earlier ones fail (error isolation).
    """
    hooks = registry.get_hooks(event, matcher_value=matcher_value)
    if not hooks:
        return []

    results: list[HookResult] = []

    for hook in hooks:
        effective_timeout = timeout if timeout is not None else hook.timeout
        executor = _EXECUTORS.get(hook.hook_type)

        if executor is None:
            results.append(
                HookResult(
                    hook_name=hook.name or str(hook.hook_type),
                    success=False,
                    error=f"No executor for hook type: {hook.hook_type}",
                )
            )
            continue

        result = await executor(hook, event, data, effective_timeout)
        results.append(result)
        logger.debug(
            "Hook %s for %s: %s",
            result.hook_name,
            event.value,
            "OK" if result.success else f"FAIL: {result.error}",
        )

    return results


# ---------------------------------------------------------------------------
# Blocking hook execution -- aggregate hook responses for veto semantics
# ---------------------------------------------------------------------------


async def execute_hooks_with_blocking(
    registry: HookRegistry,
    event: HookEvent,
    data: dict[str, Any],
    *,
    matcher_value: str | None = None,
    timeout: float | None = None,
) -> HookResponse:
    """Execute hooks and aggregate blocking decisions.

    If any hook returns decision="block", the overall response is "block".
    If all hooks return "continue" or "allow", the response is "continue"
    or "allow" (first explicit allow wins).

    Returns a HookResponse with the aggregate decision.
    """
    results = await execute_hooks(
        registry, event, data,
        matcher_value=matcher_value, timeout=timeout,
    )

    if not results:
        return HookResponse(decision="continue")

    # Check for any block decision
    for result in results:
        if result.success and result.output:
            parsed = HookResponse.from_json(result.output)
            if parsed.decision == "block":
                return parsed

    # Check for explicit allow
    for result in results:
        if result.success and result.output:
            parsed = HookResponse.from_json(result.output)
            if parsed.decision == "allow":
                return parsed

    return HookResponse(decision="continue")


# ---------------------------------------------------------------------------
# Per-hook filesystem namespacing (ADR-054, Workstream 7.4)
# ---------------------------------------------------------------------------


class HookFSViolation(PermissionError):
    """Raised when a hook accesses files outside its namespace."""


@dataclass
class HookContext:
    """Per-hook runtime context with a private filesystem namespace.

    Each hook receives a unique ``tmp_dir`` that only it may write to.
    Reads outside ``tmp_dir`` must be explicitly whitelisted in
    ``allowed_read``.  Use ``ctx.open()`` instead of the built-in open
    to enforce these constraints.
    """

    hook_name: str
    tmp_dir: Path = field(init=False)
    allowed_read: frozenset[Path] = field(default_factory=frozenset)
    allowed_write: frozenset[Path] = field(init=False)

    def __post_init__(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix=f"duh-hook-{self.hook_name}-"))
        self.allowed_write = frozenset({self.tmp_dir})

    def cleanup(self) -> None:
        """Remove the private temp directory."""
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)

    def open(self, path: "str | Path", mode: str = "r"):
        """Namespace-enforced open.

        Writes are only allowed inside ``tmp_dir`` (or other paths in
        ``allowed_write``).  Reads are allowed inside ``tmp_dir`` or any
        path in ``allowed_read``.
        """
        resolved = Path(path).resolve()
        if "w" in mode or "a" in mode or "+" in mode:
            if not any(
                resolved == w or resolved.is_relative_to(w)
                for w in self.allowed_write
            ):
                raise HookFSViolation(
                    f"hook '{self.hook_name}' wrote outside namespace: {resolved}"
                )
        else:
            all_readable = self.allowed_read | self.allowed_write
            if not any(
                resolved == r or resolved.is_relative_to(r)
                for r in all_readable
            ):
                raise HookFSViolation(
                    f"hook '{self.hook_name}' read outside namespace: {resolved}"
                )
        return builtins.open(resolved, mode)


class HookContextRegistry:
    """Tracks all active HookContexts for bulk cleanup at SESSION_END."""

    def __init__(self) -> None:
        self._contexts: list[HookContext] = []

    def create(self, hook_name: str) -> HookContext:
        """Create a new HookContext and track it for cleanup."""
        ctx = HookContext(hook_name=hook_name)
        self._contexts.append(ctx)
        return ctx

    def cleanup_all(self) -> None:
        """Remove all tracked temp directories (call at SESSION_END)."""
        for ctx in self._contexts:
            ctx.cleanup()
        self._contexts.clear()
