"""Differential fuzzer: all 5 provider adapters must parse the same
tool_use JSON into equivalent internal representations.

Any divergence is a router/executor confusion attack surface — an attacker
could craft a tool call that looks benign to the router and malicious to
the executor (ADR-054, 7.8)."""

from __future__ import annotations

import os

from hypothesis import given, settings, strategies as st

from duh.adapters.anthropic import AnthropicProvider
from duh.adapters.openai import OpenAIProvider
from duh.adapters.openai_chatgpt import OpenAIChatGPTProvider
from duh.adapters.ollama import OllamaProvider
from duh.adapters.stub_provider import StubProvider

# Strategy: well-formed tool_use blocks
tool_use_json = st.fixed_dictionaries({
    "type": st.just("tool_use"),
    "id": st.text(
        alphabet=st.characters(min_codepoint=48, max_codepoint=122),
        min_size=1, max_size=32,
    ),
    "name": st.sampled_from(["Bash", "Read", "Write", "Edit", "WebFetch", "Grep"]),
    "input": st.recursive(
        st.one_of(
            st.text(max_size=64),
            st.integers(min_value=-1000, max_value=1000),
            st.booleans(),
            st.none(),
        ),
        lambda children: (
            st.dictionaries(st.text(max_size=16), children, max_size=4)
            | st.lists(children, max_size=5)
        ),
        max_leaves=8,
    ),
})


ALL_PROVIDERS = [
    AnthropicProvider,
    OpenAIProvider,
    OpenAIChatGPTProvider,
    OllamaProvider,
    StubProvider,
]


@given(block=tool_use_json)
@settings(max_examples=500)  # fast for CI; nightly runs 10,000
def test_all_adapters_agree_on_tool_use_id(block) -> None:
    """Every adapter must extract the same tool use ID."""
    ids = []
    for cls in ALL_PROVIDERS:
        parsed = cls._parse_tool_use_block(block)
        ids.append(parsed.id)
    assert all(i == ids[0] for i in ids), f"ID divergence: {ids}"


@given(block=tool_use_json)
@settings(max_examples=500)
def test_all_adapters_agree_on_tool_name(block) -> None:
    """Every adapter must extract the same tool name."""
    names = []
    for cls in ALL_PROVIDERS:
        parsed = cls._parse_tool_use_block(block)
        names.append(parsed.name)
    assert all(n == names[0] for n in names), f"Name divergence: {names}"


@given(block=tool_use_json)
@settings(max_examples=500)
def test_all_adapters_agree_on_tool_input(block) -> None:
    """Every adapter must extract the same tool input dict."""
    inputs = []
    for cls in ALL_PROVIDERS:
        parsed = cls._parse_tool_use_block(block)
        inputs.append(parsed.input)
    assert all(i == inputs[0] for i in inputs), f"Input divergence: {inputs}"


@given(block=tool_use_json)
@settings(max_examples=int(os.environ.get("HYPOTHESIS_MAX_EXAMPLES", "500")))
def test_all_adapters_full_equivalence(block) -> None:
    """Combined equivalence check — id + name + input all must match."""
    ref = ALL_PROVIDERS[0]._parse_tool_use_block(block)
    for cls in ALL_PROVIDERS[1:]:
        parsed = cls._parse_tool_use_block(block)
        assert parsed.id == ref.id, f"{cls.__name__} ID mismatch"
        assert parsed.name == ref.name, f"{cls.__name__} name mismatch"
        assert parsed.input == ref.input, f"{cls.__name__} input mismatch"


# Edge case strategy: Unicode keys, deeply nested, empty values
edge_case_json = st.fixed_dictionaries({
    "type": st.just("tool_use"),
    "id": st.one_of(st.just(""), st.text(max_size=1)),
    "name": st.one_of(
        st.just(""),
        st.just("Bash"),
        st.text(alphabet=st.characters(min_codepoint=0x4E00, max_codepoint=0x9FFF), max_size=8),
    ),
    "input": st.one_of(
        st.just({}),
        st.just({"": ""}),
        st.just({"nested": {"deep": {"value": None}}}),
        st.dictionaries(
            st.text(
                alphabet=st.characters(min_codepoint=32, max_codepoint=0xFFFF),
                max_size=8,
            ),
            st.text(max_size=16),
            max_size=3,
        ),
    ),
})


@given(block=edge_case_json)
@settings(max_examples=200)
def test_edge_cases_all_agree(block) -> None:
    ref = ALL_PROVIDERS[0]._parse_tool_use_block(block)
    for cls in ALL_PROVIDERS[1:]:
        parsed = cls._parse_tool_use_block(block)
        assert parsed.id == ref.id
        assert parsed.name == ref.name
        assert parsed.input == ref.input
