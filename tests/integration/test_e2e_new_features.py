"""E2E integration tests for D.U.H. new features.

Exercises:
  1. Session save -> resume -> continue
  2. Snip compaction fires
  3. AgentTool spawns subagent with tools
  4. VCR replay full session
  5. @include expansion
  6. Project-scoped sessions
  7. SwarmTool parallel execution
  8. Structured handoff in compaction

All tests use the StubProvider or fake models -- no real API calls.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.adapters.file_store import FileStore
from duh.adapters.stub_provider import StubProvider
from duh.adapters.vcr import VCR
from duh.adapters.compact.snip import SnipCompactor
from duh.adapters.compact.summarize import SummarizeCompactor
from duh.adapters.compact.handoff import HANDOFF_PROMPT
from duh.config import load_instructions
from duh.tools.agent_tool import AgentTool
from duh.tools.swarm_tool import SwarmTool
from duh.tools.read import ReadTool
from duh.kernel.tool import ToolContext, ToolResult


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

async def _collect(gen) -> list[dict]:
    """Drain an async generator into a list."""
    return [e async for e in gen]


def _simple_model(text: str = "stub-ok"):
    """Return a fake call_model that yields a single text response."""
    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "text_delta", "text": text}
        yield {
            "type": "assistant",
            "message": Message(
                role="assistant",
                content=[{"type": "text", "text": text}],
                metadata={"stop_reason": "end_turn"},
            ),
        }
        yield {"type": "done", "stop_reason": "end_turn", "turns": 1}
    return model


def _get_role(msg: Any) -> str:
    if isinstance(msg, Message):
        return msg.role
    if isinstance(msg, dict):
        return msg.get("role", "")
    return ""


# ═══════════════════════════════════════════════════════════════════
# 1. Session save -> resume -> continue
# ═══════════════════════════════════════════════════════════════════

class TestSessionSaveResumeContinue:
    """Create engine, run a prompt, save session, create new engine
    loading same session, run another prompt, verify combined history."""

    async def test_save_resume_continue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileStore(base_dir=tmpdir)

            # Phase 1: create engine, run a prompt, save
            deps1 = Deps(call_model=_simple_model("Reply to first."))
            engine1 = Engine(deps=deps1)
            session_id = engine1.session_id

            events1 = await _collect(engine1.run("First user message"))
            assert engine1.turn_count == 1
            assert len(engine1.messages) == 2  # user + assistant

            # Save session
            await store.save(session_id, engine1.messages)

            # Verify messages were persisted
            loaded = await store.load(session_id)
            assert loaded is not None
            assert len(loaded) == 2
            assert loaded[0]["role"] == "user"
            assert loaded[0]["content"] == "First user message"
            assert loaded[1]["role"] == "assistant"

            # Phase 2: create new engine, pre-load history, run another prompt
            model_saw_messages = []

            async def tracking_model(**kwargs):
                msgs = kwargs.get("messages", [])
                model_saw_messages.append(len(msgs))
                yield {
                    "type": "assistant",
                    "message": Message(
                        role="assistant",
                        content=[{"type": "text", "text": "Reply to second."}],
                        metadata={"stop_reason": "end_turn"},
                    ),
                }

            deps2 = Deps(call_model=tracking_model)
            engine2 = Engine(deps=deps2)

            # Load previous history into the new engine
            for msg_dict in loaded:
                engine2._messages.append(
                    Message(
                        role=msg_dict["role"],
                        content=msg_dict["content"],
                    )
                )

            events2 = await _collect(engine2.run("Second user message"))

            # The model should have seen: loaded user + loaded assistant + new user = 3
            assert model_saw_messages[0] == 3

            # Engine now has 4 messages total (original 2 + new user + new assistant)
            assert len(engine2.messages) == 4
            roles = [m.role for m in engine2.messages]
            assert roles == ["user", "assistant", "user", "assistant"]

            # Save the combined session and verify roundtrip
            await store.save(session_id, engine2.messages)
            final_loaded = await store.load(session_id)
            assert final_loaded is not None
            assert len(final_loaded) == 4
            assert final_loaded[2]["content"] == "Second user message"


# ═══════════════════════════════════════════════════════════════════
# 2. Snip compaction fires
# ═══════════════════════════════════════════════════════════════════

class TestSnipCompaction:
    """Populate engine with 20+ alternating messages, trigger snip,
    verify messages reduced, alternation preserved, first user kept."""

    async def test_snip_reduces_messages(self):
        # Build 22 alternating messages (11 rounds of user/assistant)
        messages: list[Message] = []
        for i in range(11):
            messages.append(Message(
                role="user",
                content=f"User message {i}" if i > 0 else "Original task: build the widget",
            ))
            messages.append(Message(
                role="assistant",
                content=[{"type": "text", "text": f"Assistant reply {i}"}],
            ))

        assert len(messages) == 22

        sc = SnipCompactor(keep_last=6)
        snipped, tokens_freed = sc.snip(messages)

        # Should have reduced the count
        assert len(snipped) < len(messages), (
            f"Expected fewer messages after snip, got {len(snipped)} from {len(messages)}"
        )

        # Tokens freed should be positive
        assert tokens_freed > 0

        # First user message is always preserved (with snip marker appended)
        first_user = snipped[0]
        assert _get_role(first_user) == "user"
        text = first_user.content if isinstance(first_user.content, str) else first_user.text
        assert "Original task: build the widget" in text

        # Alternation must be preserved: roles alternate user/assistant
        for i in range(len(snipped) - 1):
            role_a = _get_role(snipped[i])
            role_b = _get_role(snipped[i + 1])
            assert role_a != role_b, (
                f"Broken alternation at index {i}: {role_a} followed by {role_b}"
            )

    async def test_snip_keeps_last_n_messages(self):
        """Verify the protected tail (keep_last) is untouched."""
        messages: list[Message] = []
        for i in range(15):
            messages.append(Message(role="user", content=f"U{i}"))
            messages.append(Message(role="assistant", content=f"A{i}"))

        keep_last = 8
        sc = SnipCompactor(keep_last=keep_last)
        snipped, _ = sc.snip(messages)

        # The last `keep_last` messages from the original must appear at the end
        original_tail = messages[-keep_last:]
        snipped_tail = snipped[-keep_last:]

        for orig, snip in zip(original_tail, snipped_tail):
            orig_text = orig.content if isinstance(orig.content, str) else orig.text
            snip_text = snip.content if isinstance(snip.content, str) else snip.text
            assert orig_text == snip_text

    async def test_snip_too_few_messages_is_noop(self):
        """With fewer messages than keep_last, snip should be a no-op."""
        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        sc = SnipCompactor(keep_last=6)
        snipped, tokens_freed = sc.snip(messages)
        assert len(snipped) == len(messages)
        assert tokens_freed == 0


# ═══════════════════════════════════════════════════════════════════
# 3. AgentTool spawns subagent with tools
# ═══════════════════════════════════════════════════════════════════

class TestAgentToolSubagent:
    """Create AgentTool with parent deps (stub provider), parent tools
    (include ReadTool), call it, verify it returns a result."""

    async def test_agent_tool_spawns_subagent(self):
        stub = StubProvider()
        deps = Deps(call_model=stub.stream)

        read_tool = ReadTool()
        agent_tool = AgentTool(parent_deps=deps, parent_tools=[read_tool])

        ctx = ToolContext(cwd="/tmp")
        result = await agent_tool.call(
            {"prompt": "List the files", "agent_type": "general"},
            ctx,
        )

        # Should NOT be the "no parent deps" error
        assert "no parent deps" not in (result.output or "").lower()
        # The subagent should have run and returned something
        assert result.output is not None
        assert len(result.output) > 0

    async def test_agent_tool_no_deps_errors(self):
        """AgentTool without parent_deps should return an error."""
        agent_tool = AgentTool(parent_deps=None, parent_tools=[])
        ctx = ToolContext(cwd="/tmp")
        result = await agent_tool.call(
            {"prompt": "do something"},
            ctx,
        )
        assert result.is_error is True
        assert "no parent deps" in result.output.lower()

    async def test_agent_tool_excludes_self_from_children(self):
        """Child tools should not include AgentTool (prevents recursion)."""
        stub = StubProvider()
        deps = Deps(call_model=stub.stream)

        read_tool = ReadTool()
        inner_agent = AgentTool(parent_deps=deps, parent_tools=[])
        outer_agent = AgentTool(
            parent_deps=deps,
            parent_tools=[read_tool, inner_agent],
        )

        child_tools = outer_agent._child_tools()
        child_names = [getattr(t, "name", "") for t in child_tools]
        assert "Agent" not in child_names
        assert "Read" in child_names


# ═══════════════════════════════════════════════════════════════════
# 4. VCR replay full session
# ═══════════════════════════════════════════════════════════════════

class TestVCRReplay:
    """Use the fixture at tests/fixtures/simple_text.jsonl, create
    engine with VCR replay as call_model, run a prompt, verify events."""

    async def test_vcr_replay_matches_fixture(self):
        fixture_path = Path(__file__).parent.parent / "fixtures" / "simple_text.jsonl"
        assert fixture_path.exists(), f"Fixture not found: {fixture_path}"

        # Read expected events from fixture
        expected_events: list[dict] = []
        with fixture_path.open("r") as f:
            for line in f:
                line = line.strip()
                if line:
                    expected_events.append(json.loads(line))

        # Create VCR in replay mode
        vcr = VCR(fixture_path=fixture_path, mode="replay")

        # Collect events from VCR replay
        replayed: list[dict] = []
        async for event in vcr.stream():
            replayed.append(event)

        # Verify event count matches
        assert len(replayed) == len(expected_events)

        # Verify event types match
        for replayed_event, expected in zip(replayed, expected_events):
            assert replayed_event["type"] == expected["type"]

        # Verify the text_delta contains expected text
        text_deltas = [e for e in replayed if e["type"] == "text_delta"]
        assert len(text_deltas) > 0
        assert text_deltas[0]["text"] == "Hello, world!"

        # Verify the done event
        done_events = [e for e in replayed if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["stop_reason"] == "end_turn"

    async def test_vcr_replay_as_engine_call_model(self):
        """Wire VCR replay into an Engine and verify the full pipeline works."""
        fixture_path = Path(__file__).parent.parent / "fixtures" / "simple_text.jsonl"
        assert fixture_path.exists(), f"Fixture not found: {fixture_path}"

        vcr = VCR(fixture_path=fixture_path, mode="replay")
        deps = Deps(call_model=vcr.stream)
        engine = Engine(deps=deps)

        events = await _collect(engine.run("hello"))
        event_types = [e["type"] for e in events]

        # Engine wraps VCR events with session + done
        assert "session" in event_types
        # The fixture should produce assistant and done events
        assert "assistant" in event_types or "text_delta" in event_types

    async def test_vcr_replay_missing_fixture_raises(self):
        """Replay with a missing fixture file should raise FileNotFoundError."""
        vcr = VCR(
            fixture_path=Path("/tmp/nonexistent_fixture_abc123.jsonl"),
            mode="replay",
        )
        with pytest.raises(FileNotFoundError):
            async for _ in vcr.stream():
                pass


# ═══════════════════════════════════════════════════════════════════
# 5. @include expansion
# ═══════════════════════════════════════════════════════════════════

class TestIncludeExpansion:
    """Create temp dir with a DUH.md containing @./rules.md, create
    rules.md, call load_instructions(), verify both files' content appears."""

    async def test_include_directive_expands(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a DUH.md that includes rules.md
            duh_md = tmpdir_path / "DUH.md"
            duh_md.write_text(
                "# Main instructions\n\n"
                "Follow the rules below.\n\n"
                "@./rules.md\n",
                encoding="utf-8",
            )

            # Create the included rules.md
            rules_md = tmpdir_path / "rules.md"
            rules_md.write_text(
                "# Coding Rules\n\n"
                "- Always write tests\n"
                "- Use type hints\n",
                encoding="utf-8",
            )

            # Also create a .git dir so config treats this as a project root
            (tmpdir_path / ".git").mkdir()

            instructions = load_instructions(cwd=str(tmpdir_path))

            # Both the DUH.md content and the included rules.md content should appear
            combined = "\n".join(instructions)
            assert "Main instructions" in combined
            assert "Always write tests" in combined
            assert "Use type hints" in combined

    async def test_include_nested(self):
        """Test nested includes: DUH.md -> rules.md -> style.md"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            (tmpdir_path / ".git").mkdir()

            duh_md = tmpdir_path / "DUH.md"
            duh_md.write_text("Top level\n@./rules.md\n", encoding="utf-8")

            rules_md = tmpdir_path / "rules.md"
            rules_md.write_text("Mid level rules\n@./style.md\n", encoding="utf-8")

            style_md = tmpdir_path / "style.md"
            style_md.write_text("Style guide: use snake_case\n", encoding="utf-8")

            instructions = load_instructions(cwd=str(tmpdir_path))
            combined = "\n".join(instructions)

            assert "Top level" in combined
            assert "Mid level rules" in combined
            assert "Style guide: use snake_case" in combined

    async def test_include_missing_file_is_silent(self):
        """An @include pointing to a non-existent file should not crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            (tmpdir_path / ".git").mkdir()

            duh_md = tmpdir_path / "DUH.md"
            duh_md.write_text(
                "Some instructions\n@./nonexistent.md\n",
                encoding="utf-8",
            )

            instructions = load_instructions(cwd=str(tmpdir_path))
            combined = "\n".join(instructions)
            assert "Some instructions" in combined
            # Should not crash; nonexistent file is silently skipped

    async def test_include_circular_reference_protected(self):
        """Circular @includes should not cause infinite recursion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            (tmpdir_path / ".git").mkdir()

            a_md = tmpdir_path / "DUH.md"
            a_md.write_text("File A\n@./b.md\n", encoding="utf-8")

            b_md = tmpdir_path / "b.md"
            b_md.write_text("File B\n@./DUH.md\n", encoding="utf-8")

            # Should not hang or crash
            instructions = load_instructions(cwd=str(tmpdir_path))
            combined = "\n".join(instructions)
            assert "File A" in combined
            assert "File B" in combined


# ═══════════════════════════════════════════════════════════════════
# 6. Project-scoped sessions
# ═══════════════════════════════════════════════════════════════════

class TestProjectScopedSessions:
    """Create FileStore with cwd for project-a, save session, create
    FileStore with cwd for project-b, verify project-b has no sessions."""

    async def test_different_projects_have_isolated_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two distinct "project" directories
            project_a = Path(tmpdir) / "project-a"
            project_b = Path(tmpdir) / "project-b"
            project_a.mkdir()
            project_b.mkdir()

            store_a = FileStore(cwd=str(project_a))
            store_b = FileStore(cwd=str(project_b))

            # Save a session in project-a
            await store_a.save(
                "sess-alpha",
                [Message(role="user", content="Hello from project A")],
            )

            # project-a should have the session
            sessions_a = await store_a.list_sessions()
            assert any(s["session_id"] == "sess-alpha" for s in sessions_a)

            # project-b should have NO sessions
            sessions_b = await store_b.list_sessions()
            session_ids_b = [s["session_id"] for s in sessions_b]
            assert "sess-alpha" not in session_ids_b

    async def test_same_project_shares_sessions(self):
        """Two FileStores with the same cwd should see the same sessions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project = Path(tmpdir) / "project"
            project.mkdir()

            store1 = FileStore(cwd=str(project))
            store2 = FileStore(cwd=str(project))

            await store1.save(
                "shared-sess",
                [Message(role="user", content="shared content")],
            )

            loaded = await store2.load("shared-sess")
            assert loaded is not None
            assert loaded[0]["content"] == "shared content"


# ═══════════════════════════════════════════════════════════════════
# 7. SwarmTool parallel execution
# ═══════════════════════════════════════════════════════════════════

class TestSwarmToolParallel:
    """Create SwarmTool with stub deps, call with 3 tasks, verify all
    3 results appear in output."""

    async def test_swarm_runs_three_tasks(self):
        stub = StubProvider()
        deps = Deps(call_model=stub.stream)

        swarm = SwarmTool(parent_deps=deps, parent_tools=[])

        ctx = ToolContext(cwd="/tmp")
        result = await swarm.call(
            {
                "tasks": [
                    {"prompt": "Task one: analyze code"},
                    {"prompt": "Task two: write tests"},
                    {"prompt": "Task three: review docs"},
                ],
            },
            ctx,
        )

        # All three tasks should appear in output
        assert "Task 1/3" in result.output
        assert "Task 2/3" in result.output
        assert "Task 3/3" in result.output

        # Each task should report OK status (stub provider always succeeds)
        assert result.output.count("Status: OK") == 3 or result.output.count("Status:") == 3

        # The overall result should not be an error
        assert result.is_error is False

    async def test_swarm_no_deps_errors(self):
        """SwarmTool without parent_deps should return an error."""
        swarm = SwarmTool(parent_deps=None, parent_tools=[])
        ctx = ToolContext(cwd="/tmp")
        result = await swarm.call(
            {"tasks": [{"prompt": "do something"}]},
            ctx,
        )
        assert result.is_error is True
        assert "no parent deps" in result.output.lower()

    async def test_swarm_no_tasks_errors(self):
        """SwarmTool with empty tasks list should return an error."""
        stub = StubProvider()
        deps = Deps(call_model=stub.stream)
        swarm = SwarmTool(parent_deps=deps, parent_tools=[])
        ctx = ToolContext(cwd="/tmp")
        result = await swarm.call({"tasks": []}, ctx)
        assert result.is_error is True
        assert "no tasks" in result.output.lower()

    async def test_swarm_excludes_recursive_tools(self):
        """Child tools should exclude Agent and Swarm to prevent recursion."""
        stub = StubProvider()
        deps = Deps(call_model=stub.stream)

        read_tool = ReadTool()
        agent_tool = AgentTool(parent_deps=deps, parent_tools=[])
        swarm_tool = SwarmTool(
            parent_deps=deps,
            parent_tools=[read_tool, agent_tool, SwarmTool(parent_deps=deps)],
        )

        child_tools = swarm_tool._child_tools()
        child_names = [getattr(t, "name", "") for t in child_tools]
        assert "Agent" not in child_names
        assert "Swarm" not in child_names
        assert "Read" in child_names


# ═══════════════════════════════════════════════════════════════════
# 8. Structured handoff in compaction
# ═══════════════════════════════════════════════════════════════════

class TestStructuredHandoff:
    """Run SummarizeCompactor with stub model, verify the prompt sent to
    the model contains the structured handoff sections."""

    async def test_handoff_prompt_contains_required_sections(self):
        captured_prompts: list[str] = []

        async def capturing_model(**kwargs):
            """Fake model that captures what it receives and returns a summary."""
            messages = kwargs.get("messages", [])
            for msg in messages:
                if isinstance(msg, Message):
                    text = msg.content if isinstance(msg.content, str) else msg.text
                    captured_prompts.append(text)
                elif isinstance(msg, dict):
                    captured_prompts.append(str(msg.get("content", "")))
            yield {"type": "text_delta", "text": "## Progress\n- Did stuff\n## Decisions\n- Chose X"}

        # Build enough messages to trigger summarization
        messages = [
            Message(role="user", content="Build a REST API"),
            Message(role="assistant", content="I will build it with Flask."),
            Message(role="user", content="Add authentication"),
            Message(role="assistant", content="Adding JWT auth."),
            Message(role="user", content="Write tests"),
            Message(role="assistant", content="Writing pytest tests."),
            Message(role="user", content="Deploy it"),
            Message(role="assistant", content="Deploying to production."),
        ]

        sc = SummarizeCompactor(
            call_model=capturing_model,
            bytes_per_token=1,  # 1 byte = 1 token for easy math
            min_keep=2,         # keep at least 2 messages
        )

        # Use a very small token limit to force summarization
        compacted = await sc.compact(messages, token_limit=100)

        # The model should have been called with the handoff prompt
        assert len(captured_prompts) > 0
        prompt_text = captured_prompts[0]

        # Verify structured handoff sections are present in the prompt
        assert "## Progress" in prompt_text
        assert "## Decisions" in prompt_text
        assert "## Constraints" in prompt_text
        assert "## Pending" in prompt_text
        assert "## Context" in prompt_text

    async def test_handoff_prompt_matches_constant(self):
        """The HANDOFF_PROMPT constant should contain all required sections."""
        assert "## Progress" in HANDOFF_PROMPT
        assert "## Decisions" in HANDOFF_PROMPT
        assert "## Constraints" in HANDOFF_PROMPT
        assert "## Pending" in HANDOFF_PROMPT
        assert "## Context" in HANDOFF_PROMPT

    async def test_summarize_compactor_mechanical_fallback(self):
        """Without a call_model, SummarizeCompactor should use mechanical summary."""
        messages = [
            Message(role="user", content="Do X"),
            Message(role="assistant", content="Done X."),
            Message(role="user", content="Do Y"),
            Message(role="assistant", content="Done Y."),
            Message(role="user", content="Latest request"),
            Message(role="assistant", content="Latest response"),
        ]

        # No call_model -- triggers mechanical fallback
        sc = SummarizeCompactor(
            call_model=None,
            bytes_per_token=1,
            min_keep=2,
        )

        compacted = await sc.compact(messages, token_limit=80)

        # Should have fewer messages than original
        assert len(compacted) <= len(messages)

        # Should contain a summary message if messages were dropped
        roles = [_get_role(m) for m in compacted]
        # Either system (summary injected) or the conversation is short enough to keep
        has_summary = any(
            "summary" in (m.content if isinstance(m, Message) and isinstance(m.content, str) else "").lower()
            for m in compacted
        )
        # If messages were dropped, a summary should have been inserted
        if len(compacted) < len(messages):
            assert has_summary, "Expected a summary message when messages were dropped"
