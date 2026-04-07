"""Tests for renderer adapters (ADR-011)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest

from duh.adapters.renderers import BareRenderer, JsonRenderer, select_renderer
from duh.ports.renderer import Renderer


# ---------------------------------------------------------------------------
# BareRenderer
# ---------------------------------------------------------------------------


class TestBareRenderer:
    def test_satisfies_renderer_protocol(self):
        r = BareRenderer()
        # All required methods exist
        assert hasattr(r, "render_text_delta")
        assert hasattr(r, "render_tool_use")
        assert hasattr(r, "render_tool_result")
        assert hasattr(r, "render_thinking")
        assert hasattr(r, "render_error")
        assert hasattr(r, "render_permission_request")
        assert hasattr(r, "finish")
        assert hasattr(r, "handle")

    def test_text_delta_writes_to_stdout(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.render_text_delta("hello")
        assert buf.getvalue() == "hello"

    def test_text_delta_sets_had_output(self):
        r = BareRenderer()
        assert r._had_output is False
        with patch("sys.stdout", StringIO()):
            r.render_text_delta("x")
        assert r._had_output is True

    def test_tool_use_writes_to_stderr(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_tool_use("Bash", {"command": "ls"})
        output = buf.getvalue()
        assert "Bash" in output
        assert "command" in output

    def test_tool_result_error_writes_to_stderr(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_tool_result("file not found", is_error=True)
        assert "file not found" in buf.getvalue()

    def test_tool_result_success_silent_without_debug(self):
        r = BareRenderer(debug=False)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_tool_result("ok", is_error=False)
        assert buf.getvalue() == ""

    def test_tool_result_success_shown_with_debug(self):
        r = BareRenderer(debug=True)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_tool_result("ok", is_error=False)
        assert "ok" in buf.getvalue()

    def test_thinking_shown_with_debug(self):
        r = BareRenderer(debug=True)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_thinking("hmm let me think")
        assert "hmm let me think" in buf.getvalue()

    def test_thinking_hidden_without_debug(self):
        r = BareRenderer(debug=False)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_thinking("hmm let me think")
        assert buf.getvalue() == ""

    def test_error_writes_to_stderr(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_error("something broke")
        assert "something broke" in buf.getvalue()

    def test_finish_prints_newline_if_had_output(self):
        r = BareRenderer()
        r._had_output = True
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.finish()
        assert buf.getvalue() == "\n"

    def test_finish_no_newline_if_no_output(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.finish()
        assert buf.getvalue() == ""

    def test_handle_dispatches_text_delta(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.handle({"type": "text_delta", "text": "hi"})
        assert buf.getvalue() == "hi"

    def test_handle_dispatches_tool_use(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.handle({"type": "tool_use", "name": "Read", "input": {"path": "/tmp"}})
        assert "Read" in buf.getvalue()

    def test_handle_dispatches_error(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.handle({"type": "error", "error": "boom"})
        assert "boom" in buf.getvalue()

    def test_handle_ignores_unknown_events(self):
        r = BareRenderer()
        # Should not raise
        r.handle({"type": "session", "session_id": "abc"})

    def test_permission_request_writes_to_stderr(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_permission_request("Bash", {"command": "rm -rf /"})
        output = buf.getvalue()
        assert "Bash" in output
        assert "Permission" in output


# ---------------------------------------------------------------------------
# JsonRenderer
# ---------------------------------------------------------------------------


class TestJsonRenderer:
    def test_collects_events(self):
        r = JsonRenderer()
        r.render_text_delta("hello")
        r.render_tool_use("Read", {"path": "/tmp"})
        r.render_error("oops")
        assert len(r._events) == 3

    def test_finish_writes_json_to_stdout(self):
        r = JsonRenderer()
        r.render_text_delta("hi")
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.finish()
        data = json.loads(buf.getvalue())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["type"] == "text_delta"
        assert data[0]["text"] == "hi"

    def test_handle_dispatches_all_event_types(self):
        r = JsonRenderer()
        r.handle({"type": "text_delta", "text": "x"})
        r.handle({"type": "tool_use", "name": "Y", "input": {}})
        r.handle({"type": "tool_result", "output": "z", "is_error": False})
        r.handle({"type": "thinking_delta", "text": "t"})
        r.handle({"type": "error", "error": "e"})
        assert len(r._events) == 5

    def test_tool_result_recorded(self):
        r = JsonRenderer()
        r.render_tool_result("output text", is_error=True)
        assert r._events[0]["is_error"] is True
        assert r._events[0]["output"] == "output text"

    def test_permission_request_recorded(self):
        r = JsonRenderer()
        r.render_permission_request("Bash", {"cmd": "ls"})
        assert r._events[0]["type"] == "permission_request"
        assert r._events[0]["tool"] == "Bash"


# ---------------------------------------------------------------------------
# select_renderer
# ---------------------------------------------------------------------------


class TestSelectRenderer:
    def test_json_format_returns_json_renderer(self):
        r = select_renderer(output_format="json")
        assert isinstance(r, JsonRenderer)

    def test_text_format_returns_bare_renderer(self):
        r = select_renderer(output_format="text")
        assert isinstance(r, BareRenderer)

    def test_default_returns_bare_renderer(self):
        r = select_renderer()
        assert isinstance(r, BareRenderer)

    def test_debug_passed_to_bare(self):
        r = select_renderer(output_format="text", debug=True)
        assert isinstance(r, BareRenderer)
        assert r.debug is True
