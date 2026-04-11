# Phase 1: Quick Wins — Safety Hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 high-impact safety features to duh that require low-to-medium effort — env var allowlist, large file caps, graceful shutdown, PTL retry, MCP session expiry detection, and a QueryGuard state machine.

**Architecture:** Each feature is a focused addition to an existing module. No new ports or adapters needed. All changes are backward-compatible. Features are independent — any can be skipped without breaking others.

**Tech Stack:** Python 3.12+, asyncio, signal module, dataclasses. No new dependencies.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `duh/tools/bash_security.py` | Add env var allowlist + binary hijack detection |
| Modify | `duh/tools/read.py` | Add MAX_FILE_READ_BYTES cap |
| Modify | `duh/tools/write.py` | Add MAX_FILE_WRITE_BYTES cap |
| Modify | `duh/adapters/file_store.py` | Add MAX_SESSION_BYTES cap |
| Create | `duh/kernel/signals.py` | Graceful shutdown signal handler |
| Modify | `duh/kernel/engine.py` | PTL retry logic + signal integration |
| Modify | `duh/adapters/mcp_executor.py` | Session expiry detection + reconnect |
| Create | `duh/kernel/query_guard.py` | Concurrent query state machine |
| Modify | `duh/cli/repl.py` | Wire QueryGuard into REPL loop |
| Create | `tests/unit/test_env_var_allowlist.py` | Tests for env var security |
| Create | `tests/unit/test_file_caps.py` | Tests for file size limits |
| Create | `tests/unit/test_signals.py` | Tests for graceful shutdown |
| Create | `tests/unit/test_ptl_retry.py` | Tests for prompt-too-long retry |
| Create | `tests/unit/test_mcp_session_expiry.py` | Tests for MCP reconnect |
| Create | `tests/unit/test_query_guard.py` | Tests for QueryGuard FSM |

---

### Task 1: Env Var Allowlist + Binary Hijack Detection

**Files:**
- Modify: `duh/tools/bash_security.py`
- Create: `tests/unit/test_env_var_allowlist.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_env_var_allowlist.py
"""Tests for env var allowlist and binary hijack detection."""

from duh.tools.bash_security import classify_command, is_env_var_safe, BINARY_HIJACK_RE


def test_safe_env_var_known():
    """Well-known safe env vars should be allowed."""
    assert is_env_var_safe("PATH") is True
    assert is_env_var_safe("HOME") is True
    assert is_env_var_safe("TERM") is True
    assert is_env_var_safe("LANG") is True
    assert is_env_var_safe("GOPATH") is True
    assert is_env_var_safe("RUST_LOG") is True
    assert is_env_var_safe("NODE_ENV") is True
    assert is_env_var_safe("PYTHONPATH") is True


def test_unsafe_env_var_hijack():
    """Binary hijack vars must be blocked."""
    assert is_env_var_safe("LD_PRELOAD") is False
    assert is_env_var_safe("LD_LIBRARY_PATH") is False
    assert is_env_var_safe("DYLD_INSERT_LIBRARIES") is False
    assert is_env_var_safe("DYLD_LIBRARY_PATH") is False


def test_binary_hijack_regex():
    """Regex should match LD_* and DYLD_* patterns."""
    assert BINARY_HIJACK_RE.match("LD_PRELOAD")
    assert BINARY_HIJACK_RE.match("LD_LIBRARY_PATH")
    assert BINARY_HIJACK_RE.match("DYLD_INSERT_LIBRARIES")
    assert not BINARY_HIJACK_RE.match("PATH")
    assert not BINARY_HIJACK_RE.match("NODE_ENV")


def test_command_with_env_injection_blocked():
    """Commands setting hijack vars should be flagged dangerous."""
    result = classify_command("LD_PRELOAD=/evil.so ./app")
    assert result["risk"] == "dangerous"
    assert "hijack" in result["reason"].lower() or "LD_PRELOAD" in result["reason"]


def test_command_with_safe_env_allowed():
    """Commands setting safe env vars should pass."""
    result = classify_command("NODE_ENV=production npm start")
    assert result["risk"] != "dangerous"


def test_export_hijack_blocked():
    """export of hijack vars should be flagged."""
    result = classify_command("export LD_PRELOAD=/evil.so")
    assert result["risk"] == "dangerous"


def test_export_safe_allowed():
    """export of safe vars should pass."""
    result = classify_command("export PATH=$PATH:/usr/local/bin")
    assert result["risk"] != "dangerous"


def test_dyld_env_blocked():
    """macOS DYLD injection should be blocked."""
    result = classify_command("DYLD_INSERT_LIBRARIES=/evil.dylib ./app")
    assert result["risk"] == "dangerous"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_env_var_allowlist.py -v`
Expected: FAIL — `is_env_var_safe` and `BINARY_HIJACK_RE` not defined

- [ ] **Step 3: Implement env var allowlist**

Add the following to `duh/tools/bash_security.py` after the existing imports (before `Classification` TypedDict):

```python
# ---------------------------------------------------------------------------
# Env var allowlist + binary hijack detection (ported from Claude Code TS)
# ---------------------------------------------------------------------------

# Vars that are safe to set in shell commands.
# Based on Claude Code's 166-var allowlist, distilled to the most common.
SAFE_ENV_VARS: frozenset[str] = frozenset({
    # Shell basics
    "PATH", "HOME", "USER", "SHELL", "TERM", "LANG", "LC_ALL", "LC_CTYPE",
    "EDITOR", "VISUAL", "PAGER", "COLORTERM", "CLICOLOR", "CLICOLOR_FORCE",
    "NO_COLOR", "FORCE_COLOR", "TERM_PROGRAM", "COLUMNS", "LINES",
    # Build tools
    "CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS", "PKG_CONFIG_PATH",
    "CMAKE_PREFIX_PATH", "MAKEFLAGS", "DESTDIR",
    # Go
    "GOPATH", "GOROOT", "GOBIN", "GOPROXY", "GOFLAGS", "GOEXPERIMENT",
    "CGO_ENABLED", "GOARCH", "GOOS",
    # Rust
    "CARGO_HOME", "RUSTUP_HOME", "RUST_LOG", "RUST_BACKTRACE",
    "RUSTFLAGS", "CARGO_TARGET_DIR",
    # Node.js
    "NODE_ENV", "NODE_OPTIONS", "NODE_PATH", "NPM_CONFIG_PREFIX",
    "YARN_CACHE_FOLDER", "NVM_DIR",
    # Python
    "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
    "VIRTUAL_ENV", "CONDA_PREFIX", "PIP_INDEX_URL",
    # Java / JVM
    "JAVA_HOME", "CLASSPATH", "MAVEN_HOME", "GRADLE_HOME",
    # Ruby
    "GEM_HOME", "GEM_PATH", "BUNDLE_PATH", "RBENV_ROOT",
    # Docker / containers
    "DOCKER_HOST", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME",
    # CI
    "CI", "GITHUB_ACTIONS", "GITLAB_CI", "CIRCLECI", "JENKINS_URL",
    "GITHUB_TOKEN", "GITHUB_REPOSITORY",
    # Git
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL", "GIT_LFS_SKIP_SMUDGE",
    # AWS (non-secret — credentials handled separately)
    "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE",
    "AWS_DEFAULT_OUTPUT",
    # Misc
    "TZ", "DISPLAY", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "TMPDIR", "TEMP", "TMP",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "KUBECONFIG", "ANSIBLE_CONFIG",
})


# Regex matching env vars that enable binary hijacking.
# These allow overriding which shared libraries get loaded — a critical
# attack vector on Unix systems.
BINARY_HIJACK_RE = re.compile(
    r"^(LD_|DYLD_|LIBPATH|SHLIB_PATH|LIB_PATH)"
)


def is_env_var_safe(name: str) -> bool:
    """Check if an environment variable name is safe to set.

    Returns True for known-safe vars, False for hijack vars,
    True for unknown vars (permissive by default — we only block known-bad).
    """
    if BINARY_HIJACK_RE.match(name):
        return False
    return True  # permissive: only block known-dangerous patterns
```

Now add two new dangerous patterns to the `DANGEROUS_PATTERNS` list:

```python
    # -- Binary hijack via env var injection --
    (re.compile(r"\bLD_PRELOAD\s*=|\bexport\s+LD_PRELOAD\b"),
     "Binary hijack via LD_PRELOAD"),
    (re.compile(r"\bDYLD_INSERT_LIBRARIES\s*=|\bexport\s+DYLD_INSERT_LIBRARIES\b"),
     "Binary hijack via DYLD_INSERT_LIBRARIES"),
    (re.compile(r"\bLD_LIBRARY_PATH\s*=.*\.\./|\bexport\s+LD_LIBRARY_PATH\b.*\.\./"),
     "Suspicious LD_LIBRARY_PATH with path traversal"),
    (re.compile(r"\bDYLD_LIBRARY_PATH\s*=|\bexport\s+DYLD_LIBRARY_PATH\b"),
     "Binary hijack via DYLD_LIBRARY_PATH"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_env_var_allowlist.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/tools/bash_security.py tests/unit/test_env_var_allowlist.py
git commit -m "feat(security): add env var allowlist and binary hijack detection"
```

---

### Task 2: Large File Caps

**Files:**
- Modify: `duh/tools/read.py`
- Modify: `duh/tools/write.py`
- Modify: `duh/adapters/file_store.py`
- Modify: `duh/kernel/tool.py`
- Create: `tests/unit/test_file_caps.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_file_caps.py
"""Tests for file size caps across tools."""
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from duh.kernel.tool import ToolContext


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=str(tmp_path))


@pytest.mark.asyncio
async def test_read_rejects_huge_file(tmp_path, ctx):
    """ReadTool should refuse files larger than MAX_FILE_READ_BYTES."""
    from duh.tools.read import ReadTool, MAX_FILE_READ_BYTES

    huge = tmp_path / "huge.bin"
    # Create a file just over the limit using sparse write
    with open(huge, "wb") as f:
        f.seek(MAX_FILE_READ_BYTES + 1)
        f.write(b"x")

    tool = ReadTool()
    result = await tool.call({"file_path": str(huge)}, ctx)
    assert result.is_error
    assert "too large" in result.output.lower() or "exceeds" in result.output.lower()


@pytest.mark.asyncio
async def test_read_accepts_normal_file(tmp_path, ctx):
    """ReadTool should read normal-sized files fine."""
    from duh.tools.read import ReadTool

    normal = tmp_path / "normal.txt"
    normal.write_text("hello\nworld\n")

    tool = ReadTool()
    result = await tool.call({"file_path": str(normal)}, ctx)
    assert not result.is_error
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_write_rejects_huge_content(tmp_path, ctx):
    """WriteTool should refuse content larger than MAX_FILE_WRITE_BYTES."""
    from duh.tools.write import WriteTool, MAX_FILE_WRITE_BYTES

    tool = WriteTool()
    huge_content = "x" * (MAX_FILE_WRITE_BYTES + 1)
    result = await tool.call(
        {"file_path": str(tmp_path / "out.txt"), "content": huge_content},
        ctx,
    )
    assert result.is_error
    assert "too large" in result.output.lower() or "exceeds" in result.output.lower()


@pytest.mark.asyncio
async def test_write_accepts_normal_content(tmp_path, ctx):
    """WriteTool should write normal-sized content fine."""
    from duh.tools.write import WriteTool

    tool = WriteTool()
    result = await tool.call(
        {"file_path": str(tmp_path / "out.txt"), "content": "hello"},
        ctx,
    )
    assert not result.is_error


def test_session_store_cap(tmp_path):
    """FileStore should refuse sessions larger than MAX_SESSION_BYTES."""
    from duh.adapters.file_store import FileStore, MAX_SESSION_BYTES

    assert MAX_SESSION_BYTES == 64 * 1024 * 1024  # 64 MB
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_file_caps.py -v`
Expected: FAIL — `MAX_FILE_READ_BYTES` not defined

- [ ] **Step 3: Implement file size caps**

Add to `duh/tools/read.py` after the existing imports:

```python
# Maximum file size for reading (50 MB). Files larger than this should
# be read with offset/limit. Prevents OOM on binary blobs.
MAX_FILE_READ_BYTES = 50 * 1024 * 1024  # 50 MB
```

Add early size check inside `ReadTool.call()`, after the `os.access` check but before the notebook check:

```python
        # --- File size cap ---
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0
        if file_size > MAX_FILE_READ_BYTES:
            return ToolResult(
                output=(
                    f"File too large ({file_size:,} bytes, limit {MAX_FILE_READ_BYTES:,})."
                    " Use offset and limit to read sections."
                ),
                is_error=True,
            )
```

Add to `duh/tools/write.py` after imports:

```python
# Maximum content size for writing (50 MB).
MAX_FILE_WRITE_BYTES = 50 * 1024 * 1024  # 50 MB
```

Add early check in `WriteTool.call()` before writing:

```python
        content = input.get("content", "")
        if len(content.encode("utf-8", errors="replace")) > MAX_FILE_WRITE_BYTES:
            return ToolResult(
                output=(
                    f"Content too large ({len(content):,} chars, limit ~{MAX_FILE_WRITE_BYTES // 1024 // 1024}MB)."
                    " Split into smaller writes."
                ),
                is_error=True,
            )
```

Add to `duh/adapters/file_store.py` after imports:

```python
# Maximum session file size (64 MB). Matches Claude Code TS MAX_PERSISTED_SIZE.
MAX_SESSION_BYTES = 64 * 1024 * 1024
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_file_caps.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/tools/read.py duh/tools/write.py duh/adapters/file_store.py tests/unit/test_file_caps.py
git commit -m "feat(safety): add large file caps (50MB read/write, 64MB session)"
```

---

### Task 3: Graceful Shutdown (Signal Handling)

**Files:**
- Create: `duh/kernel/signals.py`
- Modify: `duh/cli/repl.py` (wire in)
- Create: `tests/unit/test_signals.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_signals.py
"""Tests for graceful shutdown signal handling."""
import asyncio

import pytest

from duh.kernel.signals import ShutdownHandler


@pytest.mark.asyncio
async def test_shutdown_handler_not_triggered_by_default():
    handler = ShutdownHandler()
    assert not handler.shutting_down


@pytest.mark.asyncio
async def test_shutdown_handler_trigger():
    handler = ShutdownHandler()
    handler.trigger()
    assert handler.shutting_down


@pytest.mark.asyncio
async def test_shutdown_runs_callbacks():
    results = []

    async def cleanup1():
        results.append("c1")

    async def cleanup2():
        results.append("c2")

    handler = ShutdownHandler(timeout=5.0)
    handler.on_shutdown(cleanup1)
    handler.on_shutdown(cleanup2)
    await handler.run_cleanup()
    assert results == ["c1", "c2"]


@pytest.mark.asyncio
async def test_shutdown_callback_timeout():
    """Callbacks that exceed timeout should not block shutdown."""
    async def slow():
        await asyncio.sleep(100)

    handler = ShutdownHandler(timeout=0.1)
    handler.on_shutdown(slow)
    await handler.run_cleanup()  # Should complete within ~0.1s, not hang


@pytest.mark.asyncio
async def test_shutdown_callback_error_isolation():
    """One failing callback should not prevent others from running."""
    results = []

    async def fail():
        raise RuntimeError("boom")

    async def succeed():
        results.append("ok")

    handler = ShutdownHandler(timeout=5.0)
    handler.on_shutdown(fail)
    handler.on_shutdown(succeed)
    await handler.run_cleanup()
    assert results == ["ok"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_signals.py -v`
Expected: FAIL — `duh.kernel.signals` not found

- [ ] **Step 3: Implement ShutdownHandler**

```python
# duh/kernel/signals.py
"""Graceful shutdown — signal handling and cleanup coordination.

Registers SIGTERM/SIGINT handlers that trigger cleanup callbacks before exit.
Callbacks run with a timeout to prevent hanging on exit.

Usage:
    handler = ShutdownHandler()
    handler.on_shutdown(my_cleanup_fn)
    handler.install()  # registers signal handlers
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# Default timeout for cleanup callbacks (matches Claude Code's 1.5s session end)
DEFAULT_SHUTDOWN_TIMEOUT = 1.5


class ShutdownHandler:
    """Coordinates graceful shutdown with timeout-bounded cleanup."""

    def __init__(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT):
        self._timeout = timeout
        self._callbacks: list[Callable[[], Awaitable[None]]] = []
        self._shutting_down = False

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    def trigger(self) -> None:
        """Mark shutdown as in progress."""
        self._shutting_down = True

    def on_shutdown(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register a cleanup callback. Runs in registration order."""
        self._callbacks.append(callback)

    async def run_cleanup(self) -> None:
        """Run all cleanup callbacks with timeout. Errors are isolated."""
        self._shutting_down = True
        for cb in self._callbacks:
            try:
                await asyncio.wait_for(cb(), timeout=self._timeout)
            except asyncio.TimeoutError:
                logger.warning("Shutdown callback %s timed out", cb.__name__)
            except Exception:
                logger.warning("Shutdown callback %s failed", cb.__name__, exc_info=True)

    def install(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Install SIGTERM and SIGINT handlers on the event loop.

        Safe to call from within a running loop. On non-Unix systems
        (Windows), falls back to signal.signal().
        """
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # No loop running — skip signal installation

        def _handle_signal(sig: signal.Signals) -> None:
            logger.info("Received %s, shutting down...", sig.name)
            self.trigger()
            # Schedule cleanup as a task so it runs in the event loop
            loop.create_task(self._shutdown_and_exit(sig))

        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            # Windows: add_signal_handler not supported
            pass

    async def _shutdown_and_exit(self, sig: signal.Signals) -> None:
        """Run cleanup then exit."""
        await self.run_cleanup()
        raise SystemExit(128 + sig.value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_signals.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/kernel/signals.py tests/unit/test_signals.py
git commit -m "feat(safety): add graceful shutdown handler with timeout-bounded cleanup"
```

---

### Task 4: PTL (Prompt-Too-Long) Retry

**Files:**
- Modify: `duh/kernel/engine.py`
- Create: `tests/unit/test_ptl_retry.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ptl_retry.py
"""Tests for prompt-too-long retry in Engine."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from duh.kernel.engine import Engine, EngineConfig, _is_ptl_error, MAX_PTL_RETRIES
from duh.kernel.deps import Deps
from duh.kernel.messages import Message


def test_ptl_error_detection():
    assert _is_ptl_error("prompt is too long: 200000 tokens > 100000 maximum")
    assert _is_ptl_error("PromptTooLong")
    assert _is_ptl_error("prompt_too_long")
    assert _is_ptl_error("context length exceeded")
    assert not _is_ptl_error("rate_limit_exceeded")
    assert not _is_ptl_error("invalid_api_key")


def test_max_ptl_retries_constant():
    assert MAX_PTL_RETRIES == 3


@pytest.mark.asyncio
async def test_engine_ptl_triggers_compact_and_retry():
    """When a PTL error occurs, engine should compact and retry."""
    call_count = 0

    async def mock_call(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("prompt is too long: 200000 tokens > 100000 maximum")
        yield {"type": "assistant", "message": Message(role="assistant", content="ok")}
        yield {"type": "done", "stop_reason": "end_turn"}

    compact_called = False
    original_messages = None

    async def mock_compact(messages, token_limit=0):
        nonlocal compact_called, original_messages
        compact_called = True
        original_messages = len(messages)
        # Return a shortened version
        return messages[-2:] if len(messages) > 2 else messages

    deps = Deps(
        call_model=mock_call,
        compact=mock_compact,
    )
    engine = Engine(deps=deps, config=EngineConfig(model="test"))

    events = []
    async for event in engine.run("hello"):
        events.append(event)

    assert compact_called
    assert call_count == 2  # first fails, second succeeds
    assert any(e.get("type") == "done" for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_ptl_retry.py -v`
Expected: FAIL — `_is_ptl_error` and `MAX_PTL_RETRIES` not defined

- [ ] **Step 3: Implement PTL retry in engine.py**

Add constants and helper to `duh/kernel/engine.py` after the existing `_FALLBACK_TRIGGERS`:

```python
MAX_PTL_RETRIES = 3

_PTL_TRIGGERS = ("prompt is too long", "prompt_too_long", "prompttoolong", "context length exceeded")


def _is_ptl_error(error_text: str) -> bool:
    """Return True if error_text indicates a prompt-too-long condition."""
    lower = error_text.lower()
    return any(trigger in lower for trigger in _PTL_TRIGGERS)
```

Modify the `Engine.run()` method. Wrap the query loop call in a PTL retry loop. Replace the existing `async for event in query(...)` block with:

```python
        # --- Query with PTL retry ---
        ptl_retries = 0
        while True:
            try:
                async for event in query(
                    messages=self._messages,
                    system_prompt=self._config.system_prompt,
                    deps=self._deps,
                    tools=self._config.tools,
                    max_turns=max_turns or self._config.max_turns,
                    model=effective_model,
                    thinking=self._config.thinking,
                    tool_choice=self._config.tool_choice,
                ):
                    event_type = event.get("type", "")

                    if event_type == "assistant":
                        msg = event.get("message")
                        if isinstance(msg, Message):
                            self._messages.append(msg)
                            self._total_output_tokens += count_tokens(msg.text)
                        if self._slog:
                            self._slog.model_response(model=effective_model, turn=self._turn_count)

                    if self._slog:
                        if event_type == "tool_use":
                            self._slog.tool_call(
                                name=event.get("name", ""),
                                input=event.get("input"),
                            )
                        elif event_type == "tool_result":
                            self._slog.tool_result(
                                name=event.get("name", ""),
                                output=str(event.get("output", "")),
                                is_error=event.get("is_error", False),
                            )
                        elif event_type == "error":
                            self._slog.error(error=event.get("error", ""))

                    if fallback_model and event_type == "error":
                        error_text = event.get("error", "")
                        if _is_fallback_error(error_text):
                            should_fallback = True
                            continue

                    yield event

                    if event_type == "done":
                        budget_events = self._check_budget(effective_model)
                        for be in budget_events:
                            yield be
                        if any(be["type"] == "budget_exceeded" for be in budget_events):
                            return

                    if event_type == "done" and self._session_store:
                        try:
                            await self._session_store.save(
                                self._session_id, self._messages,
                            )
                        except Exception:
                            logger.debug("Session auto-save failed", exc_info=True)

                break  # Query completed normally

            except Exception as exc:
                if _is_ptl_error(str(exc)) and ptl_retries < MAX_PTL_RETRIES and self._deps.compact:
                    ptl_retries += 1
                    logger.info(
                        "Prompt too long (retry %d/%d), compacting...",
                        ptl_retries, MAX_PTL_RETRIES,
                    )
                    context_limit = get_context_limit(effective_model)
                    # Compact to 70% of limit to leave headroom
                    target = int(context_limit * 0.70)
                    self._messages = await self._deps.compact(
                        self._messages, token_limit=target,
                    )
                    continue  # retry the query
                raise  # re-raise non-PTL errors
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_ptl_retry.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing engine tests to verify no regressions**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_engine.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/kernel/engine.py tests/unit/test_ptl_retry.py
git commit -m "feat(safety): add prompt-too-long retry with auto-compaction (max 3 retries)"
```

---

### Task 5: MCP Session Expiry Detection + Reconnect

**Files:**
- Modify: `duh/adapters/mcp_executor.py`
- Create: `tests/unit/test_mcp_session_expiry.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mcp_session_expiry.py
"""Tests for MCP session expiry detection and reconnection."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from duh.adapters.mcp_executor import (
    MCPExecutor,
    MCPServerConfig,
    MCPConnection,
    MCPToolInfo,
    _is_session_expired,
    MAX_SESSION_RETRIES,
    MAX_ERRORS_BEFORE_RECONNECT,
)


def test_session_expiry_detection():
    assert _is_session_expired(404, "Session not found")
    assert _is_session_expired(404, "session not found: abc-123")
    assert not _is_session_expired(200, "OK")
    assert not _is_session_expired(500, "Internal server error")
    assert not _is_session_expired(404, "Tool not found")


def test_constants():
    assert MAX_SESSION_RETRIES == 1
    assert MAX_ERRORS_BEFORE_RECONNECT == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_session_expiry.py -v`
Expected: FAIL — `_is_session_expired` not defined

- [ ] **Step 3: Implement session expiry detection**

Add to `duh/adapters/mcp_executor.py` after the existing constants:

```python
# Session management constants (from Claude Code TS)
MAX_SESSION_RETRIES = 1
MAX_ERRORS_BEFORE_RECONNECT = 3


def _is_session_expired(status_code: int, message: str) -> bool:
    """Detect MCP session expiry from error response."""
    return status_code == 404 and "session not found" in message.lower()
```

Add an error counter and reconnect logic to `MCPExecutor`. Add to `__init__`:

```python
        self._error_counts: dict[str, int] = {}  # server_name -> consecutive errors
```

Modify `MCPExecutor.run()` to wrap the call_tool with retry logic:

```python
    async def run(
        self,
        tool_name: str,
        input: dict[str, Any],
        *,
        tool_use_id: str = "",
        context: Any = None,
    ) -> str | dict[str, Any]:
        info = self._tool_index.get(tool_name)
        if info is None:
            raise KeyError(f"MCP tool not found: {tool_name}")

        conn = self._connections.get(info.server_name)
        if conn is None or conn.session is None:
            raise RuntimeError(
                f"MCP server '{info.server_name}' is not connected"
            )

        retries = 0
        while True:
            try:
                result = await conn.session.call_tool(info.name, arguments=input)
                # Reset error counter on success
                self._error_counts[info.server_name] = 0
                break
            except Exception as exc:
                exc_str = str(exc)
                status = getattr(exc, "status_code", getattr(exc, "code", 0))

                # Track consecutive errors
                count = self._error_counts.get(info.server_name, 0) + 1
                self._error_counts[info.server_name] = count

                # Session expiry: reconnect and retry once
                if (_is_session_expired(status, exc_str)
                        and retries < MAX_SESSION_RETRIES):
                    retries += 1
                    logger.info(
                        "MCP session expired for %s, reconnecting...",
                        info.server_name,
                    )
                    await self.disconnect(info.server_name)
                    await self.connect(info.server_name)
                    conn = self._connections.get(info.server_name)
                    if conn is None or conn.session is None:
                        raise RuntimeError(
                            f"Reconnection to '{info.server_name}' failed"
                        ) from exc
                    continue

                # Too many consecutive errors: reconnect
                if count >= MAX_ERRORS_BEFORE_RECONNECT:
                    logger.warning(
                        "MCP server %s: %d consecutive errors, reconnecting",
                        info.server_name, count,
                    )
                    self._error_counts[info.server_name] = 0
                    await self.disconnect(info.server_name)
                    try:
                        await self.connect(info.server_name)
                    except Exception:
                        pass  # reconnect is best-effort

                raise RuntimeError(
                    f"MCP tool call failed ({tool_name}): {exc}"
                ) from exc

        # Extract content from MCP result
        if hasattr(result, "content") and result.content:
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "data"):
                    parts.append(str(block.data))
                else:
                    parts.append(str(block))
            return "\n".join(parts) if parts else ""

        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_session_expiry.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing MCP tests**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_executor.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/adapters/mcp_executor.py tests/unit/test_mcp_session_expiry.py
git commit -m "feat(mcp): add session expiry detection and auto-reconnect"
```

---

### Task 6: QueryGuard State Machine

**Files:**
- Create: `duh/kernel/query_guard.py`
- Create: `tests/unit/test_query_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_query_guard.py
"""Tests for QueryGuard concurrent query state machine."""
import pytest
from duh.kernel.query_guard import QueryGuard, QueryState


def test_initial_state():
    guard = QueryGuard()
    assert guard.state == QueryState.IDLE
    assert guard.generation == 0


def test_reserve():
    guard = QueryGuard()
    gen = guard.reserve()
    assert gen == 1
    assert guard.state == QueryState.DISPATCHING


def test_reserve_while_busy():
    guard = QueryGuard()
    guard.reserve()
    with pytest.raises(RuntimeError, match="not idle"):
        guard.reserve()


def test_try_start():
    guard = QueryGuard()
    gen = guard.reserve()
    result = guard.try_start(gen)
    assert result == gen
    assert guard.state == QueryState.RUNNING


def test_try_start_stale_generation():
    guard = QueryGuard()
    gen = guard.reserve()
    guard.force_end()  # bumps generation
    assert guard.try_start(gen) is None


def test_end():
    guard = QueryGuard()
    gen = guard.reserve()
    guard.try_start(gen)
    assert guard.end(gen) is True
    assert guard.state == QueryState.IDLE


def test_end_stale_generation():
    guard = QueryGuard()
    gen = guard.reserve()
    guard.try_start(gen)
    guard.force_end()
    assert guard.end(gen) is False


def test_force_end():
    guard = QueryGuard()
    guard.reserve()
    guard.force_end()
    assert guard.state == QueryState.IDLE
    assert guard.generation == 2  # reserve bumped to 1, force_end bumps to 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_query_guard.py -v`
Expected: FAIL — `duh.kernel.query_guard` not found

- [ ] **Step 3: Implement QueryGuard**

```python
# duh/kernel/query_guard.py
"""QueryGuard — concurrent query state machine.

Prevents race conditions where multiple queries run simultaneously.
Ported from Claude Code TS's QueryGuard pattern.

State transitions:
    IDLE → DISPATCHING (reserve)
    DISPATCHING → RUNNING (try_start)
    RUNNING → IDLE (end)
    ANY → IDLE (force_end)

Each transition is generation-tracked. Stale generations are rejected,
preventing callbacks from a cancelled query from affecting a new one.

Usage:
    guard = QueryGuard()
    gen = guard.reserve()          # IDLE → DISPATCHING
    if guard.try_start(gen):       # DISPATCHING → RUNNING
        try:
            await do_query()
        finally:
            guard.end(gen)         # RUNNING → IDLE
"""

from __future__ import annotations

from enum import Enum


class QueryState(str, Enum):
    IDLE = "idle"
    DISPATCHING = "dispatching"
    RUNNING = "running"


class QueryGuard:
    """Thread-safe state machine for concurrent query prevention."""

    def __init__(self) -> None:
        self._state = QueryState.IDLE
        self._generation = 0

    @property
    def state(self) -> QueryState:
        return self._state

    @property
    def generation(self) -> int:
        return self._generation

    def reserve(self) -> int:
        """Reserve a slot for a new query. Returns generation number.

        Raises RuntimeError if not idle.
        """
        if self._state != QueryState.IDLE:
            raise RuntimeError(
                f"Cannot reserve: state is {self._state.value}, not idle"
            )
        self._generation += 1
        self._state = QueryState.DISPATCHING
        return self._generation

    def try_start(self, gen: int) -> int | None:
        """Transition from dispatching to running.

        Returns gen if successful, None if generation is stale.
        """
        if gen != self._generation:
            return None
        if self._state != QueryState.DISPATCHING:
            return None
        self._state = QueryState.RUNNING
        return gen

    def end(self, gen: int) -> bool:
        """Transition from running to idle.

        Returns True if successful, False if generation is stale.
        """
        if gen != self._generation:
            return False
        self._state = QueryState.IDLE
        return True

    def force_end(self) -> None:
        """Force transition to idle regardless of current state.

        Bumps generation to invalidate any in-flight callbacks.
        """
        self._generation += 1
        self._state = QueryState.IDLE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_query_guard.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/kernel/query_guard.py tests/unit/test_query_guard.py
git commit -m "feat(safety): add QueryGuard concurrent query state machine"
```

---

### Task 7: Run Full Test Suite

- [ ] **Step 1: Run all tests**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/ -v --tb=short`
Expected: All existing tests still PASS, all new tests PASS

- [ ] **Step 2: Fix any regressions**

If any existing tests break, fix the issue before proceeding.

- [ ] **Step 3: Final commit**

```bash
cd /Users/nomind/Code/duh
git add -A
git commit -m "chore: phase 1 complete — quick wins safety hardening"
```
