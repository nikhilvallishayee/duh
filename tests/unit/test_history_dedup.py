"""Tests for history deduplication in duh.adapters.simple_compactor.

Covers _deduplicate_messages and its integration with compact().
"""

import pytest

from duh.adapters.simple_compactor import (
    SimpleCompactor,
    _deduplicate_messages,
    _extract_tool_uses,
    _extract_tool_results,
    _tool_use_signature,
)
from duh.kernel.messages import Message, TextBlock, ToolUseBlock, ToolResultBlock


# ---------------------------------------------------------------------------
# Helpers — build realistic message sequences
# ---------------------------------------------------------------------------

def _assistant_with_tool_use(tool_id: str, name: str, tool_input: dict,
                              text: str = "") -> Message:
    """Assistant message containing a tool_use block (and optional text)."""
    blocks: list = []
    if text:
        blocks.append(TextBlock(text=text))
    blocks.append(ToolUseBlock(id=tool_id, name=name, input=tool_input))
    return Message(role="assistant", content=blocks, id=f"a-{tool_id}", timestamp="t")


def _user_with_tool_result(tool_use_id: str, output: str = "ok") -> Message:
    """User message containing a tool_result block."""
    return Message(
        role="user",
        content=[
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": output},
        ],
        id=f"u-{tool_use_id}",
        timestamp="t",
    )


def _user_text(text: str) -> Message:
    return Message(role="user", content=text, id="ut", timestamp="t")


def _assistant_text(text: str) -> Message:
    return Message(role="assistant", content=text, id="at", timestamp="t")


def _system(text: str) -> Message:
    return Message(role="system", content=text, id="sys", timestamp="t")


# ---------------------------------------------------------------------------
# _deduplicate_messages — unit tests
# ---------------------------------------------------------------------------

class TestDeduplicateMessages:
    """Direct tests of the _deduplicate_messages helper."""

    def test_empty_list(self):
        assert _deduplicate_messages([]) == []

    def test_no_tool_use_passthrough(self):
        """Messages without tool_use blocks pass through unchanged."""
        msgs = [_user_text("hi"), _assistant_text("hello")]
        result = _deduplicate_messages(msgs)
        assert len(result) == 2
        assert result[0].content == "hi"
        assert result[1].content == "hello"

    def test_single_tool_use_no_duplicate(self):
        """A single tool call is not a duplicate — kept as-is."""
        msgs = [
            _assistant_with_tool_use("t1", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t1", "file contents"),
        ]
        result = _deduplicate_messages(msgs)
        assert len(result) == 2
        # tool_use block should still be present
        tool_uses = _extract_tool_uses(result[0])
        assert len(tool_uses) == 1
        assert tool_uses[0]["id"] == "t1"

    def test_duplicate_file_read_keeps_latest(self):
        """Same Read tool with same input twice — only the latest survives."""
        msgs = [
            _assistant_with_tool_use("t1", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t1", "old contents"),
            _assistant_text("thinking..."),
            _assistant_with_tool_use("t2", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t2", "new contents"),
        ]
        result = _deduplicate_messages(msgs)
        # t1 (tool_use + tool_result) should be stripped
        all_tool_uses = []
        all_tool_results = []
        for m in result:
            all_tool_uses.extend(_extract_tool_uses(m))
            all_tool_results.extend(_extract_tool_results(m))
        assert len(all_tool_uses) == 1
        assert all_tool_uses[0]["id"] == "t2"
        assert len(all_tool_results) == 1
        assert all_tool_results[0]["tool_use_id"] == "t2"

    def test_different_files_both_kept(self):
        """Reads of different files are not duplicates — both kept."""
        msgs = [
            _assistant_with_tool_use("t1", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t1", "contents a"),
            _assistant_with_tool_use("t2", "Read", {"file_path": "/b.py"}),
            _user_with_tool_result("t2", "contents b"),
        ]
        result = _deduplicate_messages(msgs)
        all_tu = []
        for m in result:
            all_tu.extend(_extract_tool_uses(m))
        assert len(all_tu) == 2

    def test_different_tools_same_input_both_kept(self):
        """Different tool names with same input are not duplicates."""
        msgs = [
            _assistant_with_tool_use("t1", "Read", {"path": "/x"}),
            _user_with_tool_result("t1", "read result"),
            _assistant_with_tool_use("t2", "Grep", {"path": "/x"}),
            _user_with_tool_result("t2", "grep result"),
        ]
        result = _deduplicate_messages(msgs)
        all_tu = []
        for m in result:
            all_tu.extend(_extract_tool_uses(m))
        assert len(all_tu) == 2

    def test_redundant_non_read_tool_result_stripped(self):
        """Any tool called twice with same input — earlier call stripped."""
        msgs = [
            _assistant_with_tool_use("t1", "Bash", {"command": "ls"}),
            _user_with_tool_result("t1", "first ls"),
            _assistant_text("hmm"),
            _assistant_with_tool_use("t2", "Bash", {"command": "ls"}),
            _user_with_tool_result("t2", "second ls"),
        ]
        result = _deduplicate_messages(msgs)
        all_tu = []
        for m in result:
            all_tu.extend(_extract_tool_uses(m))
        assert len(all_tu) == 1
        assert all_tu[0]["id"] == "t2"

    def test_system_messages_untouched(self):
        """System messages are never modified or removed."""
        msgs = [
            _system("You are helpful."),
            _assistant_with_tool_use("t1", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t1", "v1"),
            _assistant_with_tool_use("t2", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t2", "v2"),
        ]
        result = _deduplicate_messages(msgs)
        system_msgs = [m for m in result if m.role == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0].content == "You are helpful."

    def test_message_dropped_when_all_blocks_removed(self):
        """If removing stale blocks empties a message, the message is dropped."""
        msgs = [
            _assistant_with_tool_use("t1", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t1", "old"),
            _assistant_with_tool_use("t2", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t2", "new"),
        ]
        result = _deduplicate_messages(msgs)
        # The first assistant msg (only had tool_use t1) and the first user
        # msg (only had tool_result for t1) should both be dropped entirely.
        assert len(result) == 2

    def test_message_with_mixed_blocks_partial_strip(self):
        """A message with both text and stale tool_use keeps the text."""
        msgs = [
            _assistant_with_tool_use("t1", "Read", {"file_path": "/a.py"},
                                      text="Let me read that."),
            _user_with_tool_result("t1", "old"),
            _assistant_with_tool_use("t2", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t2", "new"),
        ]
        result = _deduplicate_messages(msgs)
        # First assistant msg should keep its TextBlock but lose tool_use
        first_asst = [m for m in result if hasattr(m, 'role') and
                      m.role == "assistant"][0]
        assert any(
            (isinstance(b, TextBlock) and b.text == "Let me read that.")
            or (isinstance(b, dict) and b.get("text") == "Let me read that.")
            for b in first_asst.content
        )
        tool_uses_in_first = _extract_tool_uses(first_asst)
        assert len(tool_uses_in_first) == 0

    def test_three_reads_of_same_file(self):
        """Three reads of the same file — only the last survives."""
        msgs = [
            _assistant_with_tool_use("t1", "Read", {"file_path": "/x.py"}),
            _user_with_tool_result("t1", "v1"),
            _assistant_with_tool_use("t2", "Read", {"file_path": "/x.py"}),
            _user_with_tool_result("t2", "v2"),
            _assistant_with_tool_use("t3", "Read", {"file_path": "/x.py"}),
            _user_with_tool_result("t3", "v3"),
        ]
        result = _deduplicate_messages(msgs)
        all_tu = []
        all_tr = []
        for m in result:
            all_tu.extend(_extract_tool_uses(m))
            all_tr.extend(_extract_tool_results(m))
        assert len(all_tu) == 1
        assert all_tu[0]["id"] == "t3"
        assert len(all_tr) == 1
        assert all_tr[0]["tool_use_id"] == "t3"

    def test_dict_messages_deduplication(self):
        """Dedup works with plain dict messages too (not just Message objects)."""
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": "/a.py"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "old"},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t2", "name": "Read",
                 "input": {"file_path": "/a.py"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t2", "content": "new"},
            ]},
        ]
        result = _deduplicate_messages(msgs)
        all_tu = []
        for m in result:
            all_tu.extend(_extract_tool_uses(m))
        assert len(all_tu) == 1
        assert all_tu[0]["id"] == "t2"

    def test_does_not_mutate_input(self):
        """_deduplicate_messages must not mutate the input list."""
        msgs = [
            _assistant_with_tool_use("t1", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t1", "old"),
            _assistant_with_tool_use("t2", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t2", "new"),
        ]
        original_len = len(msgs)
        original_ids = [m.id for m in msgs]
        _deduplicate_messages(msgs)
        assert len(msgs) == original_len
        assert [m.id for m in msgs] == original_ids


# ---------------------------------------------------------------------------
# Integration: dedup wired into compact()
# ---------------------------------------------------------------------------

class TestCompactWithDedup:
    """Verify _deduplicate_messages is called before tail-window logic."""

    async def test_compact_deduplicates_before_truncation(self):
        """Dedup reduces message count, changing what fits in the window."""
        c = SimpleCompactor(bytes_per_token=1, min_keep=1)
        # Two reads of the same file — dedup removes the first pair,
        # so fewer tokens compete for the budget.
        msgs = [
            _assistant_with_tool_use("t1", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t1", "a" * 50),
            _assistant_with_tool_use("t2", "Read", {"file_path": "/a.py"}),
            _user_with_tool_result("t2", "b" * 50),
            _user_text("final question"),
        ]
        # After dedup: 3 messages (t2 assistant, t2 result, final question)
        # Without dedup: 5 messages, more likely to need summarization
        result = await c.compact(msgs, token_limit=100_000)
        # All should fit without summarization after dedup
        summaries = [m for m in result
                     if hasattr(m, 'content') and isinstance(m.content, str)
                     and "Previous conversation summary" in m.content]
        assert len(summaries) == 0

    async def test_compact_still_works_with_no_duplicates(self):
        """compact() behaves identically when there are no duplicates."""
        c = SimpleCompactor()
        msgs = [_user_text("hello"), _assistant_text("hi")]
        result = await c.compact(msgs, token_limit=100_000)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    """Tests for _extract_tool_uses, _extract_tool_results, _tool_use_signature."""

    def test_extract_tool_uses_from_message(self):
        msg = _assistant_with_tool_use("t1", "Read", {"file_path": "/a.py"})
        uses = _extract_tool_uses(msg)
        assert len(uses) == 1
        assert uses[0]["id"] == "t1"
        assert uses[0]["name"] == "Read"

    def test_extract_tool_uses_from_dict(self):
        msg = {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"q": "x"}},
        ]}
        uses = _extract_tool_uses(msg)
        assert len(uses) == 1
        assert uses[0]["name"] == "Grep"

    def test_extract_tool_uses_string_content(self):
        msg = _assistant_text("no tools here")
        assert _extract_tool_uses(msg) == []

    def test_extract_tool_results_from_user_msg(self):
        msg = _user_with_tool_result("t1", "output")
        results = _extract_tool_results(msg)
        assert len(results) == 1
        assert results[0]["tool_use_id"] == "t1"

    def test_extract_tool_results_string_content(self):
        msg = _user_text("just text")
        assert _extract_tool_results(msg) == []

    def test_tool_use_signature_deterministic(self):
        sig1 = _tool_use_signature("Read", {"b": 2, "a": 1})
        sig2 = _tool_use_signature("Read", {"a": 1, "b": 2})
        assert sig1 == sig2

    def test_tool_use_signature_different_names(self):
        sig1 = _tool_use_signature("Read", {"path": "/x"})
        sig2 = _tool_use_signature("Grep", {"path": "/x"})
        assert sig1 != sig2

    def test_tool_use_signature_different_inputs(self):
        sig1 = _tool_use_signature("Read", {"file_path": "/a.py"})
        sig2 = _tool_use_signature("Read", {"file_path": "/b.py"})
        assert sig1 != sig2
