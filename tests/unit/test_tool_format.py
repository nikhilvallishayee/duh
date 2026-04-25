"""Tests for tool-format adapters (ADR-026)."""

from __future__ import annotations

import pytest

from duh.adapters.tool_format import (
    REGISTRY,
    ToolFormat,
    detect_format,
    get_format,
)


# ---- registry shape -------------------------------------------------

def test_registry_has_five_formats():
    assert set(REGISTRY) == {
        "passthrough", "hermes", "gemma", "mistral", "morph_xml",
    }


def test_get_format_falls_back_to_passthrough():
    assert get_format("nope-not-a-real-format").name == "passthrough"
    assert get_format("hermes").name == "hermes"


# ---- detect_format pattern matching ---------------------------------

@pytest.mark.parametrize("model_id, expected", [
    ("openrouter/nousresearch/hermes-3-llama-3.1-405b", "hermes"),
    ("openrouter/google/gemma-4-31b-it",                 "gemma"),
    ("openrouter/mistralai/mistral-large-2512",          "mistral"),
    ("openrouter/zhipu/glm-4-plus",                      "morph_xml"),
    ("openrouter/openai/gpt-oss-120b",                   "passthrough"),
    ("claude-opus-4-7",                                  "passthrough"),
    ("gpt-5.4",                                          "passthrough"),
    ("",                                                  "passthrough"),
])
def test_detect_format(model_id, expected):
    assert detect_format(model_id) == expected


# ---- hermes parser ---------------------------------------------------

def test_hermes_parse_single_call():
    fmt = get_format("hermes")
    text = (
        "Reading the file first.\n"
        '<tool_call>{"name": "Read", "arguments": {"path": "auth.py"}}</tool_call>\n'
        "Then we will see."
    )
    cleaned, calls = fmt.parse_response(text)
    assert "Reading the file first." in cleaned
    assert "Then we will see." in cleaned
    assert "<tool_call>" not in cleaned
    assert len(calls) == 1
    assert calls[0].name == "Read"
    assert calls[0].arguments == {"path": "auth.py"}


def test_hermes_parse_multiple_calls():
    fmt = get_format("hermes")
    text = (
        '<tool_call>{"name": "Read", "arguments": {"path": "a.py"}}</tool_call>'
        '<tool_call>{"name": "Read", "arguments": {"path": "b.py"}}</tool_call>'
    )
    cleaned, calls = fmt.parse_response(text)
    assert cleaned == ""
    assert [c.name for c in calls] == ["Read", "Read"]
    assert calls[0].arguments == {"path": "a.py"}
    assert calls[1].arguments == {"path": "b.py"}


def test_hermes_parse_malformed_skips():
    fmt = get_format("hermes")
    text = '<tool_call>not valid json</tool_call><tool_call>{"name": "ok", "arguments": {}}</tool_call>'
    _, calls = fmt.parse_response(text)
    assert len(calls) == 1
    assert calls[0].name == "ok"


def test_hermes_inject_includes_tool_schema():
    fmt = get_format("hermes")
    tools = [{"name": "Read", "description": "read a file",
              "input_schema": {"type": "object",
                                "properties": {"path": {"type": "string"}}}}]
    out = fmt.inject_system(tools, "You are a helpful assistant.")
    assert "You are a helpful assistant." in out
    assert "<tool_call>" in out
    assert "Read" in out
    assert "path" in out


# ---- gemma parser ----------------------------------------------------

def test_gemma_parse_with_tool_call_wrapper():
    fmt = get_format("gemma")
    text = (
        "Let me check.\n"
        "```json\n"
        '{"tool_call": {"name": "Glob", "arguments": {"pattern": "*.py"}}}\n'
        "```\n"
        "Done."
    )
    cleaned, calls = fmt.parse_response(text)
    assert "Let me check." in cleaned
    assert "Done." in cleaned
    assert "```" not in cleaned
    assert len(calls) == 1
    assert calls[0].name == "Glob"
    assert calls[0].arguments == {"pattern": "*.py"}


def test_gemma_parse_keeps_non_tool_fences_as_prose():
    fmt = get_format("gemma")
    text = (
        "Here is some code:\n"
        "```python\n"
        "x = 1\n"
        "```\n"
    )
    cleaned, calls = fmt.parse_response(text)
    assert "```python" in cleaned
    assert "x = 1" in cleaned
    assert calls == []


def test_gemma_parse_flat_shape_tolerated():
    """Some models emit ``{name, arguments}`` directly without the wrapper."""
    fmt = get_format("gemma")
    text = '```json\n{"name": "Bash", "arguments": {"command": "ls"}}\n```'
    _, calls = fmt.parse_response(text)
    assert len(calls) == 1
    assert calls[0].name == "Bash"


# ---- mistral parser --------------------------------------------------

def test_mistral_parse_array():
    fmt = get_format("mistral")
    text = (
        "I will run two commands.\n"
        '[TOOL_CALLS] [{"name": "Bash", "arguments": {"command": "ls"}},'
        ' {"name": "Bash", "arguments": {"command": "pwd"}}]'
    )
    cleaned, calls = fmt.parse_response(text)
    assert "I will run two commands." in cleaned
    assert "[TOOL_CALLS]" not in cleaned
    assert [c.arguments["command"] for c in calls] == ["ls", "pwd"]


def test_mistral_parse_with_string_arguments():
    """Some Mistral fine-tunes serialise arguments as a JSON string."""
    fmt = get_format("mistral")
    text = '[TOOL_CALLS] [{"name": "Read", "arguments": "{\\"path\\": \\"x.py\\"}"}]'
    _, calls = fmt.parse_response(text)
    assert len(calls) == 1
    assert calls[0].arguments == {"path": "x.py"}


def test_mistral_parse_empty_array():
    fmt = get_format("mistral")
    text = "Nothing to call.\n[TOOL_CALLS] []"
    cleaned, calls = fmt.parse_response(text)
    assert "Nothing to call." in cleaned
    assert calls == []


# ---- morph_xml parser ------------------------------------------------

def test_morph_xml_parse():
    fmt = get_format("morph_xml")
    text = (
        "Reading the source.\n"
        "<tool>Read</tool>\n"
        '<args>{"path": "main.py"}</args>\n'
        "Done."
    )
    cleaned, calls = fmt.parse_response(text)
    assert "Reading the source." in cleaned
    assert "<tool>" not in cleaned
    assert len(calls) == 1
    assert calls[0].name == "Read"
    assert calls[0].arguments == {"path": "main.py"}


# ---- passthrough ------------------------------------------------------

def test_passthrough_does_not_extract_tool_calls():
    fmt = get_format("passthrough")
    text = '<tool_call>{"name": "Read", "arguments": {}}</tool_call>'
    cleaned, calls = fmt.parse_response(text)
    # Passthrough returns text unchanged, no extraction.
    assert cleaned == text
    assert calls == []


def test_passthrough_inject_does_not_modify_system():
    fmt = get_format("passthrough")
    out = fmt.inject_system([{"name": "Read"}], "Be helpful.")
    assert out == "Be helpful."


# ---- empty tools list — inject is a no-op for every format ---------

@pytest.mark.parametrize("fmt_name", ["hermes", "gemma", "mistral", "morph_xml"])
def test_inject_with_empty_tools_returns_base_unchanged(fmt_name):
    fmt = get_format(fmt_name)
    base = "system prompt"
    assert fmt.inject_system([], base) == base
