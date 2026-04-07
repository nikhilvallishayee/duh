"""Tests for NDJSON helpers and stream-json protocol."""

from __future__ import annotations

import io
import json

from duh.cli.ndjson import ndjson_read_line, ndjson_write


class TestNdjsonWrite:
    def test_basic_write(self):
        buf = io.StringIO()
        ndjson_write({"type": "text_delta", "text": "hello"}, file=buf)
        line = buf.getvalue()
        assert line.endswith("\n")
        parsed = json.loads(line)
        assert parsed["type"] == "text_delta"
        assert parsed["text"] == "hello"

    def test_escapes_line_separator(self):
        buf = io.StringIO()
        ndjson_write({"text": "before\u2028after"}, file=buf)
        raw = buf.getvalue()
        # U+2028 should be escaped in the output
        assert "\u2028" not in raw
        assert "\\u2028" in raw
        # But parsing it back should restore the character
        parsed = json.loads(raw)
        assert parsed["text"] == "before\u2028after"

    def test_escapes_paragraph_separator(self):
        buf = io.StringIO()
        ndjson_write({"text": "before\u2029after"}, file=buf)
        raw = buf.getvalue()
        assert "\u2029" not in raw
        assert "\\u2029" in raw

    def test_handles_non_serializable_values(self):
        buf = io.StringIO()
        ndjson_write({"obj": object()}, file=buf)
        line = buf.getvalue()
        parsed = json.loads(line)
        assert isinstance(parsed["obj"], str)

    def test_single_line(self):
        buf = io.StringIO()
        ndjson_write({"a": 1, "b": "hello\nworld"}, file=buf)
        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 1  # must be single line


class TestNdjsonReadLine:
    def test_parse_valid_json(self):
        result = ndjson_read_line('{"type": "user", "content": "hi"}')
        assert result == {"type": "user", "content": "hi"}

    def test_blank_line_returns_none(self):
        assert ndjson_read_line("") is None
        assert ndjson_read_line("   ") is None
        assert ndjson_read_line("\n") is None

    def test_non_json_line_returns_none(self):
        assert ndjson_read_line("hello world") is None
        assert ndjson_read_line("[1, 2, 3]") is None

    def test_strips_whitespace(self):
        result = ndjson_read_line('  {"type": "test"}  \n')
        assert result == {"type": "test"}
