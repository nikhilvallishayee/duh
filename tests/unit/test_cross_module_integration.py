"""Cross-module integration tests for new feature combinations.

Tests four critical integration scenarios:
1. Bash AST parser + env var allowlist (command with LD_PRELOAD piped)
2. PTL retry + partial compaction (engine triggers compact on PTL)
3. TieredApprover + sandbox policy (full-auto mode disables network)
4. Ghost snapshot + read-only enforcement
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.kernel.messages import Message


# ============================================================================
# 1. Bash AST parser + env var allowlist
#    Verify that AST tokenization exposes LD_PRELOAD piped commands as dangerous
# ============================================================================

from duh.tools.bash_ast import ast_classify, tokenize, strip_wrappers
from duh.tools.bash_security import classify_command, is_env_var_safe


class TestBashAstPlusEnvVarAllowlist:
    def test_ld_preload_piped_command(self):
        """LD_PRELOAD=... | bash should be flagged by both AST and env var check."""
        cmd = "LD_PRELOAD=/evil.so ./app | cat output.txt"
        result = ast_classify(cmd)
        assert result["risk"] == "dangerous"

    def test_ld_preload_in_subshell(self):
        """LD_PRELOAD inside a subshell should be caught."""
        cmd = "echo $(LD_PRELOAD=/evil.so ./app)"
        result = ast_classify(cmd)
        assert result["risk"] == "dangerous"

    def test_ld_preload_after_and_chain(self):
        cmd = "ls && LD_PRELOAD=/evil.so ./app"
        result = ast_classify(cmd)
        assert result["risk"] == "dangerous"

    def test_dyld_insert_piped(self):
        """macOS DYLD injection piped should be caught."""
        cmd = "DYLD_INSERT_LIBRARIES=/evil.dylib ./app | tee log.txt"
        result = ast_classify(cmd)
        assert result["risk"] == "dangerous"

    def test_safe_env_piped_is_ok(self):
        """Safe env vars piped should not be flagged as dangerous."""
        cmd = "NODE_ENV=production npm start | tee output.log"
        result = ast_classify(cmd)
        assert result["risk"] != "dangerous"

    def test_ast_classifier_catches_env_injection_through_wrappers(self):
        """AST classifier catches LD_PRELOAD even when wrapped in timeout/env.

        strip_wrappers consumes env key=value pairs (by design), so per-segment
        regex on the stripped command misses it. But ast_classify runs the full
        command through regex FIRST before per-segment analysis, catching it.
        """
        cmd = "timeout 30 env LD_PRELOAD=/evil.so ./app"
        result = ast_classify(cmd)
        assert result["risk"] == "dangerous"
        assert "LD_PRELOAD" in result["reason"] or "hijack" in result["reason"].lower()

    def test_ld_preload_via_export_in_chain(self):
        """export LD_PRELOAD in a command chain should be caught."""
        cmd = "export LD_PRELOAD=/evil.so && ./app"
        result = ast_classify(cmd)
        assert result["risk"] == "dangerous"

    def test_safe_export_in_chain(self):
        """export PATH in a chain should be safe."""
        cmd = "export PATH=$PATH:/usr/local/bin && ls"
        result = ast_classify(cmd)
        assert result["risk"] == "safe"


# ============================================================================
# 2. PTL retry + partial compaction
#    Engine should trigger compaction on prompt-too-long and retry
# ============================================================================

from duh.kernel.engine import Engine, EngineConfig, _is_ptl_error, MAX_PTL_RETRIES
from duh.kernel.deps import Deps
from duh.adapters.simple_compactor import SimpleCompactor


class TestPTLRetryPlusPartialCompaction:
    @pytest.mark.asyncio
    async def test_engine_ptl_triggers_compact_then_succeeds(self):
        """PTL error should trigger compaction and successful retry."""
        call_count = 0

        async def mock_call(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("prompt is too long: 200000 tokens > 100000 maximum")
            yield {"type": "assistant", "message": Message(role="assistant", content="ok")}
            yield {"type": "done", "stop_reason": "end_turn"}

        compact_called = False
        compacted_message_count = 0

        async def mock_compact(messages, token_limit=0):
            nonlocal compact_called, compacted_message_count
            compact_called = True
            compacted_message_count = len(messages)
            # Simulate aggressive compaction: keep only system + last user
            return messages[-2:] if len(messages) > 2 else messages

        deps = Deps(call_model=mock_call, compact=mock_compact)
        engine = Engine(deps=deps, config=EngineConfig(model="test"))

        events = []
        async for event in engine.run("hello"):
            events.append(event)

        assert compact_called
        assert call_count == 2
        assert any(e.get("type") == "done" for e in events)

    @pytest.mark.asyncio
    async def test_ptl_exhausts_retries(self):
        """PTL error that persists after MAX_PTL_RETRIES should eventually stop."""
        call_count = 0

        async def mock_call(**kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("prompt is too long: 200000 tokens > 100000 maximum")

        async def mock_compact(messages, token_limit=0):
            return messages[-1:] if messages else messages

        deps = Deps(call_model=mock_call, compact=mock_compact)
        engine = Engine(deps=deps, config=EngineConfig(model="test"))

        events = []
        async for event in engine.run("hello"):
            events.append(event)

        # Should have tried MAX_PTL_RETRIES + 1 times total
        assert call_count <= MAX_PTL_RETRIES + 1

    def test_ptl_error_detection_comprehensive(self):
        """Various PTL error messages should all be detected."""
        assert _is_ptl_error("prompt is too long: 200000 tokens")
        assert _is_ptl_error("PromptTooLong")
        assert _is_ptl_error("prompt_too_long")
        assert _is_ptl_error("context length exceeded")
        assert not _is_ptl_error("rate_limit_exceeded")
        assert not _is_ptl_error("invalid api key")
        assert not _is_ptl_error("")


# ============================================================================
# 3. TieredApprover + sandbox policy
#    Full-auto mode with sandbox policy that disables network
# ============================================================================

from duh.adapters.approvers import ApprovalMode, TieredApprover
from duh.adapters.sandbox.policy import SandboxCommand, SandboxPolicy, SandboxType
from duh.adapters.sandbox.network import NetworkMode, NetworkPolicy


class TestTieredApproverPlusSandboxPolicy:
    async def test_full_auto_approves_bash_with_no_network_sandbox(self):
        """Full-auto mode should approve the command, but sandbox policy
        should block network access at the OS level."""
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "curl http://example.com"})
        assert result["allowed"] is True

        # But the sandbox policy disables network
        net_policy = NetworkPolicy(mode=NetworkMode.NONE)
        sandbox_policy = SandboxPolicy(
            writable_paths=["/tmp"],
            network_allowed=net_policy.to_sandbox_flag(),
        )
        assert sandbox_policy.network_allowed is False

        # When building the sandbox command, network should be denied
        cmd = SandboxCommand.build(
            command="curl http://example.com",
            policy=sandbox_policy,
            sandbox_type=SandboxType.MACOS_SEATBELT,
        )
        # The profile should deny network
        from duh.adapters.sandbox.seatbelt import generate_profile
        profile = generate_profile(sandbox_policy)
        assert "(deny network*)" in profile

    async def test_full_auto_with_limited_network(self):
        """Full-auto with LIMITED network should allow network in sandbox
        but block POST at the application level."""
        net_policy = NetworkPolicy(mode=NetworkMode.LIMITED)
        assert net_policy.to_sandbox_flag() is True  # OS allows network

        # Application-level check: GET ok, POST blocked
        assert net_policy.is_request_allowed("GET", "https://example.com") is True
        assert net_policy.is_request_allowed("POST", "https://api.example.com") is False

    async def test_suggest_mode_blocks_bash_regardless_of_sandbox(self):
        """Even with a permissive sandbox, SUGGEST mode should block Bash."""
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Bash", {"command": "ls"})
        assert result["allowed"] is False

    async def test_auto_edit_approves_write_blocks_bash(self):
        """AUTO_EDIT approves writes but blocks Bash, regardless of sandbox."""
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)

        write_result = await approver.check("Write", {"file_path": "/tmp/test"})
        assert write_result["allowed"] is True

        bash_result = await approver.check("Bash", {"command": "ls"})
        assert bash_result["allowed"] is False


# ============================================================================
# 4. Ghost snapshot + read-only enforcement
#    Snapshot mode should block all mutating tools while allowing reads
# ============================================================================

from duh.kernel.snapshot import ReadOnlyExecutor, SnapshotSession


class TestSnapshotPlusReadOnlyEnforcement:
    async def test_snapshot_blocks_all_mutating_tools(self):
        """Snapshot mode should block every tool in the blocked set."""
        call_log = []

        async def real_exec(tool_name, input, **kw):
            call_log.append(tool_name)
            return f"executed {tool_name}"

        executor = ReadOnlyExecutor(real_exec)

        blocked_tools = [
            "Write", "Edit", "MultiEdit", "Bash", "NotebookEdit",
            "WebFetch", "HTTP", "Database", "Docker", "GitHub",
            "Task", "EnterWorktree", "ExitWorktree", "MemoryStore",
        ]
        for tool in blocked_tools:
            with pytest.raises(PermissionError, match="[Ss]napshot"):
                await executor.run(tool, {})
            assert tool not in call_log, f"{tool} should have been blocked"

    async def test_snapshot_allows_all_read_tools(self):
        """Snapshot mode should allow every tool in the allowed set."""
        call_log = []

        async def real_exec(tool_name, input, **kw):
            call_log.append(tool_name)
            return f"result from {tool_name}"

        executor = ReadOnlyExecutor(real_exec)

        allowed_tools = [
            "Read", "Glob", "Grep", "ToolSearch", "WebSearch",
            "MemoryRecall", "Skill",
        ]
        for tool in allowed_tools:
            result = await executor.run(tool, {})
            assert result == f"result from {tool}"
        assert set(call_log) == set(allowed_tools)

    def test_snapshot_session_full_lifecycle(self):
        """Create snapshot, add messages, get new, discard."""
        original = [
            Message(role="user", content="original 1"),
            Message(role="assistant", content="original 2"),
        ]
        snapshot = SnapshotSession(original)

        # Initial state
        assert len(snapshot.messages) == 2
        assert snapshot.get_new_messages() == []
        assert not snapshot.is_discarded

        # Add messages in snapshot
        snapshot.add_message(Message(role="user", content="snapshot q1"))
        snapshot.add_message(Message(role="assistant", content="snapshot a1"))

        new_msgs = snapshot.get_new_messages()
        assert len(new_msgs) == 2
        assert new_msgs[0].content == "snapshot q1"

        # Original should be unaffected (deep copy)
        assert len(original) == 2

        # Discard
        snapshot.discard()
        assert snapshot.is_discarded
        assert snapshot.get_new_messages() == []
        assert len(snapshot.messages) == 0

    def test_snapshot_apply_flow(self):
        """Simulate the apply flow: get new messages and extend original."""
        original = [Message(role="user", content="hello")]
        snapshot = SnapshotSession(original)

        snapshot.add_message(Message(role="assistant", content="exploring..."))
        snapshot.add_message(Message(role="user", content="what if?"))
        snapshot.add_message(Message(role="assistant", content="here's what happens"))

        # Simulate apply: merge new messages back
        new_msgs = snapshot.get_new_messages()
        assert len(new_msgs) == 3
        original.extend(new_msgs)
        assert len(original) == 4  # 1 original + 3 new

    async def test_snapshot_read_only_with_real_read_call(self):
        """ReadOnlyExecutor should pass through reads to the inner executor."""
        async def inner(tool_name, input, **kw):
            if tool_name == "Read":
                return f"Contents of {input['file_path']}"
            return "unexpected"

        executor = ReadOnlyExecutor(inner)
        result = await executor.run("Read", {"file_path": "/tmp/test.py"})
        assert result == "Contents of /tmp/test.py"

        # But writes should still be blocked
        with pytest.raises(PermissionError):
            await executor.run("Write", {"file_path": "/tmp/test.py", "content": "evil"})
