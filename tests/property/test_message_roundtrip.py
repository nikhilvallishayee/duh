"""Property tests for duh.kernel.messages — Message round-trip & invariants.

Tests that the Message data model maintains structural integrity:
- Message -> dataclasses.asdict -> Message round-trips correctly
- merge_consecutive preserves message content
- validate_alternation guarantees strict role alternation
- Factory functions produce correct roles
- .text property extracts text consistently
"""

from __future__ import annotations

from dataclasses import asdict

from hypothesis import given, settings, assume, strategies as st

from duh.kernel.messages import (
    AssistantMessage,
    ImageBlock,
    Message,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    merge_consecutive,
    validate_alternation,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_roles = st.sampled_from(["user", "assistant", "system"])

_safe_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=200,
)

_nonempty_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=200,
)

_tool_id = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122),
    min_size=1,
    max_size=32,
)

_tool_name = st.sampled_from(["Bash", "Read", "Write", "Edit", "Grep", "Glob"])

_simple_value = st.one_of(
    st.text(max_size=50),
    st.integers(min_value=-1000, max_value=1000),
    st.booleans(),
    st.none(),
)

_tool_input = st.dictionaries(
    st.text(
        alphabet=st.characters(min_codepoint=48, max_codepoint=122),
        min_size=1,
        max_size=16,
    ),
    _simple_value,
    max_size=5,
)

_text_block = st.builds(TextBlock, text=_safe_text)

_tool_use_block = st.builds(
    ToolUseBlock,
    id=_tool_id,
    name=_tool_name,
    input=_tool_input,
)

_tool_result_block = st.builds(
    ToolResultBlock,
    tool_use_id=_tool_id,
    content=_safe_text,
    is_error=st.booleans(),
)

_thinking_block = st.builds(ThinkingBlock, thinking=_safe_text)

_any_block = st.one_of(
    _text_block,
    _tool_use_block,
    _tool_result_block,
    _thinking_block,
)

_block_content = st.lists(_any_block, min_size=1, max_size=5)

_message_content = st.one_of(_safe_text, _block_content)

_custom_id = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122),
    min_size=1,
    max_size=36,
)

_metadata = st.dictionaries(
    st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=10,
    ),
    st.text(max_size=30),
    max_size=3,
)


# ---------------------------------------------------------------------------
# Strategy: full Message
# ---------------------------------------------------------------------------

_message = st.builds(
    Message,
    role=_roles,
    content=_message_content,
    id=_custom_id,
    metadata=_metadata,
)

_string_message = st.builds(
    Message,
    role=_roles,
    content=_safe_text,
    id=_custom_id,
    metadata=_metadata,
)


# ---------------------------------------------------------------------------
# Property: Message -> asdict -> Message round-trip (string content)
# ---------------------------------------------------------------------------

@given(msg=_string_message)
@settings(max_examples=500)
def test_string_message_roundtrip_via_asdict(msg: Message) -> None:
    """A Message with string content must survive asdict -> reconstruction.
    This is the serialization path used by FileStore (JSONL persistence)."""
    d = asdict(msg)

    # Reconstruct
    restored = Message(
        role=d["role"],
        content=d["content"],
        id=d["id"],
        timestamp=d["timestamp"],
        metadata=d["metadata"],
    )

    assert restored.role == msg.role
    assert restored.content == msg.content
    assert restored.id == msg.id
    assert restored.timestamp == msg.timestamp
    assert restored.metadata == msg.metadata
    assert restored.text == msg.text


# ---------------------------------------------------------------------------
# Property: Message with block content -> asdict preserves structure
# ---------------------------------------------------------------------------

@given(msg=_message)
@settings(max_examples=500)
def test_message_asdict_preserves_structure(msg: Message) -> None:
    """asdict() on any Message must produce a dict with all required keys.
    This is the serialization format that FileStore writes to disk."""
    d = asdict(msg)
    assert isinstance(d, dict)
    assert "role" in d
    assert "content" in d
    assert "id" in d
    assert "timestamp" in d
    assert "metadata" in d
    assert d["role"] == msg.role
    assert d["id"] == msg.id


# ---------------------------------------------------------------------------
# Property: .text extracts text from any valid content
# ---------------------------------------------------------------------------

@given(msg=_message)
@settings(max_examples=500)
def test_text_property_always_returns_string(msg: Message) -> None:
    """Message.text must always return a string, regardless of content type."""
    text = msg.text
    assert isinstance(text, str)


# ---------------------------------------------------------------------------
# Property: .text for string content returns the string itself
# ---------------------------------------------------------------------------

@given(content=_safe_text, role=_roles)
@settings(max_examples=500)
def test_text_property_identity_for_string_content(content: str, role: str) -> None:
    """When content is a string, .text must return that exact string."""
    msg = Message(role=role, content=content)
    assert msg.text == content


# ---------------------------------------------------------------------------
# Property: .text for TextBlock list concatenates all text blocks
# ---------------------------------------------------------------------------

@given(texts=st.lists(_safe_text, min_size=1, max_size=5))
@settings(max_examples=500)
def test_text_property_concatenates_text_blocks(texts: list[str]) -> None:
    """When content is a list of TextBlocks, .text must concatenate them."""
    blocks = [TextBlock(text=t) for t in texts]
    msg = Message(role="assistant", content=blocks)
    assert msg.text == "".join(texts)


# ---------------------------------------------------------------------------
# Property: .has_tool_use is True iff ToolUseBlocks are present
# ---------------------------------------------------------------------------

@given(
    text_blocks=st.lists(_text_block, max_size=3),
    tool_blocks=st.lists(_tool_use_block, max_size=3),
)
@settings(max_examples=500)
def test_has_tool_use_consistency(
    text_blocks: list[TextBlock],
    tool_blocks: list[ToolUseBlock],
) -> None:
    """has_tool_use must be True iff the content list contains ToolUseBlocks."""
    all_blocks: list = list(text_blocks) + list(tool_blocks)
    if not all_blocks:
        return
    msg = Message(role="assistant", content=all_blocks)
    if tool_blocks:
        assert msg.has_tool_use is True
        assert len(msg.tool_use_blocks) == len(tool_blocks)
    else:
        assert msg.has_tool_use is False
        assert msg.tool_use_blocks == []


# ---------------------------------------------------------------------------
# Property: merge_consecutive preserves total text content
# ---------------------------------------------------------------------------

@given(messages=st.lists(
    st.builds(
        Message,
        role=st.sampled_from(["user", "assistant"]),
        content=_nonempty_text,
    ),
    min_size=1,
    max_size=10,
))
@settings(max_examples=500)
def test_merge_consecutive_preserves_text(messages: list[Message]) -> None:
    """After merging consecutive same-role messages, all original text
    must still be present in the merged result (possibly combined with
    other same-role texts via newline joining)."""
    merged = merge_consecutive(messages)

    # Collect all text from original and merged messages.
    # The merge joins consecutive same-role texts with "\n\n" then strips.
    # A message's text should appear as a substring of some merged message's
    # content (the raw content string, before .text extraction).
    all_merged_content = "\n\n".join(
        m.content if isinstance(m.content, str) else m.text
        for m in merged
    )

    for orig in messages:
        orig_text = orig.text.strip()
        if not orig_text:
            continue
        assert orig_text in all_merged_content, (
            f"Original text {orig_text!r} lost during merge. "
            f"All merged content: {all_merged_content[:500]!r}"
        )


# ---------------------------------------------------------------------------
# Property: merge_consecutive reduces consecutive same-role runs
# ---------------------------------------------------------------------------

@given(messages=st.lists(
    st.builds(
        Message,
        role=st.sampled_from(["user", "assistant"]),
        content=_nonempty_text,
    ),
    min_size=2,
    max_size=10,
))
@settings(max_examples=500)
def test_merge_consecutive_no_adjacent_same_role(messages: list[Message]) -> None:
    """After merge_consecutive, no two adjacent messages share a role."""
    merged = merge_consecutive(messages)
    for i in range(len(merged) - 1):
        assert merged[i].role != merged[i + 1].role, (
            f"Adjacent same-role after merge: "
            f"[{i}].role={merged[i].role}, [{i+1}].role={merged[i+1].role}"
        )


# ---------------------------------------------------------------------------
# Property: validate_alternation guarantees strict user/assistant alternation
# ---------------------------------------------------------------------------

@given(messages=st.lists(
    st.builds(
        Message,
        role=st.sampled_from(["user", "assistant"]),
        content=_nonempty_text,
    ),
    min_size=1,
    max_size=10,
))
@settings(max_examples=500)
def test_validate_alternation_strict(messages: list[Message]) -> None:
    """After validate_alternation, messages must strictly alternate roles
    and the first message must be from the user."""
    fixed = validate_alternation(messages)

    assert len(fixed) >= 1
    assert fixed[0].role == "user", (
        f"First message must be 'user', got {fixed[0].role!r}"
    )

    for i in range(len(fixed) - 1):
        assert fixed[i].role != fixed[i + 1].role, (
            f"Alternation violated at [{i}]={fixed[i].role}, [{i+1}]={fixed[i+1].role}"
        )


# ---------------------------------------------------------------------------
# Property: validate_alternation preserves all original messages
# ---------------------------------------------------------------------------

@given(messages=st.lists(
    st.builds(
        Message,
        role=st.sampled_from(["user", "assistant"]),
        content=_nonempty_text,
    ),
    min_size=1,
    max_size=10,
))
@settings(max_examples=500)
def test_validate_alternation_preserves_original_content(
    messages: list[Message],
) -> None:
    """Every non-synthetic message in the output should contain original content.
    The function may add synthetic messages but must not lose real ones.
    Note: merge_consecutive joins same-role texts with '\\n\\n' then strips,
    so we check for the stripped version of each original text."""
    fixed = validate_alternation(messages)

    # Collect all content from the fixed messages into one blob for searching.
    all_fixed_content = "\n\n".join(
        m.content if isinstance(m.content, str) else m.text
        for m in fixed
    )

    for orig in messages:
        orig_stripped = orig.text.strip()
        if not orig_stripped:
            continue
        assert orig_stripped in all_fixed_content, (
            f"Original text {orig_stripped!r} lost during validation. "
            f"Fixed content: {all_fixed_content[:500]!r}"
        )


# ---------------------------------------------------------------------------
# Property: factory functions produce correct roles
# ---------------------------------------------------------------------------

@given(content=_safe_text)
@settings(max_examples=500)
def test_factory_roles(content: str) -> None:
    """UserMessage, AssistantMessage, SystemMessage must set the correct role."""
    assert UserMessage(content).role == "user"
    assert AssistantMessage(content).role == "assistant"
    assert SystemMessage(content).role == "system"


# ---------------------------------------------------------------------------
# Property: factory messages are valid Message instances
# ---------------------------------------------------------------------------

@given(content=_safe_text, meta=_metadata)
@settings(max_examples=500)
def test_factory_messages_are_valid(content: str, meta: dict) -> None:
    """Factory-produced messages must be valid Message instances with
    all required fields populated."""
    for factory in (UserMessage, AssistantMessage, SystemMessage):
        msg = factory(content, metadata=meta)
        assert isinstance(msg, Message)
        assert msg.content == content
        assert msg.metadata == meta
        assert msg.id  # non-empty
        assert msg.timestamp  # non-empty


# ---------------------------------------------------------------------------
# Property: Message asdict -> load round-trip (FileStore path)
# ---------------------------------------------------------------------------

@given(
    role=st.sampled_from(["user", "assistant"]),
    content=_nonempty_text,
)
@settings(max_examples=500)
def test_filestore_roundtrip_string_messages(role: str, content: str) -> None:
    """Simulates the FileStore save/load path: Message -> asdict -> JSON-like
    dict -> reconstruct Message. The reconstructed message must match."""
    import json

    msg = Message(role=role, content=content)
    # Simulate FileStore: asdict -> json.dumps -> json.loads
    serialized = json.dumps(asdict(msg), ensure_ascii=False)
    loaded = json.loads(serialized)

    restored = Message(
        role=loaded["role"],
        content=loaded["content"],
        id=loaded["id"],
        timestamp=loaded["timestamp"],
        metadata=loaded["metadata"],
    )

    assert restored.role == msg.role
    assert restored.content == msg.content
    assert restored.text == msg.text
    assert restored.id == msg.id
