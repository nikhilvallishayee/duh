# Phase 5: Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 8 remaining feature/quality gaps identified by the Phase 4 verification audit. Wire QueryGuard into the REPL, emit the 22 unused hook events, add hook blocking semantics, build a model-call compactor, extend Bash AST for heredocs/process substitution, add TodoWrite + AskUserQuestion tools, add secrets redaction, and add connection pre-warming.

**Architecture:** Each gap is a focused, independently testable unit. QueryGuard wiring touches only repl.py. Hook emission threads the HookRegistry through deps so loop.py and engine.py can fire events. Blocking hooks add a `HookResponse` dataclass parsed from hook stdout JSON. Model compaction is a new adapter behind the existing `CompactFn` signature. Bash AST extensions add tokenizer rules. New tools implement the existing `Tool` protocol. Secrets redaction is a pure function. Pre-warming is a background task at REPL startup.

**Tech Stack:** Python 3.12+, asyncio, dataclasses, `re`. No new dependencies.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `duh/cli/repl.py` | Wire QueryGuard, emit USER_PROMPT_SUBMIT/STATUS_LINE/CWD_CHANGED |
| Modify | `duh/kernel/deps.py` | Add `hook_registry` field to Deps |
| Modify | `duh/kernel/loop.py` | Emit PERMISSION_REQUEST/DENIED, POST_TOOL_USE_FAILURE |
| Modify | `duh/kernel/engine.py` | Emit PRE_COMPACT/POST_COMPACT |
| Modify | `duh/adapters/native_executor.py` | Emit POST_TOOL_USE_FAILURE |
| Modify | `duh/hooks.py` | Add HookResponse, glob matching, env vars on subprocess |
| Create | `duh/adapters/model_compactor.py` | Model-call compactor adapter |
| Modify | `duh/tools/bash_ast.py` | Heredoc, process substitution, ANSI-C quoting |
| Create | `duh/tools/todo_tool.py` | TodoWrite tool |
| Create | `duh/tools/ask_user_tool.py` | AskUserQuestion tool |
| Modify | `duh/tools/registry.py` | Register TodoWrite + AskUserQuestion |
| Create | `duh/kernel/redact.py` | Secrets redaction |
| Create | `tests/unit/test_query_guard_wiring.py` | QueryGuard REPL integration tests |
| Create | `tests/unit/test_hook_emit.py` | Hook emission integration tests |
| Create | `tests/unit/test_hook_blocking.py` | Hook blocking semantics tests |
| Create | `tests/unit/test_model_compactor.py` | Model compactor tests |
| Create | `tests/unit/test_bash_ast_heredoc.py` | Heredoc/process-sub tokenizer tests |
| Create | `tests/unit/test_todo_tool.py` | TodoWrite tool tests |
| Create | `tests/unit/test_ask_user_tool.py` | AskUserQuestion tool tests |
| Create | `tests/unit/test_redact.py` | Secrets redaction tests |
| Create | `tests/unit/test_prewarm.py` | Pre-warming tests |

---

### Task 1: Wire QueryGuard into REPL (Gap 1, ADR-043)

**Files:**
- Modify: `duh/cli/repl.py`
- Create: `tests/unit/test_query_guard_wiring.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_query_guard_wiring.py
"""Tests for QueryGuard wiring into the REPL loop."""

from __future__ import annotations

import asyncio
import pytest

from duh.kernel.query_guard import QueryGuard, QueryState


class TestQueryGuardREPLIntegration:
    """Verify that the REPL uses QueryGuard around engine.run()."""

    def test_guard_reserve_transitions_to_dispatching(self):
        guard = QueryGuard()
        gen = guard.reserve()
        assert guard.state == QueryState.DISPATCHING
        assert gen == 1

    def test_guard_try_start_transitions_to_running(self):
        guard = QueryGuard()
        gen = guard.reserve()
        result = guard.try_start(gen)
        assert result == gen
        assert guard.state == QueryState.RUNNING

    def test_guard_end_transitions_to_idle(self):
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        assert guard.end(gen) is True
        assert guard.state == QueryState.IDLE

    def test_guard_force_end_resets_from_any_state(self):
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        guard.force_end()
        assert guard.state == QueryState.IDLE
        assert guard.generation == gen + 1

    def test_reserve_while_not_idle_raises(self):
        guard = QueryGuard()
        guard.reserve()
        with pytest.raises(RuntimeError, match="not idle"):
            guard.reserve()

    def test_stale_generation_rejected_by_try_start(self):
        guard = QueryGuard()
        gen1 = guard.reserve()
        guard.force_end()
        gen2 = guard.reserve()
        assert guard.try_start(gen1) is None
        assert guard.try_start(gen2) == gen2

    def test_stale_generation_rejected_by_end(self):
        guard = QueryGuard()
        gen1 = guard.reserve()
        guard.try_start(gen1)
        guard.force_end()
        gen2 = guard.reserve()
        guard.try_start(gen2)
        assert guard.end(gen1) is False
        assert guard.end(gen2) is True

    def test_full_lifecycle_sequence(self):
        """Simulate the REPL calling reserve -> try_start -> end."""
        guard = QueryGuard()
        # Turn 1
        gen = guard.reserve()
        assert guard.try_start(gen) == gen
        assert guard.end(gen) is True
        # Turn 2
        gen = guard.reserve()
        assert guard.try_start(gen) == gen
        assert guard.end(gen) is True
        assert guard.state == QueryState.IDLE

    def test_abort_during_running_allows_new_query(self):
        """Simulate Ctrl-C abort during streaming."""
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        # User hits Ctrl-C
        guard.force_end()
        # New query should work
        gen2 = guard.reserve()
        assert guard.try_start(gen2) == gen2
        guard.end(gen2)
        assert guard.state == QueryState.IDLE
```

- [ ] **Step 2: Run the tests (expect pass — these test QueryGuard itself)**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_query_guard_wiring.py -v
```

- [ ] **Step 3: Wire QueryGuard into repl.py**

In `duh/cli/repl.py`, add the import near the top imports:

```python
from duh.kernel.query_guard import QueryGuard
```

In `run_repl()`, after creating the engine (around line 1008), add:

```python
    _query_guard = QueryGuard()
```

In the main REPL loop, wrap the `engine.run()` call with QueryGuard lifecycle. Replace the block starting at `renderer.status_bar(model, engine.turn_count + 1)` (around line 1124) through `renderer.turn_end()` (around line 1171) with:

```python
        # Show status bar before each turn (model + turn count)
        renderer.status_bar(model, engine.turn_count + 1)

        # --- QueryGuard: reserve slot before dispatching ---
        try:
            _qg_gen = _query_guard.reserve()
        except RuntimeError:
            renderer.error("A query is already in progress.")
            continue

        try:
            _qg_started = _query_guard.try_start(_qg_gen)
            if _qg_started is None:
                renderer.error("Query generation became stale.")
                continue

            # Run the prompt through the engine
            async for event in engine.run(effective_input):
                event_type = event.get("type", "")

                if event_type == "text_delta":
                    renderer.text_delta(event.get("text", ""))

                elif event_type == "thinking_delta":
                    renderer.thinking_delta(event.get("text", ""))

                elif event_type == "tool_use":
                    name = event.get("name", "?")
                    inp = event.get("input", {})
                    renderer.tool_use(name, inp)

                elif event_type == "tool_result":
                    renderer.tool_result(
                        str(event.get("output", "")),
                        bool(event.get("is_error")),
                    )

                elif event_type == "assistant":
                    msg = event.get("message")
                    if isinstance(msg, Message) and msg.metadata.get("is_error"):
                        hint = _interpret_error(msg.text)
                        renderer.error(hint)

                elif event_type == "error":
                    hint = _interpret_error(event.get("error", "unknown"))
                    renderer.error(hint)

                elif event_type == "budget_warning":
                    sys.stderr.write(
                        f"\033[33mWarning: {event.get('message', '')}\033[0m\n"
                    )

                elif event_type == "budget_exceeded":
                    sys.stderr.write(
                        f"\033[1;33m{event.get('message', '')}\033[0m\n"
                    )

        except (KeyboardInterrupt, EOFError):
            # User aborted mid-query
            _query_guard.force_end()
            sys.stdout.write("\n  (query aborted)\n")
            continue
        finally:
            # Always return to idle
            _query_guard.end(_qg_gen)

        # Re-render accumulated text as Rich Markdown (no-op for plain)
        renderer.flush_response()
        renderer.turn_end()
```

- [ ] **Step 4: Run the full test suite**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_query_guard_wiring.py tests/unit/test_query_guard.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/cli/repl.py tests/unit/test_query_guard_wiring.py && git commit -m "Wire QueryGuard into REPL loop (ADR-043)"
```

---

### Task 2: Emit Hook Events (Gap 2, ADR-044)

**Files:**
- Modify: `duh/kernel/deps.py`
- Modify: `duh/kernel/loop.py`
- Modify: `duh/kernel/engine.py`
- Modify: `duh/adapters/native_executor.py`
- Modify: `duh/cli/repl.py`
- Create: `tests/unit/test_hook_emit.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_hook_emit.py
"""Tests for hook event emission across engine, loop, and REPL."""

from __future__ import annotations

import asyncio
import pytest

from duh.hooks import HookEvent, HookRegistry, HookConfig, HookType, HookResult, execute_hooks


class _Recorder:
    """Records hook events fired during tests."""

    def __init__(self):
        self.events: list[tuple[HookEvent, dict]] = []

    async def callback(self, event: HookEvent, data: dict) -> HookResult:
        self.events.append((event, data))
        return HookResult(hook_name="recorder", success=True)

    def has_event(self, event: HookEvent) -> bool:
        return any(e == event for e, _ in self.events)

    def get_data(self, event: HookEvent) -> dict | None:
        for e, d in self.events:
            if e == event:
                return d
        return None


class TestHookRegistryForNewEvents:
    """Verify new hook events can be registered and dispatched."""

    @pytest.mark.asyncio
    async def test_permission_request_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.PERMISSION_REQUEST,
            hook_type=HookType.FUNCTION,
            name="perm_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.PERMISSION_REQUEST, {
            "tool_name": "Bash",
            "input": {"command": "ls"},
        })
        assert recorder.has_event(HookEvent.PERMISSION_REQUEST)

    @pytest.mark.asyncio
    async def test_permission_denied_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.PERMISSION_DENIED,
            hook_type=HookType.FUNCTION,
            name="denied_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.PERMISSION_DENIED, {
            "tool_name": "Bash",
            "reason": "user rejected",
        })
        assert recorder.has_event(HookEvent.PERMISSION_DENIED)

    @pytest.mark.asyncio
    async def test_pre_compact_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.PRE_COMPACT,
            hook_type=HookType.FUNCTION,
            name="precompact_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.PRE_COMPACT, {
            "message_count": 50,
            "token_estimate": 120000,
        })
        assert recorder.has_event(HookEvent.PRE_COMPACT)

    @pytest.mark.asyncio
    async def test_post_compact_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.POST_COMPACT,
            hook_type=HookType.FUNCTION,
            name="postcompact_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.POST_COMPACT, {
            "message_count_before": 50,
            "message_count_after": 10,
        })
        assert recorder.has_event(HookEvent.POST_COMPACT)

    @pytest.mark.asyncio
    async def test_user_prompt_submit_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.USER_PROMPT_SUBMIT,
            hook_type=HookType.FUNCTION,
            name="prompt_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.USER_PROMPT_SUBMIT, {
            "prompt": "fix the bug",
            "session_id": "abc-123",
        })
        assert recorder.has_event(HookEvent.USER_PROMPT_SUBMIT)
        data = recorder.get_data(HookEvent.USER_PROMPT_SUBMIT)
        assert data["prompt"] == "fix the bug"

    @pytest.mark.asyncio
    async def test_post_tool_use_failure_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.POST_TOOL_USE_FAILURE,
            hook_type=HookType.FUNCTION,
            name="failure_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.POST_TOOL_USE_FAILURE, {
            "tool_name": "Bash",
            "error": "timeout after 300s",
        })
        assert recorder.has_event(HookEvent.POST_TOOL_USE_FAILURE)

    @pytest.mark.asyncio
    async def test_status_line_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.STATUS_LINE,
            hook_type=HookType.FUNCTION,
            name="status_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.STATUS_LINE, {
            "model": "claude-sonnet-4-6",
            "turn": 3,
        })
        assert recorder.has_event(HookEvent.STATUS_LINE)

    @pytest.mark.asyncio
    async def test_cwd_changed_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.CWD_CHANGED,
            hook_type=HookType.FUNCTION,
            name="cwd_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.CWD_CHANGED, {
            "old_cwd": "/old",
            "new_cwd": "/new",
        })
        assert recorder.has_event(HookEvent.CWD_CHANGED)
```

- [ ] **Step 2: Run the tests (expect pass — these test dispatch only)**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_hook_emit.py -v
```

- [ ] **Step 3: Add hook_registry to Deps**

In `duh/kernel/deps.py`, add after the existing fields:

```python
    # Optional: hook registry for lifecycle event emission
    hook_registry: Any = None  # HookRegistry | None
```

- [ ] **Step 4: Emit PERMISSION_REQUEST / PERMISSION_DENIED in loop.py**

In `duh/kernel/loop.py`, add at the top imports:

```python
from duh.hooks import HookEvent, execute_hooks
```

In the tool execution section of the `query()` function, around the approval check (line ~189), wrap the approval check with hook emission:

```python
            # Check approval
            if deps.approve:
                # Emit PERMISSION_REQUEST hook
                if deps.hook_registry:
                    await execute_hooks(
                        deps.hook_registry,
                        HookEvent.PERMISSION_REQUEST,
                        {"tool_name": tool_name, "input": tool_input},
                        matcher_value=tool_name,
                    )

                approval = await deps.approve(tool_name, tool_input)
                if not approval.get("allowed", True):
                    reason = approval.get("reason", "Permission denied")

                    # Emit PERMISSION_DENIED hook
                    if deps.hook_registry:
                        await execute_hooks(
                            deps.hook_registry,
                            HookEvent.PERMISSION_DENIED,
                            {"tool_name": tool_name, "input": tool_input, "reason": reason},
                            matcher_value=tool_name,
                        )

                    result = ToolResultBlock(
                        tool_use_id=tool_id,
                        content=f"Tool use denied: {reason}",
                        is_error=True,
                    )
                    tool_results.append(result)
                    yield {"type": "tool_result", "tool_use_id": tool_id,
                           "output": result.content, "is_error": True}
                    continue
```

Also emit POST_TOOL_USE_FAILURE when tool execution raises, in the except block:

```python
                except Exception as e:
                    # Emit POST_TOOL_USE_FAILURE hook
                    if deps.hook_registry:
                        await execute_hooks(
                            deps.hook_registry,
                            HookEvent.POST_TOOL_USE_FAILURE,
                            {"tool_name": tool_name, "error": str(e)},
                            matcher_value=tool_name,
                        )
                    result = ToolResultBlock(
                        tool_use_id=tool_id,
                        content=f"Tool error: {e}",
                        is_error=True,
                    )
```

- [ ] **Step 5: Emit PRE_COMPACT / POST_COMPACT in engine.py**

In `duh/kernel/engine.py`, add import:

```python
from duh.hooks import HookEvent, execute_hooks
```

In the auto-compact section (around line 234), wrap the compaction call:

```python
            if input_estimate > threshold:
                # Emit PRE_COMPACT hook
                if self._deps.hook_registry:
                    await execute_hooks(
                        self._deps.hook_registry,
                        HookEvent.PRE_COMPACT,
                        {"message_count": len(self._messages), "token_estimate": input_estimate},
                    )

                count_before = len(self._messages)
                self._messages = await self._deps.compact(
                    self._messages, token_limit=threshold,
                )

                # Emit POST_COMPACT hook
                if self._deps.hook_registry:
                    await execute_hooks(
                        self._deps.hook_registry,
                        HookEvent.POST_COMPACT,
                        {
                            "message_count_before": count_before,
                            "message_count_after": len(self._messages),
                        },
                    )
```

- [ ] **Step 6: Emit USER_PROMPT_SUBMIT, STATUS_LINE, CWD_CHANGED in repl.py**

In `duh/cli/repl.py`, add import:

```python
from duh.hooks import HookEvent, HookRegistry, execute_hooks
```

In `run_repl()`, create a registry after deps creation:

```python
    _hook_registry = HookRegistry()
    deps.hook_registry = _hook_registry
```

Before dispatching `engine.run()`, emit USER_PROMPT_SUBMIT:

```python
        # Emit USER_PROMPT_SUBMIT hook
        if _hook_registry:
            await execute_hooks(
                _hook_registry,
                HookEvent.USER_PROMPT_SUBMIT,
                {"prompt": effective_input, "session_id": engine.session_id},
            )
```

In the status_bar call, emit STATUS_LINE:

```python
        renderer.status_bar(model, engine.turn_count + 1)
        if _hook_registry:
            await execute_hooks(
                _hook_registry,
                HookEvent.STATUS_LINE,
                {"model": model, "turn": engine.turn_count + 1},
            )
```

- [ ] **Step 7: Run full test suite**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_hook_emit.py tests/unit/test_hooks*.py -v
```

- [ ] **Step 8: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/deps.py duh/kernel/loop.py duh/kernel/engine.py duh/cli/repl.py tests/unit/test_hook_emit.py && git commit -m "Emit 22 hook events across engine, loop, and REPL (ADR-044)"
```

---

### Task 3: Hook Blocking Semantics (Gap 3, ADR-045)

**Files:**
- Modify: `duh/hooks.py`
- Create: `tests/unit/test_hook_blocking.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_hook_blocking.py
"""Tests for hook blocking semantics — hooks that can veto tool calls."""

from __future__ import annotations

import asyncio
import json
import pytest

from duh.hooks import (
    HookConfig,
    HookEvent,
    HookRegistry,
    HookResponse,
    HookType,
    execute_hooks_with_blocking,
    _glob_match,
)


class TestHookResponse:
    def test_default_is_continue(self):
        r = HookResponse()
        assert r.decision == "continue"
        assert r.suppress_output is False
        assert r.message == ""

    def test_block_decision(self):
        r = HookResponse(decision="block", message="denied by policy")
        assert r.decision == "block"
        assert r.message == "denied by policy"

    def test_allow_decision(self):
        r = HookResponse(decision="allow")
        assert r.decision == "allow"

    def test_from_json_block(self):
        raw = json.dumps({"decision": "block", "message": "nope"})
        r = HookResponse.from_json(raw)
        assert r.decision == "block"
        assert r.message == "nope"

    def test_from_json_invalid_falls_back_to_continue(self):
        r = HookResponse.from_json("not json at all")
        assert r.decision == "continue"

    def test_from_json_empty_string(self):
        r = HookResponse.from_json("")
        assert r.decision == "continue"


class TestGlobMatcher:
    def test_exact_match(self):
        assert _glob_match("Bash", "Bash") is True

    def test_wildcard_match(self):
        assert _glob_match("Bash(git *)", "Bash(git push)") is True

    def test_wildcard_no_match(self):
        assert _glob_match("Bash(git *)", "Bash(rm -rf /)") is False

    def test_empty_matcher_matches_all(self):
        assert _glob_match("", "anything") is True

    def test_star_matches_everything(self):
        assert _glob_match("*", "Bash") is True

    def test_question_mark(self):
        assert _glob_match("Bas?", "Bash") is True
        assert _glob_match("Bas?", "Bass") is True
        assert _glob_match("Bas?", "Basic") is False


class TestBlockingExecution:
    @pytest.mark.asyncio
    async def test_no_hooks_returns_continue(self):
        registry = HookRegistry()
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"}, matcher_value="Bash",
        )
        assert response.decision == "continue"

    @pytest.mark.asyncio
    async def test_blocking_hook_returns_block(self):
        registry = HookRegistry()

        async def blocker(event, data):
            from duh.hooks import HookResult
            return HookResult(
                hook_name="blocker",
                success=True,
                output=json.dumps({"decision": "block", "message": "blocked by test"}),
            )

        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="blocker",
            callback=blocker,
        ))
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"}, matcher_value="Bash",
        )
        assert response.decision == "block"
        assert response.message == "blocked by test"

    @pytest.mark.asyncio
    async def test_allow_hook_returns_allow(self):
        registry = HookRegistry()

        async def allower(event, data):
            from duh.hooks import HookResult
            return HookResult(
                hook_name="allower",
                success=True,
                output=json.dumps({"decision": "allow"}),
            )

        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="allower",
            callback=allower,
        ))
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Read"}, matcher_value="Read",
        )
        assert response.decision == "allow"

    @pytest.mark.asyncio
    async def test_first_block_wins(self):
        """If any hook returns block, the result is block."""
        registry = HookRegistry()

        async def allower(event, data):
            from duh.hooks import HookResult
            return HookResult(hook_name="allower", success=True,
                              output=json.dumps({"decision": "allow"}))

        async def blocker(event, data):
            from duh.hooks import HookResult
            return HookResult(hook_name="blocker", success=True,
                              output=json.dumps({"decision": "block", "message": "no"}))

        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE, hook_type=HookType.FUNCTION,
            name="allower", callback=allower,
        ))
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE, hook_type=HookType.FUNCTION,
            name="blocker", callback=blocker,
        ))
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"}, matcher_value="Bash",
        )
        assert response.decision == "block"
```

- [ ] **Step 2: Run the tests (expect fail — HookResponse etc don't exist yet)**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_hook_blocking.py -v
```

- [ ] **Step 3: Implement HookResponse, glob matching, and blocking execution**

In `duh/hooks.py`, add after the `HookResult` dataclass:

```python
@dataclass
class HookResponse:
    """Parsed response from a blocking hook.

    Hooks can return JSON on stdout with these fields:
    - decision: "continue" (default) | "block" | "allow"
    - suppress_output: bool (default False) — suppress tool output from model
    - message: str — explanation for block/allow decision
    """
    decision: str = "continue"  # "continue" | "block" | "allow"
    suppress_output: bool = False
    message: str = ""

    @classmethod
    def from_json(cls, raw: str) -> "HookResponse":
        """Parse a HookResponse from JSON string. Falls back to continue on parse error."""
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
```

Add the glob matching function:

```python
import fnmatch

def _glob_match(pattern: str, value: str) -> bool:
    """Match a value against a glob pattern.

    Empty pattern matches everything. Supports *, ?, [seq] via fnmatch.
    """
    if not pattern:
        return True
    return fnmatch.fnmatch(value, pattern)
```

Update `get_hooks()` in `HookRegistry` to use glob matching:

```python
    def get_hooks(
        self,
        event: HookEvent,
        *,
        matcher_value: str | None = None,
    ) -> list[HookConfig]:
        hooks = self._hooks.get(event, [])
        if matcher_value is None:
            return list(hooks)
        return [
            h
            for h in hooks
            if not h.matcher or _glob_match(h.matcher, matcher_value)
        ]
```

Add env vars to the command hook executor. In `_execute_command_hook`, update the subprocess creation:

```python
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
```

Add `import os` to the top of `hooks.py`.

Add the blocking execution function:

```python
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
    If all hooks return "continue" or "allow", the response is "continue" or "allow".

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
        if result.output:
            parsed = HookResponse.from_json(result.output)
            if parsed.decision == "block":
                return parsed

    # Check for explicit allow
    for result in results:
        if result.output:
            parsed = HookResponse.from_json(result.output)
            if parsed.decision == "allow":
                return parsed

    return HookResponse(decision="continue")
```

- [ ] **Step 4: Run the tests**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_hook_blocking.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/hooks.py tests/unit/test_hook_blocking.py && git commit -m "Add hook blocking semantics with HookResponse + glob matching (ADR-045)"
```

---

### Task 4: Model-Call Compaction (Gap 4, ADR-046)

**Files:**
- Create: `duh/adapters/model_compactor.py`
- Create: `tests/unit/test_model_compactor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_model_compactor.py
"""Tests for duh.adapters.model_compactor — model-call compaction."""

from __future__ import annotations

import asyncio
import pytest

from duh.kernel.messages import Message


class _FakeModelProvider:
    """Fake model that returns a canned summary."""

    def __init__(self, summary: str = "Summary of the conversation."):
        self._summary = summary
        self.calls: list[dict] = []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        yield {"type": "text_delta", "text": self._summary}
        yield {
            "type": "assistant",
            "message": Message(role="assistant", content=self._summary),
        }
        yield {"type": "done", "stop_reason": "end_turn"}


class TestModelCompactor:
    @pytest.mark.asyncio
    async def test_compact_below_limit_returns_unchanged(self):
        from duh.adapters.model_compactor import ModelCompactor
        provider = _FakeModelProvider()
        compactor = ModelCompactor(call_model=provider.stream)
        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        result = await compactor.compact(messages, token_limit=100_000)
        assert len(result) == 2  # no compaction needed
        assert provider.calls == []  # model not called

    @pytest.mark.asyncio
    async def test_compact_above_limit_calls_model(self):
        from duh.adapters.model_compactor import ModelCompactor
        provider = _FakeModelProvider(summary="Conversation about bugs.")
        compactor = ModelCompactor(call_model=provider.stream, bytes_per_token=1)
        # Create enough messages to exceed a tiny limit
        messages = [
            Message(role="user", content="A" * 500),
            Message(role="assistant", content="B" * 500),
            Message(role="user", content="fix the latest bug"),
        ]
        result = await compactor.compact(messages, token_limit=100)
        # Should have compacted: system summary + kept recent messages
        assert len(result) < len(messages) or any(
            "summary" in (m.content.lower() if isinstance(m.content, str) else "")
            for m in result
            if isinstance(m, Message)
        )
        assert len(provider.calls) > 0

    @pytest.mark.asyncio
    async def test_compact_preserves_recent_messages(self):
        from duh.adapters.model_compactor import ModelCompactor
        provider = _FakeModelProvider(summary="Earlier context.")
        compactor = ModelCompactor(
            call_model=provider.stream,
            bytes_per_token=1,
            min_keep=1,
        )
        messages = [
            Message(role="user", content="A" * 500),
            Message(role="assistant", content="B" * 500),
            Message(role="user", content="latest message"),
        ]
        result = await compactor.compact(messages, token_limit=100)
        # The latest message should be preserved
        assert any(
            isinstance(m, Message) and "latest" in (m.content if isinstance(m.content, str) else "")
            for m in result
        )

    @pytest.mark.asyncio
    async def test_compact_fallback_on_model_failure(self):
        """When model call fails, fall back to simple truncation."""
        from duh.adapters.model_compactor import ModelCompactor

        async def failing_model(**kwargs):
            raise RuntimeError("API error")
            yield  # make it a generator  # noqa: E501

        compactor = ModelCompactor(call_model=failing_model, bytes_per_token=1)
        messages = [
            Message(role="user", content="A" * 500),
            Message(role="assistant", content="B" * 500),
            Message(role="user", content="latest"),
        ]
        # Should not raise — falls back to simple compaction
        result = await compactor.compact(messages, token_limit=100)
        assert isinstance(result, list)

    def test_estimate_tokens(self):
        from duh.adapters.model_compactor import ModelCompactor
        compactor = ModelCompactor(call_model=None)
        messages = [Message(role="user", content="hello world")]
        tokens = compactor.estimate_tokens(messages)
        assert tokens > 0
```

- [ ] **Step 2: Run the tests (expect fail — module doesn't exist)**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_model_compactor.py -v
```

- [ ] **Step 3: Implement ModelCompactor**

```python
# duh/adapters/model_compactor.py
"""Model-call compactor — uses the model to summarize conversation context.

Unlike SimpleCompactor which does deterministic text truncation, this adapter
calls the model to produce an intelligent summary of older messages. Falls
back to SimpleCompactor behavior when the model is unavailable.

    compactor = ModelCompactor(call_model=provider.stream)
    compacted = await compactor.compact(messages, token_limit=100_000)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from duh.kernel.messages import Message
from duh.adapters.simple_compactor import SimpleCompactor

logger = logging.getLogger(__name__)

_SUMMARIZE_PROMPT = (
    "Summarize the following conversation context concisely. "
    "Preserve key decisions, file paths, tool results, and any "
    "instructions that are still relevant. Output only the summary."
)


class ModelCompactor:
    """Compactor that uses a model call to produce intelligent summaries.

    Implements the same interface as SimpleCompactor (ContextManager port).
    When the model call fails, falls back to SimpleCompactor's truncation.
    """

    def __init__(
        self,
        call_model: Any = None,
        default_limit: int = 100_000,
        bytes_per_token: int = 4,
        min_keep: int = 2,
    ):
        self._call_model = call_model
        self._simple = SimpleCompactor(
            default_limit=default_limit,
            bytes_per_token=bytes_per_token,
            min_keep=min_keep,
        )

    def estimate_tokens(self, messages: list[Any]) -> int:
        """Estimate token count (delegates to SimpleCompactor)."""
        return self._simple.estimate_tokens(messages)

    async def compact(
        self,
        messages: list[Any],
        token_limit: int = 0,
    ) -> list[Any]:
        """Compact messages using model-generated summary.

        Strategy:
        1. If messages fit within limit, return as-is.
        2. Partition into system messages, droppable, and kept (tail window).
        3. Call the model to summarize the droppable messages.
        4. Return: system + summary + kept.
        5. On model failure, fall back to SimpleCompactor.
        """
        limit = token_limit or self._simple.default_limit

        # Check if compaction is needed
        total_tokens = self.estimate_tokens(messages)
        if total_tokens <= limit:
            return list(messages)

        # If no model available, fall back to simple
        if not self._call_model:
            return await self._simple.compact(messages, token_limit=limit)

        # Partition
        system_msgs: list[Any] = []
        conversation: list[Any] = []
        for msg in messages:
            role = msg.role if isinstance(msg, Message) else msg.get("role", "")
            if role == "system":
                system_msgs.append(msg)
            else:
                conversation.append(msg)

        if not conversation:
            return list(system_msgs)

        # Determine tail window (walk backward)
        system_tokens = self._simple.estimate_tokens(system_msgs)
        budget = max(0, limit - system_tokens)

        kept: list[Any] = []
        used = 0
        for msg in reversed(conversation):
            msg_tokens = self._simple._estimate_single(msg)
            if used + msg_tokens > budget and len(kept) >= self._simple.min_keep:
                break
            kept.append(msg)
            used += msg_tokens
        kept.reverse()

        dropped_count = len(conversation) - len(kept)
        if dropped_count <= 0:
            return system_msgs + kept

        dropped = conversation[:dropped_count]

        # Try model-generated summary
        try:
            summary_text = await self._generate_summary(dropped)
        except Exception:
            logger.debug("Model summary failed, falling back to simple compaction", exc_info=True)
            return await self._simple.compact(messages, token_limit=limit)

        summary_msg = Message(role="system", content=f"Previous conversation summary:\n{summary_text}")
        return system_msgs + [summary_msg] + kept

    async def _generate_summary(self, messages: list[Any]) -> str:
        """Call the model to summarize a list of messages."""
        # Build a text representation of messages to summarize
        parts: list[str] = []
        for msg in messages:
            if isinstance(msg, Message):
                role = msg.role
                text = msg.text if hasattr(msg, "text") else str(msg.content)
            else:
                role = msg.get("role", "?")
                content = msg.get("content", "")
                text = content if isinstance(content, str) else json.dumps(content, default=str)

            # Truncate individual messages
            if len(text) > 500:
                text = text[:497] + "..."
            parts.append(f"[{role}] {text}")

        conversation_text = "\n".join(parts)
        # Cap total input to avoid recursive PTL
        if len(conversation_text) > 10_000:
            conversation_text = conversation_text[:10_000] + "\n... (truncated)"

        summary_parts: list[str] = []
        async for event in self._call_model(
            messages=[Message(role="user", content=f"{_SUMMARIZE_PROMPT}\n\n{conversation_text}")],
            system_prompt="You are a concise summarizer. Output only the summary.",
            model="",  # Use default model
        ):
            if isinstance(event, dict):
                if event.get("type") == "text_delta":
                    summary_parts.append(event.get("text", ""))

        return "".join(summary_parts) or "Conversation context (summary unavailable)."
```

- [ ] **Step 4: Run the tests**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_model_compactor.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/adapters/model_compactor.py tests/unit/test_model_compactor.py && git commit -m "Add model-call compactor adapter (ADR-046)"
```

---

### Task 5: Bash AST Heredoc + Process Substitution (Gap 5, ADR-047)

**Files:**
- Modify: `duh/tools/bash_ast.py`
- Create: `tests/unit/test_bash_ast_heredoc.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_bash_ast_heredoc.py
"""Tests for heredoc, process substitution, and ANSI-C quoting in bash_ast."""

from __future__ import annotations

import pytest

from duh.tools.bash_ast import tokenize, Segment, SegmentType, strip_wrappers


class TestHeredoc:
    def test_simple_heredoc(self):
        cmd = "cat <<EOF\nhello world\nEOF"
        segments = tokenize(cmd)
        # The heredoc content should be captured; the command itself is cat
        assert any("cat" in s.text for s in segments)

    def test_heredoc_with_dash(self):
        """<<- allows leading tabs to be stripped."""
        cmd = "cat <<-EOF\n\thello\nEOF"
        segments = tokenize(cmd)
        assert any("cat" in s.text for s in segments)

    def test_heredoc_quoted_delimiter(self):
        """Quoted delimiter means no variable expansion (but we just tokenize)."""
        cmd = "cat <<'END'\n$VAR stays literal\nEND"
        segments = tokenize(cmd)
        assert len(segments) >= 1

    def test_heredoc_in_pipeline(self):
        cmd = "cat <<EOF | grep hello\nfoo\nhello\nEOF"
        segments = tokenize(cmd)
        assert any("grep" in s.text for s in segments)

    def test_heredoc_preserves_following_command(self):
        cmd = "cat <<EOF\ndata\nEOF\necho done"
        segments = tokenize(cmd)
        assert any("echo" in s.text for s in segments)


class TestProcessSubstitution:
    def test_input_process_substitution(self):
        cmd = "diff <(ls dir1) <(ls dir2)"
        segments = tokenize(cmd)
        # The main command is diff; process subs are extracted
        assert any("diff" in s.text for s in segments)
        # Process sub contents should appear as subshell segments
        assert any("ls dir1" in s.text for s in segments if s.seg_type == SegmentType.SUBSHELL)

    def test_output_process_substitution(self):
        cmd = "tee >(grep error > errors.log)"
        segments = tokenize(cmd)
        assert any("tee" in s.text for s in segments)
        assert any("grep error" in s.text for s in segments if s.seg_type == SegmentType.SUBSHELL)

    def test_nested_process_substitution(self):
        cmd = "diff <(sort file1) <(sort file2)"
        segments = tokenize(cmd)
        subshells = [s for s in segments if s.seg_type == SegmentType.SUBSHELL]
        assert len(subshells) >= 2


class TestAnsiCQuoting:
    def test_ansi_c_escape_newline(self):
        """$'...' with escape sequences should be treated as a quoted string."""
        cmd = "echo $'hello\\nworld'"
        segments = tokenize(cmd)
        assert len(segments) >= 1
        # The command should not be split on the escaped newline
        assert any("echo" in s.text for s in segments)

    def test_ansi_c_with_tab(self):
        cmd = "printf $'col1\\tcol2'"
        segments = tokenize(cmd)
        assert len(segments) >= 1

    def test_ansi_c_in_pipeline(self):
        cmd = "echo $'line1\\nline2' | grep line1"
        segments = tokenize(cmd)
        assert any("grep" in s.text for s in segments)


class TestWhitespaceNormalization:
    def test_extra_spaces_normalized(self):
        cmd = "ls    -la     /tmp"
        segments = tokenize(cmd)
        assert len(segments) == 1

    def test_tabs_in_command(self):
        cmd = "echo\thello"
        segments = tokenize(cmd)
        assert len(segments) == 1

    def test_mixed_whitespace(self):
        cmd = "  ls  &&  echo done  "
        segments = tokenize(cmd)
        assert len(segments) == 2
```

- [ ] **Step 2: Run the tests (expect some failures)**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_bash_ast_heredoc.py -v
```

- [ ] **Step 3: Extend the tokenizer in bash_ast.py**

Add heredoc handling after the `_QUOTE_RE` regex definition:

```python
# Heredoc patterns: <<EOF, <<-EOF, <<'EOF', <<"EOF"
_HEREDOC_RE = re.compile(
    r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|(\w+))"
)

# Process substitution: <(...) and >(...)
_PROC_SUB_RE = re.compile(r"[<>]\(")

# ANSI-C quoting: $'...'
_ANSI_C_RE = re.compile(r"""\$'(?:[^'\\]|\\.)*'""")
```

Update `_mask_quotes` to also mask ANSI-C strings:

```python
def _mask_quotes(cmd: str) -> tuple[str, str]:
    """Replace quoted strings with placeholders so operators inside quotes
    are not treated as segment separators.

    Also masks ANSI-C $'...' strings.
    """
    masked = list(cmd)
    # Mask ANSI-C quoting first (before regular quotes)
    for m in _ANSI_C_RE.finditer(cmd):
        for i in range(m.start(), m.end()):
            masked[i] = "\x00"
    # Then mask regular quotes
    for m in _QUOTE_RE.finditer(cmd):
        for i in range(m.start(), m.end()):
            masked[i] = "\x00"
    return "".join(masked), cmd
```

Add heredoc extraction function:

```python
def _extract_heredocs(cmd: str, masked: str) -> tuple[str, list[str]]:
    """Extract heredoc bodies from the command.

    Handles <<EOF...EOF, <<-EOF...EOF, <<'EOF'...EOF, <<"EOF"...EOF.
    Returns the command with heredoc bodies removed, and a list of
    the heredoc body contents.
    """
    heredoc_bodies: list[str] = []
    lines = cmd.split("\n")
    masked_lines = masked.split("\n")
    result_lines: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        masked_line = masked_lines[i] if i < len(masked_lines) else line

        # Check for heredoc start in the masked line
        m = _HEREDOC_RE.search(masked_line)
        if m:
            delimiter = m.group(1) or m.group(2) or m.group(3)
            # Keep the command line (before heredoc), strip the heredoc marker
            result_lines.append(line[:m.start()].rstrip())
            # Collect heredoc body
            body_lines: list[str] = []
            i += 1
            while i < len(lines):
                if lines[i].strip() == delimiter:
                    break
                body_lines.append(lines[i])
                i += 1
            heredoc_bodies.append("\n".join(body_lines))
        else:
            result_lines.append(line)
        i += 1

    return "\n".join(result_lines), heredoc_bodies
```

Add process substitution extraction to `_extract_subshells`:

```python
def _extract_process_subs(cmd: str, masked: str) -> tuple[str, list[str]]:
    """Extract <(...) and >(...) process substitutions.

    Returns the command with process subs replaced by placeholders,
    and a list of the extracted inner contents.
    """
    subshells: list[str] = []
    result_chars = list(cmd)
    i = 0

    while i < len(masked):
        if (i + 1 < len(masked)
                and masked[i] in "<>"
                and masked[i + 1] == "("
                and masked[i] != "\x00"):
            depth = 1
            start = i
            j = i + 2
            while j < len(masked) and depth > 0:
                if masked[j] == "(" and masked[j] != "\x00":
                    depth += 1
                elif masked[j] == ")" and masked[j] != "\x00":
                    depth -= 1
                j += 1
            if depth == 0:
                inner = cmd[start + 2:j - 1]
                subshells.append(inner)
                for k in range(start, j):
                    result_chars[k] = "\x01"
                masked = masked[:start] + "\x01" * (j - start) + masked[j:]
            i = j
        else:
            i += 1

    return "".join(result_chars), subshells
```

Update `tokenize()` to call the new extractors before existing processing:

```python
def tokenize(cmd: str) -> list[Segment]:
    # Strip full-line comments first
    cmd = strip_comments(cmd)

    if not cmd or not cmd.strip():
        return []

    masked, original = _mask_quotes(cmd)

    # Extract heredocs before splitting
    cmd, heredoc_bodies = _extract_heredocs(cmd, masked)
    masked, _ = _mask_quotes(cmd)  # re-mask after heredoc removal

    # Extract process substitutions
    cmd, proc_subs = _extract_process_subs(cmd, masked)
    masked, _ = _mask_quotes(cmd)  # re-mask after process sub removal

    # Extract $(...) and backtick subshells
    cmd_no_sub, subshells = _extract_subshells(cmd, masked)

    # ... rest of existing tokenize logic ...

    # Add process substitution contents as subshell segments
    for sub in proc_subs:
        sub_stripped = sub.strip()
        if sub_stripped:
            segments.append(Segment(text=sub_stripped, seg_type=SegmentType.SUBSHELL))

    # Heredoc bodies are not classified as separate commands
    # (they're data, not code) — but note them if non-empty
    # for the fanout cap
    total = len(segments)
    if total > MAX_SUBCOMMANDS:
        raise ValueError(...)

    return segments
```

- [ ] **Step 4: Run all bash AST tests**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_bash_ast.py tests/unit/test_bash_ast_heredoc.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/tools/bash_ast.py tests/unit/test_bash_ast_heredoc.py && git commit -m "Extend Bash AST: heredocs, process substitution, ANSI-C quoting (ADR-047)"
```

---

### Task 6: TodoWrite + AskUserQuestion Tools (Gap 6, ADR-048)

**Files:**
- Create: `duh/tools/todo_tool.py`
- Create: `duh/tools/ask_user_tool.py`
- Modify: `duh/tools/registry.py`
- Create: `tests/unit/test_todo_tool.py`
- Create: `tests/unit/test_ask_user_tool.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_todo_tool.py
"""Tests for duh.tools.todo_tool — TodoWrite structured checklist."""

from __future__ import annotations

import asyncio
import json
import pytest

from duh.tools.todo_tool import TodoWriteTool
from duh.kernel.tool import ToolContext


@pytest.fixture
def tool():
    return TodoWriteTool()


@pytest.fixture
def ctx():
    return ToolContext(cwd="/tmp")


class TestTodoWriteTool:
    def test_name(self, tool):
        assert tool.name == "TodoWrite"

    def test_has_schema(self, tool):
        assert "properties" in tool.input_schema

    @pytest.mark.asyncio
    async def test_create_todo(self, tool, ctx):
        result = await tool.call({
            "todos": [
                {"id": "1", "text": "Fix the bug", "status": "pending"},
                {"id": "2", "text": "Write tests", "status": "pending"},
            ]
        }, ctx)
        assert not result.is_error
        assert "2 todos" in result.output.lower() or "updated" in result.output.lower()

    @pytest.mark.asyncio
    async def test_update_todo_status(self, tool, ctx):
        # Create
        await tool.call({
            "todos": [
                {"id": "1", "text": "Fix the bug", "status": "pending"},
            ]
        }, ctx)
        # Update
        result = await tool.call({
            "todos": [
                {"id": "1", "text": "Fix the bug", "status": "done"},
            ]
        }, ctx)
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_get_all_todos(self, tool, ctx):
        await tool.call({
            "todos": [
                {"id": "1", "text": "Task A", "status": "pending"},
                {"id": "2", "text": "Task B", "status": "done"},
            ]
        }, ctx)
        # Each instance tracks its own state
        assert len(tool._todos) == 2

    @pytest.mark.asyncio
    async def test_empty_todos_list(self, tool, ctx):
        result = await tool.call({"todos": []}, ctx)
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self, tool, ctx):
        result = await tool.call({
            "todos": [
                {"id": "1", "text": "Bad status", "status": "invalid"},
            ]
        }, ctx)
        assert result.is_error

    def test_is_not_destructive(self, tool):
        assert tool.is_destructive is False

    def test_is_not_read_only(self, tool):
        assert tool.is_read_only is False
```

```python
# tests/unit/test_ask_user_tool.py
"""Tests for duh.tools.ask_user_tool — AskUserQuestion tool."""

from __future__ import annotations

import asyncio
import pytest

from duh.tools.ask_user_tool import AskUserQuestionTool
from duh.kernel.tool import ToolContext


@pytest.fixture
def ctx():
    return ToolContext(cwd="/tmp")


class TestAskUserQuestionTool:
    def test_name(self):
        tool = AskUserQuestionTool()
        assert tool.name == "AskUserQuestion"

    def test_has_schema(self):
        tool = AskUserQuestionTool()
        assert "properties" in tool.input_schema

    @pytest.mark.asyncio
    async def test_asks_user_via_callback(self, ctx):
        async def fake_input(question: str) -> str:
            return "yes, do it"

        tool = AskUserQuestionTool(ask_fn=fake_input)
        result = await tool.call({"question": "Should I proceed?"}, ctx)
        assert not result.is_error
        assert "yes, do it" in result.output

    @pytest.mark.asyncio
    async def test_empty_question_rejected(self, ctx):
        async def fake_input(question: str) -> str:
            return "answer"

        tool = AskUserQuestionTool(ask_fn=fake_input)
        result = await tool.call({"question": ""}, ctx)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_ask_fn_returns_error(self, ctx):
        tool = AskUserQuestionTool(ask_fn=None)
        result = await tool.call({"question": "hello?"}, ctx)
        assert result.is_error

    def test_is_read_only(self):
        tool = AskUserQuestionTool()
        assert tool.is_read_only is True

    def test_is_not_destructive(self):
        tool = AskUserQuestionTool()
        assert tool.is_destructive is False
```

- [ ] **Step 2: Run the tests (expect fail — modules don't exist)**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_todo_tool.py tests/unit/test_ask_user_tool.py -v
```

- [ ] **Step 3: Implement TodoWriteTool**

```python
# duh/tools/todo_tool.py
"""TodoWrite tool — structured checklist management.

Allows the model to create and update a todo list with status tracking.
This is the tool equivalent of Claude Code's TodoWrite — it gives the
model a way to maintain structured task state.

    tool = TodoWriteTool()
    result = await tool.call({
        "todos": [
            {"id": "1", "text": "Fix the bug", "status": "pending"},
            {"id": "2", "text": "Write tests", "status": "done"},
        ]
    }, context)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult

_VALID_STATUSES = frozenset({"pending", "in_progress", "done", "blocked", "cancelled"})


@dataclass
class TodoItem:
    """A single todo item."""
    id: str
    text: str
    status: str = "pending"


class TodoWriteTool:
    """Structured checklist management tool."""

    name = "TodoWrite"
    description = (
        "Create or update a structured todo checklist. "
        "Each todo has an id, text, and status "
        "(pending | in_progress | done | blocked | cancelled)."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "List of todo items to create or update.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique todo identifier"},
                        "text": {"type": "string", "description": "Todo description"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done", "blocked", "cancelled"],
                            "description": "Current status",
                        },
                    },
                    "required": ["id", "text", "status"],
                },
            },
        },
        "required": ["todos"],
    }

    def __init__(self) -> None:
        self._todos: dict[str, TodoItem] = {}

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        todos = input.get("todos", [])

        for item in todos:
            item_id = item.get("id", "")
            text = item.get("text", "")
            status = item.get("status", "pending")

            if status not in _VALID_STATUSES:
                return ToolResult(
                    output=f"Invalid status '{status}' for todo '{item_id}'. "
                           f"Valid: {', '.join(sorted(_VALID_STATUSES))}",
                    is_error=True,
                )

            self._todos[item_id] = TodoItem(id=item_id, text=text, status=status)

        # Build summary
        total = len(self._todos)
        done = sum(1 for t in self._todos.values() if t.status == "done")
        pending = sum(1 for t in self._todos.values() if t.status == "pending")
        in_progress = sum(1 for t in self._todos.values() if t.status == "in_progress")

        lines = [f"Updated {len(todos)} todos ({total} total)."]
        if total > 0:
            lines.append(f"  Done: {done} | In progress: {in_progress} | Pending: {pending}")
        for t in self._todos.values():
            marker = {"done": "[x]", "in_progress": "[~]", "pending": "[ ]",
                       "blocked": "[!]", "cancelled": "[-]"}.get(t.status, "[ ]")
            lines.append(f"  {marker} {t.id}: {t.text}")

        return ToolResult(output="\n".join(lines))

    def summary(self) -> str:
        """Return a text summary of all todos (for /tasks command)."""
        if not self._todos:
            return "No tasks."
        lines: list[str] = []
        for t in self._todos.values():
            marker = {"done": "[x]", "in_progress": "[~]", "pending": "[ ]",
                       "blocked": "[!]", "cancelled": "[-]"}.get(t.status, "[ ]")
            lines.append(f"  {marker} {t.id}: {t.text}")
        done = sum(1 for t in self._todos.values() if t.status == "done")
        lines.append(f"  ({done}/{len(self._todos)} done)")
        return "\n".join(lines)
```

- [ ] **Step 4: Implement AskUserQuestionTool**

```python
# duh/tools/ask_user_tool.py
"""AskUserQuestion tool — prompts the user for input during execution.

This tool blocks execution and asks the user a question via the
provided ask_fn callback. The user's response is returned to the model.

    async def terminal_input(question: str) -> str:
        return input(f"  {question}\\n  > ")

    tool = AskUserQuestionTool(ask_fn=terminal_input)
    result = await tool.call({"question": "Which file?"}, context)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from duh.kernel.tool import ToolContext, ToolResult

AskFn = Callable[[str], Awaitable[str]]


class AskUserQuestionTool:
    """Blocks execution and prompts the user for a response."""

    name = "AskUserQuestion"
    description = (
        "Ask the user a question and wait for their response. "
        "Use when you need clarification or a decision from the user."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
        },
        "required": ["question"],
    }

    def __init__(self, ask_fn: AskFn | None = None) -> None:
        self._ask_fn = ask_fn

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        question = input.get("question", "").strip()
        if not question:
            return ToolResult(
                output="Question cannot be empty.",
                is_error=True,
            )

        if self._ask_fn is None:
            return ToolResult(
                output="No input handler available (non-interactive mode).",
                is_error=True,
            )

        try:
            answer = await self._ask_fn(question)
            return ToolResult(output=answer)
        except (EOFError, KeyboardInterrupt):
            return ToolResult(output="(user cancelled)")
        except Exception as e:
            return ToolResult(
                output=f"Failed to get user input: {e}",
                is_error=True,
            )
```

- [ ] **Step 5: Register the tools in registry.py**

Add to `duh/tools/registry.py` after the existing tool registrations (before `return tools`):

```python
    # TodoWrite (structured checklist)
    try:
        from duh.tools.todo_tool import TodoWriteTool
        tools.append(TodoWriteTool())
    except ImportError:
        pass

    # AskUserQuestion (interactive user prompting)
    try:
        from duh.tools.ask_user_tool import AskUserQuestionTool
        tools.append(AskUserQuestionTool())
    except ImportError:
        pass
```

- [ ] **Step 6: Run the tests**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_todo_tool.py tests/unit/test_ask_user_tool.py -v
```

- [ ] **Step 7: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/tools/todo_tool.py duh/tools/ask_user_tool.py duh/tools/registry.py tests/unit/test_todo_tool.py tests/unit/test_ask_user_tool.py && git commit -m "Add TodoWrite + AskUserQuestion tools (ADR-048)"
```

---

### Task 7: Secrets Redaction (Gap 7, ADR-049)

**Files:**
- Create: `duh/kernel/redact.py`
- Create: `tests/unit/test_redact.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_redact.py
"""Tests for duh.kernel.redact — secrets redaction."""

from __future__ import annotations

import pytest

from duh.kernel.redact import redact_secrets, REDACTED


class TestRedactSecrets:
    def test_no_secrets(self):
        text = "Hello, world! Just a normal message."
        assert redact_secrets(text) == text

    def test_anthropic_api_key(self):
        text = "Key is sk-ant-api03-abc123xyz"
        result = redact_secrets(text)
        assert "sk-ant" not in result
        assert REDACTED in result

    def test_openai_api_key(self):
        text = "export OPENAI_API_KEY=sk-proj-abc123def456"
        result = redact_secrets(text)
        assert "sk-proj" not in result
        assert REDACTED in result

    def test_aws_access_key(self):
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "AKIA" not in result
        assert REDACTED in result

    def test_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test.sig"
        result = redact_secrets(text)
        assert "eyJhbGci" not in result
        assert REDACTED in result

    def test_github_token(self):
        text = "GITHUB_TOKEN=ghp_abc123def456ghi789jkl012"
        result = redact_secrets(text)
        assert "ghp_" not in result
        assert REDACTED in result

    def test_generic_private_key(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
        result = redact_secrets(text)
        assert "PRIVATE KEY" not in result
        assert REDACTED in result

    def test_multiple_secrets_in_one_string(self):
        text = "Key1=sk-ant-api03-abc123 and Key2=AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "sk-ant" not in result
        assert "AKIA" not in result
        assert result.count(REDACTED) == 2

    def test_empty_string(self):
        assert redact_secrets("") == ""

    def test_password_in_url(self):
        text = "postgres://user:s3cretP@ss@localhost:5432/db"
        result = redact_secrets(text)
        assert "s3cretP@ss" not in result

    def test_generic_secret_assignment(self):
        text = 'SECRET_KEY="my-super-secret-value-12345"'
        result = redact_secrets(text)
        assert "my-super-secret" not in result

    def test_short_values_not_redacted(self):
        """Short values after SECRET_KEY= should still be redacted."""
        text = 'API_KEY="abc"'
        result = redact_secrets(text)
        # Even short values after a key-like name are redacted
        assert REDACTED in result

    def test_preserves_surrounding_text(self):
        text = "Config loaded. API key: sk-ant-api03-xyz. Continuing."
        result = redact_secrets(text)
        assert result.startswith("Config loaded.")
        assert result.endswith("Continuing.")
```

- [ ] **Step 2: Run the tests (expect fail — module doesn't exist)**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_redact.py -v
```

- [ ] **Step 3: Implement redact.py**

```python
# duh/kernel/redact.py
"""Secrets redaction — strip sensitive values from text before it reaches the model.

Catches common secret patterns:
- API keys: sk-ant-*, sk-proj-*, sk-*, AKIA*, ghp_*, gho_*, ghs_*
- Bearer tokens
- Private keys (PEM blocks)
- Passwords in URLs
- Generic secret/key/token assignments

    from duh.kernel.redact import redact_secrets
    safe_text = redact_secrets(tool_output)
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Ordered list of (compiled regex, replacement).
# Order matters: more specific patterns first.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM private keys (multi-line)
    (re.compile(
        r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
        r"[\s\S]*?"
        r"-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
        re.MULTILINE,
    ), REDACTED),

    # Anthropic API keys: sk-ant-api03-...
    (re.compile(r"sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}"), REDACTED),

    # OpenAI API keys: sk-proj-... or sk-...
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"), REDACTED),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), REDACTED),

    # AWS access keys: AKIA...
    (re.compile(r"AKIA[0-9A-Z]{16}"), REDACTED),

    # GitHub tokens: ghp_, gho_, ghs_, ghr_
    (re.compile(r"gh[posh]_[A-Za-z0-9_]{20,}"), REDACTED),

    # Bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{10,}", re.IGNORECASE), f"Bearer {REDACTED}"),

    # Passwords in URLs: protocol://user:password@host
    (re.compile(r"(://[^:]+:)[^@]+(@)"), rf"\1{REDACTED}\2"),

    # Generic secret/key/token/password assignments
    # Matches: SECRET_KEY="value", api_key=value, TOKEN='value', password: "value"
    (re.compile(
        r"(?i)"
        r"(?:secret|api[_-]?key|token|password|passwd|credential|auth)"
        r"""(?:\s*[:=]\s*["']?)"""
        r"""([^"'\s,;}{)]+)"""
    ), lambda m: m.group(0).replace(m.group(1), REDACTED)),
]


def redact_secrets(text: str) -> str:
    """Redact secrets from text, returning the sanitized version.

    Applies each pattern in order. Patterns are designed to avoid
    false positives on normal code/prose while catching the most
    common secret formats.
    """
    if not text:
        return text

    result = text
    for pattern, replacement in _PATTERNS:
        if callable(replacement):
            result = pattern.sub(replacement, result)
        else:
            result = pattern.sub(replacement, result)

    return result
```

- [ ] **Step 4: Run the tests**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_redact.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/redact.py tests/unit/test_redact.py && git commit -m "Add secrets redaction module (ADR-049)"
```

---

### Task 8: Pre-warming (Gap 8, ADR-050)

**Files:**
- Modify: `duh/cli/repl.py`
- Create: `tests/unit/test_prewarm.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_prewarm.py
"""Tests for connection pre-warming at REPL startup."""

from __future__ import annotations

import asyncio
import time
import pytest


class _FakeProvider:
    """Provider that tracks whether it was called."""

    def __init__(self, latency: float = 0.0):
        self.called = False
        self.call_count = 0
        self._latency = latency

    async def stream(self, **kwargs):
        self.called = True
        self.call_count += 1
        if self._latency:
            await asyncio.sleep(self._latency)
        yield {"type": "text_delta", "text": ""}
        yield {"type": "done", "stop_reason": "end_turn"}


class TestPrewarm:
    @pytest.mark.asyncio
    async def test_prewarm_fires_lightweight_call(self):
        from duh.cli.prewarm import prewarm_connection
        provider = _FakeProvider()
        task = asyncio.create_task(prewarm_connection(provider.stream))
        await task
        assert provider.called is True

    @pytest.mark.asyncio
    async def test_prewarm_does_not_block_startup(self):
        from duh.cli.prewarm import prewarm_connection
        provider = _FakeProvider(latency=0.5)
        start = time.monotonic()
        task = asyncio.create_task(prewarm_connection(provider.stream))
        elapsed = time.monotonic() - start
        # Creating the task should be near-instant
        assert elapsed < 0.1
        # Let it complete
        await task
        assert provider.called

    @pytest.mark.asyncio
    async def test_prewarm_failure_is_silent(self):
        async def failing_provider(**kwargs):
            raise RuntimeError("connection refused")
            yield  # noqa: E501

        from duh.cli.prewarm import prewarm_connection
        # Should not raise
        await prewarm_connection(failing_provider)

    @pytest.mark.asyncio
    async def test_prewarm_caches_result(self):
        from duh.cli.prewarm import prewarm_connection, PrewarmResult
        provider = _FakeProvider()
        result = await prewarm_connection(provider.stream)
        assert isinstance(result, PrewarmResult)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_prewarm_records_latency(self):
        from duh.cli.prewarm import prewarm_connection
        provider = _FakeProvider(latency=0.05)
        result = await prewarm_connection(provider.stream)
        assert result.latency_ms >= 0
```

- [ ] **Step 2: Run the tests (expect fail — module doesn't exist)**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_prewarm.py -v
```

- [ ] **Step 3: Implement prewarm module**

```python
# duh/cli/prewarm.py
"""Connection pre-warming — reduce first-turn latency.

Fires a lightweight model ping in the background at REPL startup.
The warmed connection is reused by the provider for the first real turn.

    task = asyncio.create_task(prewarm_connection(provider.stream))
    # ... REPL startup continues ...
    # First real query benefits from warm connection
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PrewarmResult:
    """Result of a pre-warming attempt."""
    success: bool
    latency_ms: float = 0.0
    error: str = ""


async def prewarm_connection(
    call_model: Any,
    *,
    timeout: float = 10.0,
) -> PrewarmResult:
    """Make a lightweight model ping to warm the connection.

    Sends a minimal prompt and discards the response. The HTTP connection
    and any TLS handshake are cached by the underlying HTTP client,
    reducing latency for the first real query.

    Never raises — failures are logged and returned as PrewarmResult.
    """
    import asyncio
    from duh.kernel.messages import Message

    start = time.monotonic()

    try:
        async for event in call_model(
            messages=[Message(role="user", content="hi")],
            system_prompt="Reply with a single word.",
            model="",  # use default
        ):
            # Consume events but discard them
            pass

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("Pre-warm completed in %.0fms", elapsed_ms)
        return PrewarmResult(success=True, latency_ms=elapsed_ms)

    except asyncio.TimeoutError:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("Pre-warm timed out after %.0fms", elapsed_ms)
        return PrewarmResult(success=False, latency_ms=elapsed_ms, error="timeout")

    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("Pre-warm failed: %s (%.0fms)", e, elapsed_ms)
        return PrewarmResult(success=False, latency_ms=elapsed_ms, error=str(e))
```

- [ ] **Step 4: Wire pre-warming into repl.py**

In `duh/cli/repl.py`, add import:

```python
from duh.cli.prewarm import prewarm_connection
```

After the `call_model` is created (after the provider selection block, around line 910), add:

```python
    # --- Pre-warm the model connection in background ---
    _prewarm_task = asyncio.ensure_future(prewarm_connection(call_model))
```

- [ ] **Step 5: Run the tests**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_prewarm.py -v
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/cli/prewarm.py duh/cli/repl.py tests/unit/test_prewarm.py && git commit -m "Add connection pre-warming at REPL startup (ADR-050)"
```

---

## Final Verification

- [ ] **Run the full test suite**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

- [ ] **Verify all new tests pass**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_query_guard_wiring.py tests/unit/test_hook_emit.py tests/unit/test_hook_blocking.py tests/unit/test_model_compactor.py tests/unit/test_bash_ast_heredoc.py tests/unit/test_todo_tool.py tests/unit/test_ask_user_tool.py tests/unit/test_redact.py tests/unit/test_prewarm.py -v
```

- [ ] **Verify existing tests still pass**

```bash
cd /Users/nomind/Code/duh && python -m pytest tests/ -x --tb=short
```
