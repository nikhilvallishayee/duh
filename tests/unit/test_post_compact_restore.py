# tests/unit/test_post_compact_restore.py
"""Tests for post-compact context restoration.

Covers:
- restore_context (file + skill restoration in SummarizeCompactor)
- restore_plan_context (ADR-058 Phase 4)
- restore_skill_context (ADR-058 Phase 4)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from duh.adapters.simple_compactor import (
    POST_COMPACT_MAX_FILES,
    POST_COMPACT_TOKEN_BUDGET,
    restore_context,
)
from duh.kernel.file_tracker import FileTracker
from duh.kernel.messages import Message, TextBlock
from duh.kernel.post_compact import restore_plan_context, restore_skill_context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role: str = "user", content: str = "hello", **kw) -> Message:
    return Message(role=role, content=content, id=kw.get("id", "m"), timestamp="t")


def _sys(content: str = "system") -> Message:
    return Message(role="system", content=content, id="sys", timestamp="t0")


# ===========================================================================
# restore_context
# ===========================================================================

class TestRestoreContext:
    def test_no_tracker_no_change(self):
        """Without a file tracker, messages are returned unchanged."""
        msgs = [_msg(content="hello"), _msg(content="world")]
        result = restore_context(msgs, file_tracker=None, skill_context=None)
        assert len(result) == len(msgs)

    def test_recent_files_added(self):
        """Recently read files should be added as a system message."""
        tracker = FileTracker()
        tracker.track("/foo/bar.py", "read")
        tracker.track("/foo/baz.py", "read")

        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=tracker, skill_context=None)
        # Should have original message + restoration system message
        assert len(result) == len(msgs) + 1
        restore_msg = result[-1]
        assert restore_msg.role == "system"
        assert "/foo/bar.py" in restore_msg.content or "/foo/baz.py" in restore_msg.content

    def test_max_files_respected(self):
        """Only the most recent POST_COMPACT_MAX_FILES files are restored."""
        tracker = FileTracker()
        for i in range(POST_COMPACT_MAX_FILES + 5):
            tracker.track(f"/file_{i}.py", "read")

        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=tracker, skill_context=None)
        restore_msg = result[-1]
        # Should mention at most POST_COMPACT_MAX_FILES files
        file_mentions = [
            line for line in restore_msg.content.split("\n")
            if line.strip().startswith("/file_")
            or line.strip().startswith("- /file_")
        ]
        assert len(file_mentions) <= POST_COMPACT_MAX_FILES

    def test_skill_context_added(self):
        """Active skill context should be included in restoration."""
        skill_ctx = "Active skill: test-driven-development\nAlways write tests first."
        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=None, skill_context=skill_ctx)
        assert len(result) == len(msgs) + 1
        restore_msg = result[-1]
        assert "test-driven-development" in restore_msg.content

    def test_both_files_and_skills(self):
        """Both file tracker and skill context are combined."""
        tracker = FileTracker()
        tracker.track("/foo/bar.py", "read")
        skill_ctx = "Skill: debugging"
        msgs = [_msg(content="hello")]
        result = restore_context(
            msgs, file_tracker=tracker, skill_context=skill_ctx
        )
        assert len(result) == len(msgs) + 1
        restore_msg = result[-1]
        assert "/foo/bar.py" in restore_msg.content
        assert "debugging" in restore_msg.content

    def test_token_budget_respected(self):
        """Restoration content should not exceed POST_COMPACT_TOKEN_BUDGET."""
        tracker = FileTracker()
        # Track files with very long paths to test budget enforcement
        for i in range(10):
            tracker.track(f"/{'x' * 5000}/file_{i}.py", "read")

        msgs = [_msg(content="hello")]
        result = restore_context(
            msgs, file_tracker=tracker, skill_context=None,
            token_budget=100,  # very tight budget
        )
        if len(result) > len(msgs):
            restore_msg = result[-1]
            # Rough token estimate: len(content) / 4
            assert len(restore_msg.content) // 4 <= 200  # generous allowance

    def test_empty_tracker_no_restoration(self):
        """An empty file tracker should not add a restoration message."""
        tracker = FileTracker()
        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=tracker, skill_context=None)
        assert len(result) == len(msgs)

    def test_empty_skill_no_restoration(self):
        """Empty skill context should not add a restoration message."""
        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=None, skill_context="")
        assert len(result) == len(msgs)

    def test_deduplicates_files(self):
        """Same file read multiple times should appear only once."""
        tracker = FileTracker()
        tracker.track("/foo/bar.py", "read")
        tracker.track("/foo/bar.py", "read")
        tracker.track("/foo/bar.py", "edit")

        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=tracker, skill_context=None)
        restore_msg = result[-1]
        # /foo/bar.py should appear exactly once
        count = restore_msg.content.count("/foo/bar.py")
        assert count == 1

    def test_does_not_mutate_input(self):
        msgs = [_msg(content="hello")]
        original_len = len(msgs)
        restore_context(msgs, file_tracker=None, skill_context="some skill")
        assert len(msgs) == original_len


class TestConstants:
    def test_max_files_value(self):
        assert POST_COMPACT_MAX_FILES == 5

    def test_token_budget_value(self):
        assert POST_COMPACT_TOKEN_BUDGET == 50_000


# ===========================================================================
# restore_plan_context (ADR-058 Phase 4)
# ===========================================================================


class _FakePlanStep:
    """Minimal PlanStep stand-in for testing."""
    def __init__(self, number: int, description: str, done: bool = False):
        self.number = number
        self.description = description
        self.done = done


class TestRestorePlanContext:
    """restore_plan_context returns active plan as a context string."""

    def test_no_plan_mode_returns_none(self):
        """Engine without _plan_mode returns None."""
        engine = object()  # no _plan_mode attribute
        assert restore_plan_context(engine) is None

    def test_empty_state_returns_none(self):
        """Plan in EMPTY state returns None."""
        from duh.kernel.plan_mode import PlanState

        class FakePM:
            state = PlanState.EMPTY
            description = ""
            steps = []

        class FakeEngine:
            _plan_mode = FakePM()

        assert restore_plan_context(FakeEngine()) is None

    def test_done_state_returns_none(self):
        """Plan in DONE state returns None."""
        from duh.kernel.plan_mode import PlanState

        class FakePM:
            state = PlanState.DONE
            description = "old plan"
            steps = [_FakePlanStep(1, "step one", done=True)]

        class FakeEngine:
            _plan_mode = FakePM()

        assert restore_plan_context(FakeEngine()) is None

    def test_proposed_plan_restored(self):
        """A proposed plan is returned as a formatted context string."""
        from duh.kernel.plan_mode import PlanState

        class FakePM:
            state = PlanState.PROPOSED
            description = "refactor the auth module"
            steps = [
                _FakePlanStep(1, "Extract interface"),
                _FakePlanStep(2, "Implement adapter"),
            ]

        class FakeEngine:
            _plan_mode = FakePM()

        result = restore_plan_context(FakeEngine())
        assert result is not None
        assert "refactor the auth module" in result
        assert "PROPOSED" in result
        assert "[ ] 1. Extract interface" in result
        assert "[ ] 2. Implement adapter" in result

    def test_executing_plan_with_partial_progress(self):
        """An executing plan shows done/undone markers."""
        from duh.kernel.plan_mode import PlanState

        class FakePM:
            state = PlanState.EXECUTING
            description = "migrate database"
            steps = [
                _FakePlanStep(1, "Create schema", done=True),
                _FakePlanStep(2, "Run migrations", done=False),
            ]

        class FakeEngine:
            _plan_mode = FakePM()

        result = restore_plan_context(FakeEngine())
        assert result is not None
        assert "[x] 1. Create schema" in result
        assert "[ ] 2. Run migrations" in result
        assert "EXECUTING" in result

    def test_plan_mode_no_steps_no_description(self):
        """Plan mode present but empty description and steps returns None."""
        from duh.kernel.plan_mode import PlanState

        class FakePM:
            state = PlanState.PROPOSED
            description = ""
            steps = []

        class FakeEngine:
            _plan_mode = FakePM()

        assert restore_plan_context(FakeEngine()) is None


# ===========================================================================
# restore_skill_context (ADR-058 Phase 4)
# ===========================================================================


class _FakeSkillDef:
    """Minimal SkillDef stand-in for testing."""
    def __init__(self, name: str, description: str, argument_hint: str = ""):
        self.name = name
        self.description = description
        self.argument_hint = argument_hint


class _FakeSkillTool:
    """Minimal SkillTool stand-in (class name must be 'SkillTool')."""
    def __init__(self, skills: list):
        self._skills = skills

    @property
    def skills(self):
        return list(self._skills)


# Rename the class so type().__name__ == "SkillTool"
_FakeSkillTool.__name__ = "SkillTool"


class TestRestoreSkillContext:
    """restore_skill_context returns loaded skills as a context string."""

    def test_no_config_returns_none(self):
        """Engine without _config returns None."""
        engine = object()
        assert restore_skill_context(engine) is None

    def test_no_tools_returns_none(self):
        """Engine with empty tools list returns None."""
        class FakeConfig:
            tools = []

        class FakeEngine:
            _config = FakeConfig()

        assert restore_skill_context(FakeEngine()) is None

    def test_no_skill_tool_returns_none(self):
        """Engine with tools but no SkillTool returns None."""
        class FakeTool:
            pass

        class FakeConfig:
            tools = [FakeTool()]

        class FakeEngine:
            _config = FakeConfig()

        assert restore_skill_context(FakeEngine()) is None

    def test_skill_tool_no_skills_returns_none(self):
        """SkillTool with no registered skills returns None."""
        skill_tool = _FakeSkillTool(skills=[])

        class FakeConfig:
            tools = [skill_tool]

        class FakeEngine:
            _config = FakeConfig()

        assert restore_skill_context(FakeEngine()) is None

    def test_skills_restored(self):
        """Loaded skills are returned as a formatted listing."""
        skill_tool = _FakeSkillTool(skills=[
            _FakeSkillDef("commit", "Create a git commit"),
            _FakeSkillDef("review-pr", "Review a pull request", argument_hint="PR number"),
        ])

        class FakeConfig:
            tools = [skill_tool]

        class FakeEngine:
            _config = FakeConfig()

        result = restore_skill_context(FakeEngine())
        assert result is not None
        assert "commit" in result
        assert "Create a git commit" in result
        assert "review-pr" in result
        assert "Review a pull request" in result
        assert "(PR number)" in result

    def test_skill_tool_among_other_tools(self):
        """SkillTool is found even when mixed with other tool types."""
        class OtherTool:
            pass

        skill_tool = _FakeSkillTool(skills=[
            _FakeSkillDef("debug", "Debug an issue"),
        ])

        class FakeConfig:
            tools = [OtherTool(), skill_tool, OtherTool()]

        class FakeEngine:
            _config = FakeConfig()

        result = restore_skill_context(FakeEngine())
        assert result is not None
        assert "debug" in result


# ===========================================================================
# Engine integration — plan/skill messages appended after compact
# ===========================================================================


class TestEnginePostCompactIntegration:
    """Verify Engine appends plan/skill system messages after auto-compact."""

    def _make_engine(self, *, plan_mode=None, skill_tool=None):
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.deps import Deps

        tools = []
        if skill_tool is not None:
            tools.append(skill_tool)

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        config = EngineConfig(model="test-model", tools=tools)
        engine = Engine(deps=deps, config=config)

        if plan_mode is not None:
            engine._plan_mode = plan_mode

        return engine

    def test_plan_context_injected_on_engine(self):
        """restore_plan_context works with a real Engine instance."""
        from duh.kernel.plan_mode import PlanState

        class FakePM:
            state = PlanState.PROPOSED
            description = "add tests"
            steps = [_FakePlanStep(1, "write unit tests")]

        engine = self._make_engine(plan_mode=FakePM())
        result = restore_plan_context(engine)
        assert result is not None
        assert "add tests" in result

    def test_skill_context_injected_on_engine(self):
        """restore_skill_context works with a real Engine instance."""
        skill_tool = _FakeSkillTool(skills=[
            _FakeSkillDef("init", "Initialize project"),
        ])
        engine = self._make_engine(skill_tool=skill_tool)
        result = restore_skill_context(engine)
        assert result is not None
        assert "init" in result

    def test_no_plan_no_skills_noop(self):
        """Without plan or skills, both return None."""
        engine = self._make_engine()
        assert restore_plan_context(engine) is None
        assert restore_skill_context(engine) is None
