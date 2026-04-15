"""Tests for structured handoff summaries in compaction."""

from __future__ import annotations

import pytest

from duh.adapters.compact.handoff import HANDOFF_PROMPT
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# HANDOFF_PROMPT structure tests
# ---------------------------------------------------------------------------

class TestHandoffPromptSections:
    """HANDOFF_PROMPT must contain all 5 structured sections."""

    def test_contains_progress_section(self):
        assert "## Progress" in HANDOFF_PROMPT

    def test_contains_decisions_section(self):
        assert "## Decisions" in HANDOFF_PROMPT

    def test_contains_constraints_section(self):
        assert "## Constraints" in HANDOFF_PROMPT

    def test_contains_pending_section(self):
        assert "## Pending" in HANDOFF_PROMPT

    def test_contains_context_section(self):
        assert "## Context" in HANDOFF_PROMPT

    def test_all_five_sections_present(self):
        sections = [
            "## Progress",
            "## Decisions",
            "## Constraints",
            "## Pending",
            "## Context",
        ]
        for section in sections:
            assert section in HANDOFF_PROMPT, f"Missing section: {section}"

    def test_prompt_mentions_bullet_points(self):
        assert "bullet" in HANDOFF_PROMPT.lower()

    def test_prompt_mentions_preserving_specifics(self):
        assert "file paths" in HANDOFF_PROMPT.lower()
        assert "function names" in HANDOFF_PROMPT.lower()


# ---------------------------------------------------------------------------
# SummarizeCompactor uses structured prompt
# ---------------------------------------------------------------------------

class _FakeModel:
    """Fake model that captures the prompt it receives."""

    def __init__(self, reply: str = "## Progress\n- Done stuff"):
        self._reply = reply
        self.captured_prompts: list[str] = []
        self.captured_system_prompts: list[str] = []

    async def __call__(self, *, messages, system_prompt="", model="", **kw):
        for msg in messages:
            content = msg.content if isinstance(msg, Message) else msg.get("content", "")
            self.captured_prompts.append(content)
        self.captured_system_prompts.append(system_prompt)
        yield {"type": "text_delta", "text": self._reply}


def _msg(role: str = "user", content: str = "hello") -> Message:
    return Message(role=role, content=content)


class TestSummarizeCompactorUsesHandoff:
    """SummarizeCompactor must use the structured handoff prompt."""

    @pytest.mark.asyncio
    async def test_model_prompt_contains_handoff_sections(self):
        """When compaction drops messages, the model receives the handoff prompt."""
        from duh.adapters.compact.summarize import SummarizeCompactor

        fake = _FakeModel()
        sc = SummarizeCompactor(call_model=fake, bytes_per_token=1, min_keep=1)

        msgs = [
            _msg(content="A" * 200),
            _msg(content="B" * 200),
            _msg(content="C" * 200),
        ]
        await sc.compact(msgs, token_limit=250)

        assert len(fake.captured_prompts) == 1
        prompt_sent = fake.captured_prompts[0]

        for section in ["## Progress", "## Decisions", "## Constraints",
                        "## Pending", "## Context"]:
            assert section in prompt_sent, (
                f"Model prompt missing handoff section: {section}"
            )

    @pytest.mark.asyncio
    async def test_model_system_prompt_mentions_structured_handoff(self):
        """The system prompt should describe structured handoff summarization."""
        from duh.adapters.compact.summarize import SummarizeCompactor

        fake = _FakeModel()
        sc = SummarizeCompactor(call_model=fake, bytes_per_token=1, min_keep=1)

        msgs = [
            _msg(content="A" * 200),
            _msg(content="B" * 200),
        ]
        await sc.compact(msgs, token_limit=220)

        assert len(fake.captured_system_prompts) == 1
        sys_prompt = fake.captured_system_prompts[0]
        assert "structured" in sys_prompt.lower()
        assert "handoff" in sys_prompt.lower()

    @pytest.mark.asyncio
    async def test_no_model_call_when_nothing_dropped(self):
        """When all messages fit, no model call and no handoff prompt."""
        from duh.adapters.compact.summarize import SummarizeCompactor

        fake = _FakeModel()
        sc = SummarizeCompactor(call_model=fake, bytes_per_token=1)

        msgs = [_msg(content="short")]
        result = await sc.compact(msgs, token_limit=100_000)

        assert len(result) == 1
        assert fake.captured_prompts == []

    @pytest.mark.asyncio
    async def test_handoff_prompt_includes_conversation_content(self):
        """The prompt sent to the model includes the actual conversation text."""
        from duh.adapters.compact.summarize import SummarizeCompactor

        fake = _FakeModel()
        sc = SummarizeCompactor(call_model=fake, bytes_per_token=1, min_keep=1)

        msgs = [
            _msg(content="important_file.py needs refactoring"),
            _msg(content="X" * 200),
        ]
        await sc.compact(msgs, token_limit=210)

        prompt_sent = fake.captured_prompts[0]
        assert "important_file.py" in prompt_sent

    @pytest.mark.asyncio
    async def test_mechanical_fallback_without_model(self):
        """Without a model, compaction still works via mechanical fallback."""
        from duh.adapters.compact.summarize import SummarizeCompactor

        sc = SummarizeCompactor(call_model=None, bytes_per_token=1, min_keep=1)
        msgs = [
            _msg(content="A" * 200),
            _msg(content="B" * 200),
        ]
        result = await sc.compact(msgs, token_limit=220)

        # Should have summary + kept message
        assert len(result) >= 2
        summary_msgs = [
            m for m in result
            if isinstance(m, Message) and m.role == "system"
            and "Previous conversation summary" in m.content
        ]
        assert len(summary_msgs) == 1
