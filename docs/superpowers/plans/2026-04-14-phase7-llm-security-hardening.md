# Phase 7: LLM-Specific Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden D.U.H. against the class of LLM-specific attacks that produced every published agent RCE in 2024-2026 (Claude Code CVE-2025-59536, Codex CVE-2025-59532, Cursor MCPoison, EchoLeak, etc.) by introducing taint-propagating UntrustedStr, confirmation-token gating on dangerous tools, lethal-trifecta capability matrix, per-hook filesystem namespacing, sys.addaudithook telemetry bridge (PEP 578), MCP Unicode normalization + subprocess sandboxing, signed plugin manifests with TOFU trust, and a provider adapter differential fuzzer.

**Architecture:** Eight independently-shippable workstreams with a dependency graph rooted at UntrustedStr. Taint propagates through every string operation; dangerous tools refuse tainted-origin calls without a user-minted confirmation token. Capabilities are declared per-tool and checked at session start. Audit hooks feed telemetry into the existing 28-event hook bus. MCP servers run under the same sandbox as Bash.

**Tech Stack:** Python 3.12+, hypothesis (property tests), sigstore-python (plugin signing), pydantic v2, existing duh.hooks + duh.adapters.sandbox + duh.security. Prerequisite: ADR-053 must be merged first (Phase 6 plan).

---

## File Structure

### New files

**Workstream 7.1 (UntrustedStr):**
- `/Users/nomind/Code/duh/duh/kernel/untrusted.py` — UntrustedStr subclass, TaintSource enum, merge_source helper, ~400 LOC
- `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py` — exhaustive str-method matrix
- `/Users/nomind/Code/duh/tests/property/test_taint_propagation.py` — hypothesis property tests

**Workstream 7.2 (Confirmation tokens):**
- `/Users/nomind/Code/duh/duh/kernel/confirmation.py` — ConfirmationMinter, HMAC-bound tokens, ~200 LOC
- `/Users/nomind/Code/duh/tests/unit/test_confirmation.py`

**Workstream 7.3 (Lethal trifecta):**
- `/Users/nomind/Code/duh/duh/security/trifecta.py` — Capability flag enum, LethalTrifectaError, check_trifecta, ~150 LOC
- `/Users/nomind/Code/duh/tests/unit/test_trifecta.py`

**Workstream 7.4 (Per-hook FS namespacing):**
- `/Users/nomind/Code/duh/tests/unit/test_hook_sandbox.py`
- (extends existing `/Users/nomind/Code/duh/duh/hooks.py` with HookContext)

**Workstream 7.5 (sys.addaudithook bridge):**
- `/Users/nomind/Code/duh/duh/kernel/audit.py` — PEP 578 bridge, ~200 LOC
- `/Users/nomind/Code/duh/tests/unit/test_audit_hook.py`
- `/Users/nomind/Code/duh/tests/benchmarks/test_audit_perf.py` — regression benchmark

**Workstream 7.6 (MCP Unicode + sandbox):**
- `/Users/nomind/Code/duh/duh/adapters/mcp_unicode.py` — NFKC normalization + rejection rules
- `/Users/nomind/Code/duh/duh/adapters/mcp_manifest.py` — server manifest loader
- `/Users/nomind/Code/duh/tests/unit/test_mcp_unicode.py`
- `/Users/nomind/Code/duh/tests/unit/test_mcp_subprocess_sandbox.py`

**Workstream 7.7 (Signed manifests + TOFU):**
- `/Users/nomind/Code/duh/duh/plugins/manifest.py`
- `/Users/nomind/Code/duh/duh/plugins/trust_store.py`
- `/Users/nomind/Code/duh/tests/unit/test_plugin_manifest.py`
- `/Users/nomind/Code/duh/tests/unit/test_plugin_trust.py`

**Workstream 7.8 (Provider differential fuzzer):**
- `/Users/nomind/Code/duh/tests/property/__init__.py`
- `/Users/nomind/Code/duh/tests/property/test_provider_equivalence.py`

### Modified files

**Workstream 7.1 touches ~15 existing files:**
- `/Users/nomind/Code/duh/duh/kernel/context_builder.py`
- `/Users/nomind/Code/duh/duh/kernel/messages.py`
- `/Users/nomind/Code/duh/duh/adapters/simple_compactor.py`
- `/Users/nomind/Code/duh/duh/adapters/model_compactor.py`
- `/Users/nomind/Code/duh/duh/adapters/anthropic.py`
- `/Users/nomind/Code/duh/duh/adapters/openai.py`
- `/Users/nomind/Code/duh/duh/adapters/openai_chatgpt.py`
- `/Users/nomind/Code/duh/duh/adapters/ollama.py`
- `/Users/nomind/Code/duh/duh/adapters/stub_provider.py`
- `/Users/nomind/Code/duh/duh/adapters/native_executor.py`
- `/Users/nomind/Code/duh/duh/adapters/mcp_executor.py`
- `/Users/nomind/Code/duh/duh/tools/read.py`
- `/Users/nomind/Code/duh/duh/tools/grep.py`
- `/Users/nomind/Code/duh/duh/tools/glob_tool.py`
- `/Users/nomind/Code/duh/duh/tools/web_fetch.py`
- `/Users/nomind/Code/duh/duh/cli/repl.py`
- `/Users/nomind/Code/duh/duh/cli/runner.py`
- `/Users/nomind/Code/duh/duh/cli/sdk_runner.py`
- `/Users/nomind/Code/duh/duh/kernel/redact.py`

**Workstream 7.2 touches:** `duh/kernel/engine.py`, `duh/kernel/loop.py`, `duh/kernel/tool.py`, `duh/security/policy.py`, `duh/cli/repl.py`, `duh/cli/sdk_runner.py`, `duh/tools/ask_user_tool.py`

**Workstream 7.3 touches:** `duh/kernel/tool.py`, `duh/tools/*.py` (~25 tools), `duh/kernel/engine.py`, `duh/cli/parser.py`, `duh/config.py`

**Workstream 7.4 touches:** `duh/hooks.py`, `duh/plugins.py`, `duh/security/hooks.py`

**Workstream 7.5 touches:** `duh/kernel/__main__.py`, `duh/hooks.py`

**Workstream 7.6 touches:** `duh/adapters/mcp_executor.py`

**Workstream 7.7 touches:** `duh/plugins.py`

**Workstream 7.8 touches:** every `duh/adapters/*.py` provider (adds `_parse_tool_use_block` classmethod)

---

## Dependency Graph

```
7.1 UntrustedStr (keystone) ─┬─> 7.2 Confirmation tokens
                              └─> 7.3 Lethal trifecta
                                     └─> 7.6 MCP Unicode + sandbox

Independent (can run in parallel with the taint chain):
  7.4 Per-hook FS namespacing
  7.5 sys.addaudithook bridge
  7.7 Signed hook manifests + TOFU
  7.8 Provider differential fuzzer
```

Workstream 7.2 cannot start before 7.1 completes. Workstream 7.6 cannot start before 7.3 completes. Independent workstreams (7.4, 7.5, 7.7, 7.8) can start as soon as ADR-053 is merged. Every commit message should mention the workstream number (e.g. `[7.1]`, `[7.3]`).

---

## Rollout Schedule

| Phase | Weeks | Deliverable |
|---|---|---|
| **7.1** | 1–3 | UntrustedStr + context builder tagging (~25 tasks) |
| **7.2** | 3–4 | Confirmation tokens (~10 tasks) |
| **7.3** | 4 | Lethal trifecta capability matrix (~8 tasks) |
| **7.4** | 5 | Per-hook FS namespacing (~8 tasks) |
| **7.5** | 6 | sys.addaudithook bridge (~6 tasks) |
| **7.6** | 7 | MCP Unicode + subprocess sandbox (~10 tasks) |
| **7.7** | 8–9 | Signed hook manifests + TOFU (~12 tasks) |
| **7.8** | 9–10 | Provider differential fuzzer (~5 tasks) |

---

## Codebase Layout Notes (deviations from spec)

Before implementing, note these actual-vs-spec discrepancies discovered in the current tree:

1. **`duh/kernel/context_builder.py` does not exist.** The spec lists it as a touched file in 7.1, but the actual prompt-assembly code lives in `duh/kernel/loop.py` (the main turn loop) and in the per-provider adapters. Tasks in 7.1 point at `duh/kernel/loop.py` for the context-assembly tagging and at each adapter for model-output tagging. If a `context_builder.py` module is introduced between now and 7.1 start, these tasks should be redirected.
2. **`duh/plugins.py` is a single-file module, not a package.** Workstream 7.7 requires `duh/plugins/manifest.py` and `duh/plugins/trust_store.py`. Task 7.7.1 promotes `duh/plugins.py` to a package `duh/plugins/__init__.py` that re-exports the existing public names, then adds new sibling modules.
3. **`duh/security/` does not exist yet.** ADR-053 (Phase 6) is a hard prerequisite; Workstreams 7.2, 7.3 and 7.6 patch `duh/security/policy.py` and `duh/security/__init__.py` that Phase 6 creates. Do not start Phase 7 until Phase 6 is merged.
4. **No `duh/security/hooks.py` yet.** Phase 6 creates it; 7.4 extends it.

---

## Workstream 7.1: UntrustedStr + context builder tagging

**Depends on:** ADR-053 (Phase 6) merged.
**Blocks:** 7.2, 7.3, 7.6.
**Tasks:** 26.

### Task 7.1.1: Create `TaintSource` enum, `UNTAINTED_SOURCES`, `merge_source`

**Files:**
- Create: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Create: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
# /Users/nomind/Code/duh/tests/unit/test_untrusted_str.py
"""Exhaustive tests for duh.kernel.untrusted — TaintSource + UntrustedStr."""

from __future__ import annotations

import pytest

from duh.kernel.untrusted import (
    TaintSource,
    UNTAINTED_SOURCES,
    merge_source,
)


def test_taint_source_values() -> None:
    assert TaintSource.USER_INPUT.value == "user_input"
    assert TaintSource.MODEL_OUTPUT.value == "model_output"
    assert TaintSource.TOOL_OUTPUT.value == "tool_output"
    assert TaintSource.FILE_CONTENT.value == "file_content"
    assert TaintSource.MCP_OUTPUT.value == "mcp_output"
    assert TaintSource.NETWORK.value == "network"
    assert TaintSource.SYSTEM.value == "system"


def test_untainted_sources_contents() -> None:
    assert TaintSource.USER_INPUT in UNTAINTED_SOURCES
    assert TaintSource.SYSTEM in UNTAINTED_SOURCES
    assert TaintSource.MODEL_OUTPUT not in UNTAINTED_SOURCES
    assert TaintSource.TOOL_OUTPUT not in UNTAINTED_SOURCES
    assert TaintSource.FILE_CONTENT not in UNTAINTED_SOURCES
    assert TaintSource.MCP_OUTPUT not in UNTAINTED_SOURCES
    assert TaintSource.NETWORK not in UNTAINTED_SOURCES


def test_merge_source_both_untainted_prefers_first() -> None:
    class _S(str):
        _source = TaintSource.SYSTEM
    class _U(str):
        _source = TaintSource.USER_INPUT
    a, b = _S("x"), _U("y")
    assert merge_source(a, b) == TaintSource.SYSTEM


def test_merge_source_tainted_wins_over_untainted() -> None:
    class _S(str):
        _source = TaintSource.SYSTEM
    class _M(str):
        _source = TaintSource.MODEL_OUTPUT
    assert merge_source(_S("x"), _M("y")) == TaintSource.MODEL_OUTPUT
    assert merge_source(_M("y"), _S("x")) == TaintSource.MODEL_OUTPUT


def test_merge_source_both_tainted_first_wins() -> None:
    class _M(str):
        _source = TaintSource.MODEL_OUTPUT
    class _F(str):
        _source = TaintSource.FILE_CONTENT
    assert merge_source(_M("a"), _F("b")) == TaintSource.MODEL_OUTPUT


def test_merge_source_plain_str_defaults_to_system() -> None:
    assert merge_source("a", "b") == TaintSource.SYSTEM
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

Expected failure: `ModuleNotFoundError: No module named 'duh.kernel.untrusted'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# /Users/nomind/Code/duh/duh/kernel/untrusted.py
"""UntrustedStr — taint-propagating str subclass (ADR-054, workstream 7.1).

A str that remembers where its bytes came from. Every str-returning method is
overridden to produce an UntrustedStr with the same (or merged) TaintSource,
so a path like

    model_out = UntrustedStr(provider_stream, TaintSource.MODEL_OUTPUT)
    rendered  = f"prompt={model_out.upper()}"

carries the taint from the provider straight into rendered's source tag. The
policy resolver can then refuse dangerous tool calls that traced through a
tainted ancestor.

Environment variables:
  DUH_TAINT_DEBUG=1   — print every str op that preserves/merges taint
  DUH_TAINT_STRICT=1  — raise TaintLossError on any silent tag drop
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any

__all__ = [
    "TaintSource",
    "UNTAINTED_SOURCES",
    "UntrustedStr",
    "TaintLossError",
    "merge_source",
]


class TaintSource(str, Enum):
    USER_INPUT = "user_input"      # untainted — REPL, /continue, AskUserQuestion
    MODEL_OUTPUT = "model_output"  # tainted
    TOOL_OUTPUT = "tool_output"    # tainted
    FILE_CONTENT = "file_content"  # tainted
    MCP_OUTPUT = "mcp_output"      # tainted
    NETWORK = "network"            # tainted
    SYSTEM = "system"              # untainted — D.U.H. prompts, config, skills


UNTAINTED_SOURCES: frozenset[TaintSource] = frozenset(
    {TaintSource.USER_INPUT, TaintSource.SYSTEM}
)


class TaintLossError(RuntimeError):
    """Raised when DUH_TAINT_STRICT=1 and a str op silently drops taint."""


def _strict() -> bool:
    return os.environ.get("DUH_TAINT_STRICT", "") == "1"


def _debug() -> bool:
    return os.environ.get("DUH_TAINT_DEBUG", "") == "1"


def merge_source(a: Any, b: Any) -> TaintSource:
    """Combine two source tags; tainted wins over untainted."""
    a_src = getattr(a, "_source", TaintSource.SYSTEM)
    b_src = getattr(b, "_source", TaintSource.SYSTEM)
    if a_src in UNTAINTED_SOURCES and b_src in UNTAINTED_SOURCES:
        return a_src
    if a_src in UNTAINTED_SOURCES:
        return b_src
    if b_src in UNTAINTED_SOURCES:
        return a_src
    return a_src
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

Expected: 6 passed.

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] TaintSource enum + merge_source helper (ADR-054)"
```

---

### Task 7.1.2: Bare `UntrustedStr` subclass with `__new__` and `source` property

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test (append to existing file)**

```python
# append to /Users/nomind/Code/duh/tests/unit/test_untrusted_str.py

from duh.kernel.untrusted import UntrustedStr


def test_untrusted_str_constructs_from_str() -> None:
    s = UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
    assert str(s) == "hello"
    assert s.source == TaintSource.MODEL_OUTPUT


def test_untrusted_str_default_source_is_model_output() -> None:
    s = UntrustedStr("hello")
    assert s.source == TaintSource.MODEL_OUTPUT


def test_untrusted_str_is_str_subclass() -> None:
    s = UntrustedStr("hello", TaintSource.USER_INPUT)
    assert isinstance(s, str)
    assert isinstance(s, UntrustedStr)


def test_untrusted_str_every_taint_source_round_trips() -> None:
    for src in TaintSource:
        s = UntrustedStr("x", src)
        assert s.source is src
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py::test_untrusted_str_constructs_from_str -x -q --timeout=30 --timeout-method=thread
```

Expected failure: `ImportError: cannot import name 'UntrustedStr'`.

- [ ] **Step 3: Write the minimal implementation (append class to untrusted.py)**

```python
# append to /Users/nomind/Code/duh/duh/kernel/untrusted.py

class UntrustedStr(str):
    """str subclass carrying a TaintSource tag.

    See module docstring for propagation semantics. This class intentionally
    defines __slots__ to avoid adding a __dict__ per instance — this keeps
    the per-operation overhead bounded."""

    __slots__ = ("_source",)

    _source: TaintSource

    def __new__(
        cls,
        value: object = "",
        source: TaintSource = TaintSource.MODEL_OUTPUT,
    ) -> "UntrustedStr":
        instance = super().__new__(cls, value)
        instance._source = source
        return instance

    @property
    def source(self) -> TaintSource:
        return self._source
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] bare UntrustedStr subclass with source property"
```

---

### Task 7.1.3: `is_tainted()` method

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
# append to /Users/nomind/Code/duh/tests/unit/test_untrusted_str.py

def test_is_tainted_per_source() -> None:
    assert UntrustedStr("x", TaintSource.USER_INPUT).is_tainted() is False
    assert UntrustedStr("x", TaintSource.SYSTEM).is_tainted() is False
    assert UntrustedStr("x", TaintSource.MODEL_OUTPUT).is_tainted() is True
    assert UntrustedStr("x", TaintSource.TOOL_OUTPUT).is_tainted() is True
    assert UntrustedStr("x", TaintSource.FILE_CONTENT).is_tainted() is True
    assert UntrustedStr("x", TaintSource.MCP_OUTPUT).is_tainted() is True
    assert UntrustedStr("x", TaintSource.NETWORK).is_tainted() is True
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py::test_is_tainted_per_source -x -q --timeout=30 --timeout-method=thread
```

Expected failure: `AttributeError: 'UntrustedStr' object has no attribute 'is_tainted'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# insert into UntrustedStr class body

    def is_tainted(self) -> bool:
        return self._source not in UNTAINTED_SOURCES
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr.is_tainted() method"
```

---

### Task 7.1.4: `_wrap()` helper + `__add__` / `__radd__` propagation

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
# append to /Users/nomind/Code/duh/tests/unit/test_untrusted_str.py

def test_add_preserves_source_left() -> None:
    a = UntrustedStr("hello ", TaintSource.MODEL_OUTPUT)
    result = a + "world"
    assert isinstance(result, UntrustedStr)
    assert str(result) == "hello world"
    assert result.source == TaintSource.MODEL_OUTPUT


def test_add_preserves_source_right_with_merge() -> None:
    a = UntrustedStr("hello ", TaintSource.USER_INPUT)
    b = UntrustedStr("world", TaintSource.MODEL_OUTPUT)
    result = a + b
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.MODEL_OUTPUT


def test_radd_preserves_source() -> None:
    b = UntrustedStr("world", TaintSource.TOOL_OUTPUT)
    result = "hello " + b
    assert isinstance(result, UntrustedStr)
    assert str(result) == "hello world"
    assert result.source == TaintSource.TOOL_OUTPUT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "add_preserves or radd_preserves"
```

Expected failure: concatenation returns plain `str`, `isinstance(result, UntrustedStr)` is False.

- [ ] **Step 3: Write the minimal implementation**

```python
# insert into UntrustedStr class body

    def _wrap(self, value: object, source: TaintSource | None = None) -> "UntrustedStr":
        src = source if source is not None else self._source
        return UntrustedStr(value, src)

    def __add__(self, other: object) -> "UntrustedStr":
        result = super().__add__(other)  # type: ignore[arg-type]
        return self._wrap(result, merge_source(self, other))

    def __radd__(self, other: object) -> "UntrustedStr":
        result = other.__add__(self) if isinstance(other, str) else NotImplemented  # type: ignore[arg-type]
        if result is NotImplemented:
            return NotImplemented  # type: ignore[return-value]
        return self._wrap(result, merge_source(other, self))
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr __add__/__radd__ preserve taint"
```

---

### Task 7.1.5: `__mod__` (%-format) propagation

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
# append to /Users/nomind/Code/duh/tests/unit/test_untrusted_str.py

def test_mod_format_preserves_source() -> None:
    tmpl = UntrustedStr("hello %s", TaintSource.MODEL_OUTPUT)
    result = tmpl % "world"
    assert isinstance(result, UntrustedStr)
    assert str(result) == "hello world"
    assert result.source == TaintSource.MODEL_OUTPUT


def test_mod_format_merges_with_tainted_arg() -> None:
    tmpl = UntrustedStr("x=%s", TaintSource.SYSTEM)
    arg = UntrustedStr("evil", TaintSource.MODEL_OUTPUT)
    result = tmpl % arg
    assert result.source == TaintSource.MODEL_OUTPUT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py::test_mod_format_preserves_source -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 3: Write the minimal implementation**

```python
# insert into UntrustedStr class body

    def __mod__(self, args: object) -> "UntrustedStr":
        result = super().__mod__(args)
        src = self._source
        if isinstance(args, tuple):
            for item in args:
                src = merge_source(UntrustedStr("", src), item)
        else:
            src = merge_source(UntrustedStr("", src), args)
        return UntrustedStr(result, src)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr __mod__ preserves taint"
```

---

### Task 7.1.6: `__mul__` repetition propagation

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_mul_preserves_source() -> None:
    a = UntrustedStr("ab", TaintSource.FILE_CONTENT)
    result = a * 3
    assert isinstance(result, UntrustedStr)
    assert str(result) == "ababab"
    assert result.source == TaintSource.FILE_CONTENT


def test_rmul_preserves_source() -> None:
    a = UntrustedStr("ab", TaintSource.FILE_CONTENT)
    result = 2 * a
    assert isinstance(result, UntrustedStr)
    assert str(result) == "abab"
    assert result.source == TaintSource.FILE_CONTENT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "mul_preserves or rmul_preserves"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def __mul__(self, n: int) -> "UntrustedStr":
        return UntrustedStr(super().__mul__(n), self._source)

    def __rmul__(self, n: int) -> "UntrustedStr":
        return UntrustedStr(super().__rmul__(n), self._source)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr __mul__/__rmul__ preserve taint"
```

---

### Task 7.1.7: `format` and `format_map` propagation

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_format_preserves_source() -> None:
    tmpl = UntrustedStr("hi {}", TaintSource.MODEL_OUTPUT)
    result = tmpl.format("bob")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.MODEL_OUTPUT


def test_format_merges_tainted_arg() -> None:
    tmpl = UntrustedStr("hi {}", TaintSource.SYSTEM)
    arg = UntrustedStr("evil", TaintSource.MODEL_OUTPUT)
    result = tmpl.format(arg)
    assert result.source == TaintSource.MODEL_OUTPUT


def test_format_map_preserves_source() -> None:
    tmpl = UntrustedStr("{x}", TaintSource.TOOL_OUTPUT)
    result = tmpl.format_map({"x": "hi"})
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.TOOL_OUTPUT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "format_preserves or format_merges or format_map_preserves"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def format(self, *args: object, **kwargs: object) -> "UntrustedStr":  # type: ignore[override]
        result = super().format(*args, **kwargs)
        src = self._source
        for a in args:
            src = merge_source(UntrustedStr("", src), a)
        for v in kwargs.values():
            src = merge_source(UntrustedStr("", src), v)
        return UntrustedStr(result, src)

    def format_map(self, mapping: object) -> "UntrustedStr":  # type: ignore[override]
        result = super().format_map(mapping)
        src = self._source
        try:
            for v in mapping.values():  # type: ignore[attr-defined]
                src = merge_source(UntrustedStr("", src), v)
        except AttributeError:
            pass
        return UntrustedStr(result, src)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr format/format_map preserve taint"
```

---

### Task 7.1.8: `join` propagation

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_join_preserves_self_source() -> None:
    sep = UntrustedStr(", ", TaintSource.SYSTEM)
    result = sep.join(["a", "b", "c"])
    assert isinstance(result, UntrustedStr)
    assert str(result) == "a, b, c"


def test_join_merges_with_tainted_parts() -> None:
    sep = UntrustedStr(", ", TaintSource.SYSTEM)
    parts = [UntrustedStr("evil", TaintSource.MODEL_OUTPUT), "clean"]
    result = sep.join(parts)
    assert result.source == TaintSource.MODEL_OUTPUT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "join_"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def join(self, iterable) -> "UntrustedStr":  # type: ignore[override]
        parts = list(iterable)
        result = super().join(parts)
        src = self._source
        for p in parts:
            src = merge_source(UntrustedStr("", src), p)
        return UntrustedStr(result, src)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr.join preserves + merges taint"
```

---

### Task 7.1.9: `replace` propagation with arg merging

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_replace_preserves_source() -> None:
    s = UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
    result = s.replace("l", "L")
    assert isinstance(result, UntrustedStr)
    assert str(result) == "heLLo"
    assert result.source == TaintSource.MODEL_OUTPUT


def test_replace_merges_tainted_new() -> None:
    s = UntrustedStr("x", TaintSource.SYSTEM)
    evil = UntrustedStr("y", TaintSource.FILE_CONTENT)
    result = s.replace("x", evil)
    assert result.source == TaintSource.FILE_CONTENT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "replace_"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def replace(self, old, new, count=-1) -> "UntrustedStr":  # type: ignore[override]
        result = super().replace(old, new, count)
        src = merge_source(self, new)
        src = merge_source(UntrustedStr("", src), old)
        return UntrustedStr(result, src)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr.replace preserves + merges taint"
```

---

### Task 7.1.10: `strip`, `lstrip`, `rstrip` propagation

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_strip_family_preserves_source() -> None:
    for src in (TaintSource.MODEL_OUTPUT, TaintSource.USER_INPUT):
        s = UntrustedStr("  hi  ", src)
        for fn in (s.strip, s.lstrip, s.rstrip):
            result = fn()
            assert isinstance(result, UntrustedStr), f"{fn} lost type"
            assert result.source == src, f"{fn} lost source"
    assert UntrustedStr("xxhix", TaintSource.FILE_CONTENT).strip("x") == "hi"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "strip_family"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def strip(self, chars=None) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().strip(chars), self._source)

    def lstrip(self, chars=None) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().lstrip(chars), self._source)

    def rstrip(self, chars=None) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().rstrip(chars), self._source)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr strip family preserves taint"
```

---

### Task 7.1.11: `split`, `rsplit`, `splitlines` propagation

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_split_returns_list_of_untrusted_str() -> None:
    s = UntrustedStr("a,b,c", TaintSource.MODEL_OUTPUT)
    parts = s.split(",")
    assert len(parts) == 3
    for p in parts:
        assert isinstance(p, UntrustedStr)
        assert p.source == TaintSource.MODEL_OUTPUT
    assert [str(p) for p in parts] == ["a", "b", "c"]


def test_rsplit_returns_untrusted() -> None:
    s = UntrustedStr("a.b.c", TaintSource.TOOL_OUTPUT)
    parts = s.rsplit(".", 1)
    assert all(isinstance(p, UntrustedStr) for p in parts)
    assert parts[0].source == TaintSource.TOOL_OUTPUT


def test_splitlines_returns_untrusted() -> None:
    s = UntrustedStr("x\ny\n", TaintSource.FILE_CONTENT)
    lines = s.splitlines()
    assert len(lines) == 2
    for line in lines:
        assert isinstance(line, UntrustedStr)
        assert line.source == TaintSource.FILE_CONTENT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "split_returns or rsplit_returns or splitlines_returns"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def split(self, sep=None, maxsplit=-1) -> list["UntrustedStr"]:  # type: ignore[override]
        return [UntrustedStr(p, self._source) for p in super().split(sep, maxsplit)]

    def rsplit(self, sep=None, maxsplit=-1) -> list["UntrustedStr"]:  # type: ignore[override]
        return [UntrustedStr(p, self._source) for p in super().rsplit(sep, maxsplit)]

    def splitlines(self, keepends=False) -> list["UntrustedStr"]:  # type: ignore[override]
        return [UntrustedStr(p, self._source) for p in super().splitlines(keepends)]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr split/rsplit/splitlines propagate taint"
```

---

### Task 7.1.12: Case methods (`lower`, `upper`, `title`, `casefold`, `capitalize`, `swapcase`)

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest as _pytest


@_pytest.mark.parametrize("method,expected", [
    ("lower", "hello"),
    ("upper", "HELLO"),
    ("title", "Hello"),
    ("casefold", "hello"),
    ("capitalize", "Hello"),
    ("swapcase", "HELLO"),
])
def test_case_methods_preserve_source(method, expected) -> None:
    s = UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
    result = getattr(s, method)()
    assert isinstance(result, UntrustedStr)
    assert str(result) == expected
    assert result.source == TaintSource.MODEL_OUTPUT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "case_methods_preserve"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def lower(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().lower(), self._source)

    def upper(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().upper(), self._source)

    def title(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().title(), self._source)

    def casefold(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().casefold(), self._source)

    def capitalize(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().capitalize(), self._source)

    def swapcase(self) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().swapcase(), self._source)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr case methods preserve taint"
```

---

### Task 7.1.13: Padding methods (`expandtabs`, `center`, `ljust`, `rjust`, `zfill`)

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_expandtabs_preserves_source() -> None:
    s = UntrustedStr("a\tb", TaintSource.TOOL_OUTPUT)
    assert isinstance(s.expandtabs(4), UntrustedStr)
    assert s.expandtabs(4).source == TaintSource.TOOL_OUTPUT


def test_justify_preserve_source() -> None:
    s = UntrustedStr("x", TaintSource.FILE_CONTENT)
    for r in (s.center(5), s.ljust(5), s.rjust(5)):
        assert isinstance(r, UntrustedStr)
        assert r.source == TaintSource.FILE_CONTENT
    assert str(s.center(5)) == "  x  "


def test_zfill_preserves_source() -> None:
    s = UntrustedStr("42", TaintSource.MODEL_OUTPUT)
    assert s.zfill(5).source == TaintSource.MODEL_OUTPUT
    assert str(s.zfill(5)) == "00042"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "expandtabs or justify_preserve or zfill_preserve"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def expandtabs(self, tabsize=8) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().expandtabs(tabsize), self._source)

    def center(self, width, fillchar=" ") -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().center(width, fillchar), self._source)

    def ljust(self, width, fillchar=" ") -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().ljust(width, fillchar), self._source)

    def rjust(self, width, fillchar=" ") -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().rjust(width, fillchar), self._source)

    def zfill(self, width) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().zfill(width), self._source)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr padding methods preserve taint"
```

---

### Task 7.1.14: `translate`, `encode`, `removeprefix`, `removesuffix`

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_translate_preserves_source() -> None:
    s = UntrustedStr("abc", TaintSource.MCP_OUTPUT)
    table = str.maketrans("a", "A")
    result = s.translate(table)
    assert isinstance(result, UntrustedStr)
    assert str(result) == "Abc"
    assert result.source == TaintSource.MCP_OUTPUT


def test_encode_returns_plain_bytes() -> None:
    s = UntrustedStr("hi", TaintSource.NETWORK)
    result = s.encode("utf-8")
    assert isinstance(result, bytes)
    assert result == b"hi"


def test_removeprefix_preserves_source() -> None:
    s = UntrustedStr("pfx-body", TaintSource.FILE_CONTENT)
    result = s.removeprefix("pfx-")
    assert isinstance(result, UntrustedStr)
    assert str(result) == "body"
    assert result.source == TaintSource.FILE_CONTENT


def test_removesuffix_preserves_source() -> None:
    s = UntrustedStr("body-sfx", TaintSource.FILE_CONTENT)
    result = s.removesuffix("-sfx")
    assert isinstance(result, UntrustedStr)
    assert str(result) == "body"
    assert result.source == TaintSource.FILE_CONTENT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "translate_preserves or encode_returns or removeprefix_preserves or removesuffix_preserves"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def translate(self, table) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().translate(table), self._source)

    # encode returns bytes, not str — pass through unchanged.

    def removeprefix(self, prefix) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().removeprefix(prefix), self._source)

    def removesuffix(self, suffix) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().removesuffix(suffix), self._source)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr translate/encode/remove[pre|suf]fix"
```

---

### Task 7.1.15: `__getitem__` slicing propagation

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_slice_preserves_source() -> None:
    s = UntrustedStr("helloworld", TaintSource.MODEL_OUTPUT)
    result = s[5:]
    assert isinstance(result, UntrustedStr)
    assert str(result) == "world"
    assert result.source == TaintSource.MODEL_OUTPUT


def test_index_preserves_source() -> None:
    s = UntrustedStr("abc", TaintSource.FILE_CONTENT)
    result = s[1]
    assert isinstance(result, UntrustedStr)
    assert str(result) == "b"
    assert result.source == TaintSource.FILE_CONTENT


def test_stride_preserves_source() -> None:
    s = UntrustedStr("abcdef", TaintSource.TOOL_OUTPUT)
    assert s[::2].source == TaintSource.TOOL_OUTPUT
    assert str(s[::2]) == "ace"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "slice_preserves or index_preserves or stride_preserves"
```

- [ ] **Step 3: Write the minimal implementation**

```python
    def __getitem__(self, key) -> "UntrustedStr":  # type: ignore[override]
        return UntrustedStr(super().__getitem__(key), self._source)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] UntrustedStr __getitem__ preserves taint"
```

---

### Task 7.1.16: Pass-through tests for non-str-returning methods

**Files:**
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_non_str_methods_pass_through() -> None:
    s = UntrustedStr("hello world", TaintSource.MODEL_OUTPUT)
    # Methods returning int / bool / list[int] / not str
    assert len(s) == 11
    assert s.count("l") == 3
    assert s.startswith("hello") is True
    assert s.endswith("world") is True
    assert s.find("world") == 6
    assert s.rfind("l") == 9
    assert s.index("o") == 4
    assert s.rindex("o") == 7
    assert UntrustedStr("123", TaintSource.FILE_CONTENT).isdigit() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).isalpha() is True
    assert UntrustedStr("   ", TaintSource.FILE_CONTENT).isspace() is True
    assert UntrustedStr("Hi There", TaintSource.FILE_CONTENT).istitle() is True
    assert UntrustedStr("ABC", TaintSource.FILE_CONTENT).isupper() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).islower() is True
    assert UntrustedStr("⅒", TaintSource.FILE_CONTENT).isnumeric() is True
    assert UntrustedStr("3", TaintSource.FILE_CONTENT).isdecimal() is True
    assert UntrustedStr("abc1", TaintSource.FILE_CONTENT).isalnum() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).isidentifier() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).isprintable() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).isascii() is True
    assert hash(UntrustedStr("x", TaintSource.MODEL_OUTPUT)) == hash("x")
    assert bool(UntrustedStr("x", TaintSource.MODEL_OUTPUT)) is True
    assert bool(UntrustedStr("", TaintSource.MODEL_OUTPUT)) is False
    assert "lo" in UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
    assert list(iter(UntrustedStr("ab", TaintSource.MODEL_OUTPUT))) == ["a", "b"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py::test_non_str_methods_pass_through -x -q --timeout=30 --timeout-method=thread
```

Expected: test passes immediately because these methods inherit from `str`. This task is an assertion of current behavior; if any method fails, the implementation must be corrected.

- [ ] **Step 3: Write the minimal implementation**

No implementation needed unless step 2 reveals a drift. Add an explicit comment block in `untrusted.py` documenting which methods are inherited unchanged.

```python
# (inside UntrustedStr class body — documentation-only)
# Methods inherited from str unchanged (return non-str, no taint to carry):
#   __len__, __hash__, __bool__, __contains__, __iter__, __eq__, __ne__,
#   __lt__, __le__, __gt__, __ge__, __repr__,
#   count, startswith, endswith, find, rfind, index, rindex,
#   isdigit, isalpha, isspace, istitle, isupper, islower,
#   isnumeric, isdecimal, isalnum, isidentifier, isprintable, isascii.
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "test(kernel): [7.1] assert non-str-returning methods pass through"
```

---

### Task 7.1.17: Hypothesis property test — arbitrary op sequences preserve tightest taint

**Files:**
- Create: `/Users/nomind/Code/duh/tests/property/test_taint_propagation.py`

- [ ] **Step 1: Write the failing test**

```python
# /Users/nomind/Code/duh/tests/property/test_taint_propagation.py
"""Property test: arbitrary sequences of str operations preserve taint.

Pick a starting UntrustedStr and a sequence of ops. After running every op,
the result must still be an UntrustedStr whose source is at least as tainted
as the most-tainted input that flowed in."""

from __future__ import annotations

from hypothesis import given, strategies as st

from duh.kernel.untrusted import (
    TaintSource,
    UNTAINTED_SOURCES,
    UntrustedStr,
)


def _tainted(src: TaintSource) -> bool:
    return src not in UNTAINTED_SOURCES


sources = st.sampled_from(list(TaintSource))
safe_str = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=16)


@given(value=safe_str, src=sources)
def test_starting_source_is_preserved_under_case_ops(value, src) -> None:
    s = UntrustedStr(value, src)
    for op in (str.lower, str.upper, str.casefold, str.title, str.capitalize, str.swapcase):
        out = op(s)
        assert isinstance(out, UntrustedStr)
        assert out.source == src


@given(value=safe_str, src=sources)
def test_slicing_preserves_source(value, src) -> None:
    s = UntrustedStr(value, src)
    assert s[:].source == src
    assert s[1:].source == src
    assert s[::2].source == src


@given(a_val=safe_str, a_src=sources, b_val=safe_str, b_src=sources)
def test_concat_retains_most_tainted(a_val, a_src, b_val, b_src) -> None:
    a = UntrustedStr(a_val, a_src)
    b = UntrustedStr(b_val, b_src)
    result = a + b
    assert isinstance(result, UntrustedStr)
    if _tainted(a_src) or _tainted(b_src):
        assert _tainted(result.source)


@given(parts=st.lists(safe_str, min_size=1, max_size=5), sep_src=sources)
def test_join_preserves_tightest_taint(parts, sep_src) -> None:
    sep = UntrustedStr(",", sep_src)
    result = sep.join(UntrustedStr(p, TaintSource.MODEL_OUTPUT) for p in parts)
    assert isinstance(result, UntrustedStr)
    assert _tainted(result.source)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/property/test_taint_propagation.py -x -q --timeout=30 --timeout-method=thread
```

Expected: passes if tasks 7.1.1–7.1.16 are complete. If any property fails, fix the offending override before proceeding.

- [ ] **Step 3: Write the minimal implementation**

None needed — any failure must be fixed in the offending override.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/property/test_taint_propagation.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add tests/property/test_taint_propagation.py && git commit -m "test(property): [7.1] hypothesis taint propagation over op sequences"
```

---

### Task 7.1.18: `DUH_TAINT_DEBUG` + `DUH_TAINT_STRICT` env var hooks

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/kernel/untrusted.py`
- Modify: `/Users/nomind/Code/duh/tests/unit/test_untrusted_str.py`

- [ ] **Step 1: Write the failing test**

```python
def test_taint_strict_raises_on_drop(monkeypatch) -> None:
    from duh.kernel.untrusted import TaintLossError, _record_drop

    monkeypatch.setenv("DUH_TAINT_STRICT", "1")
    with pytest.raises(TaintLossError):
        _record_drop("fake_op", "expected_source")


def test_taint_debug_prints(monkeypatch, capsys) -> None:
    from duh.kernel.untrusted import _record_preserve

    monkeypatch.setenv("DUH_TAINT_DEBUG", "1")
    _record_preserve("my_op", TaintSource.MODEL_OUTPUT)
    out = capsys.readouterr().err
    assert "my_op" in out
    assert "model_output" in out
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread -k "taint_strict or taint_debug"
```

- [ ] **Step 3: Write the minimal implementation**

```python
# add near _strict / _debug helpers in untrusted.py
import sys as _sys

def _record_preserve(op: str, src: TaintSource) -> None:
    if _debug():
        print(f"[taint] preserved {op} src={src.value}", file=_sys.stderr)

def _record_drop(op: str, expected_src: object) -> None:
    if _strict():
        raise TaintLossError(f"taint dropped by {op}; expected src={expected_src}")
    if _debug():
        print(f"[taint] DROPPED {op} expected={expected_src}", file=_sys.stderr)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_untrusted_str.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/kernel/untrusted.py tests/unit/test_untrusted_str.py && git commit -m "feat(kernel): [7.1] DUH_TAINT_DEBUG + DUH_TAINT_STRICT env vars"
```

---

### Task 7.1.19: Tag REPL user input (`duh/cli/repl.py`)

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/cli/repl.py`
- Create: `/Users/nomind/Code/duh/tests/unit/test_repl_taint.py`

- [ ] **Step 1: Write the failing test**

```python
# /Users/nomind/Code/duh/tests/unit/test_repl_taint.py
"""Every user input value handed to the REPL message queue is wrapped as
UntrustedStr with TaintSource.USER_INPUT."""

from __future__ import annotations

from duh.cli.repl import _wrap_user_input
from duh.kernel.untrusted import TaintSource, UntrustedStr


def test_wrap_user_input_returns_untrusted() -> None:
    result = _wrap_user_input("hello")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.USER_INPUT


def test_wrap_user_input_idempotent_preserves_existing_tag() -> None:
    pre = UntrustedStr("hi", TaintSource.USER_INPUT)
    result = _wrap_user_input(pre)
    assert result.source == TaintSource.USER_INPUT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_repl_taint.py -x -q --timeout=30 --timeout-method=thread
```

Expected failure: `ImportError: cannot import name '_wrap_user_input'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# near the top of /Users/nomind/Code/duh/duh/cli/repl.py

from duh.kernel.untrusted import TaintSource, UntrustedStr


def _wrap_user_input(raw: str) -> UntrustedStr:
    """Tag raw REPL input as USER_INPUT taint-source."""
    if isinstance(raw, UntrustedStr):
        return raw
    return UntrustedStr(raw, TaintSource.USER_INPUT)
```

Then update every site that does `line = input(...)` or `line = await prompt(...)` in `repl.py` to push `_wrap_user_input(line)` onto the message queue.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_repl_taint.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/cli/repl.py tests/unit/test_repl_taint.py && git commit -m "feat(cli): [7.1] tag REPL user input as TaintSource.USER_INPUT"
```

---

### Task 7.1.20: Tag `-p/--prompt` flag and stream-json user messages

**Files:**
- Modify: `/Users/nomind/Code/duh/duh/cli/runner.py`
- Modify: `/Users/nomind/Code/duh/duh/cli/sdk_runner.py`
- Create: `/Users/nomind/Code/duh/tests/unit/test_runner_taint.py`

- [ ] **Step 1: Write the failing test**

```python
# /Users/nomind/Code/duh/tests/unit/test_runner_taint.py
"""Taint tagging for CLI prompt flag and SDK user messages."""

from __future__ import annotations

from duh.cli.runner import wrap_prompt_flag
from duh.cli.sdk_runner import wrap_stream_user_message
from duh.kernel.untrusted import TaintSource, UntrustedStr


def test_prompt_flag_tagged_user_input() -> None:
    result = wrap_prompt_flag("hello")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.USER_INPUT


def test_stream_user_message_tagged_user_input() -> None:
    result = wrap_stream_user_message({"role": "user", "content": "hi"})
    # Content string tagged
    assert isinstance(result["content"], UntrustedStr)
    assert result["content"].source == TaintSource.USER_INPUT
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_runner_taint.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 3: Write the minimal implementation**

```python
# /Users/nomind/Code/duh/duh/cli/runner.py — add near imports
from duh.kernel.untrusted import TaintSource, UntrustedStr


def wrap_prompt_flag(value: str) -> UntrustedStr:
    if isinstance(value, UntrustedStr):
        return value
    return UntrustedStr(value, TaintSource.USER_INPUT)
```

```python
# /Users/nomind/Code/duh/duh/cli/sdk_runner.py — add near imports
from duh.kernel.untrusted import TaintSource, UntrustedStr


def wrap_stream_user_message(msg: dict) -> dict:
    content = msg.get("content", "")
    if isinstance(content, str) and not isinstance(content, UntrustedStr):
        msg = dict(msg)
        msg["content"] = UntrustedStr(content, TaintSource.USER_INPUT)
    return msg
```

Then call `wrap_prompt_flag()` on the `-p` argument in `runner.main()` and `wrap_stream_user_message()` on every incoming stream-json user message in `sdk_runner.py`.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/unit/test_runner_taint.py -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
cd /Users/nomind/Code/duh && .venv/bin/python -m pytest tests/ -x -q --timeout=30 --timeout-method=thread
```

- [ ] **Step 6: Commit**

```bash
cd /Users/nomind/Code/duh && git add duh/cli/runner.py duh/cli/sdk_runner.py tests/unit/test_runner_taint.py && git commit -m "feat(cli): [7.1] tag -p prompt and SDK user messages"
```

---
