"""Tests for the Hermes-style tool-arg repair middleware (ADR-028)."""

from __future__ import annotations

import pytest

from duh.adapters.tool_repair import repair_tool_arguments


# ---- happy path -----------------------------------------------------

def test_valid_json_passthrough():
    out = repair_tool_arguments('{"path": "auth.py", "lines": 10}')
    assert out == {"path": "auth.py", "lines": 10}


def test_dict_input_passthrough():
    """Already-parsed dicts are returned unchanged."""
    d = {"already": "parsed"}
    assert repair_tool_arguments(d) is d


def test_none_returns_none():
    assert repair_tool_arguments(None) is None


def test_empty_string_returns_empty_dict():
    assert repair_tool_arguments("") == {}


# ---- repair: trailing commas ----------------------------------------

def test_trailing_comma_in_object():
    out = repair_tool_arguments('{"path": "a.py",}')
    assert out == {"path": "a.py"}


def test_trailing_comma_in_array():
    out = repair_tool_arguments('{"items": [1, 2, 3,]}')
    assert out == {"items": [1, 2, 3]}


def test_nested_trailing_commas():
    out = repair_tool_arguments('{"a": {"b": 1,}, "c": [1,],}')
    assert out == {"a": {"b": 1}, "c": [1]}


# ---- repair: Python literals ----------------------------------------

def test_python_true_lowercased():
    out = repair_tool_arguments('{"recursive": True}')
    assert out == {"recursive": True}


def test_python_false_lowercased():
    out = repair_tool_arguments('{"verbose": False}')
    assert out == {"verbose": False}


def test_python_none_lowercased():
    out = repair_tool_arguments('{"timeout": None}')
    assert out == {"timeout": None}


def test_quoted_python_literals_preserved():
    """Quoted ``"True"`` is a string, not a boolean — leave it alone."""
    out = repair_tool_arguments('{"label": "True"}')
    assert out == {"label": "True"}


# ---- repair: smart quotes -------------------------------------------

def test_smart_double_quotes():
    # Wrap the JSON in smart quotes — common when a model formats prose.
    out = repair_tool_arguments('\u201c\u201d') or {}
    # Empty smart-quoted "" is recoverable as empty dict via prose-strip.
    # Real test: smart quotes inside an actual JSON-shape body.
    out = repair_tool_arguments('{\u201cpath\u201d: \u201cauth.py\u201d}')
    assert out == {"path": "auth.py"}


# ---- repair: bare control chars in strings --------------------------

def test_unescaped_newline_in_string():
    raw = '{"description": "line1\nline2"}'
    out = repair_tool_arguments(raw)
    assert out == {"description": "line1\nline2"}


def test_unescaped_tab_in_string():
    raw = '{"label": "col1\tcol2"}'
    out = repair_tool_arguments(raw)
    assert out == {"label": "col1\tcol2"}


# ---- repair: prose wrapper ------------------------------------------

def test_prose_wrapper_stripped():
    raw = 'Here is the tool call:\n{"path": "x.py"}\nThanks!'
    out = repair_tool_arguments(raw)
    assert out == {"path": "x.py"}


def test_prose_wrapper_with_array():
    raw = 'The list is [1,2,3]'
    # Top-level array → returns empty dict (only object payloads count
    # as tool-call arguments).
    out = repair_tool_arguments(raw)
    assert out == {}


# ---- combined repairs ------------------------------------------------

def test_python_literals_plus_trailing_commas():
    raw = '{"recursive": True, "ignore": None,}'
    out = repair_tool_arguments(raw)
    assert out == {"recursive": True, "ignore": None}


def test_prose_plus_smart_quotes_plus_trailing_comma():
    raw = 'Sure, here you go:\n{\u201cpath\u201d: \u201cmain.py\u201d, \u201cverbose\u201d: True,}'
    out = repair_tool_arguments(raw)
    assert out == {"path": "main.py", "verbose": True}


# ---- unrecoverable inputs --------------------------------------------

def test_completely_garbage_returns_none():
    out = repair_tool_arguments("not json at all just text")
    assert out is None


def test_unbalanced_braces_returns_none():
    out = repair_tool_arguments('{"path": "x.py"')
    assert out is None
