# tests/unit/test_bash_ast_heredoc.py
"""Tests for heredoc, process substitution, and ANSI-C quoting in bash_ast."""

from __future__ import annotations

import pytest

from duh.tools.bash_ast import tokenize, Segment, SegmentType, strip_wrappers


class TestHeredoc:
    def test_simple_heredoc(self):
        cmd = "cat <<EOF\nhello world\nEOF"
        segments = tokenize(cmd)
        # The heredoc content should be captured; the command itself is cat
        assert any("cat" in s.text for s in segments)

    def test_heredoc_with_dash(self):
        """<<- allows leading tabs to be stripped."""
        cmd = "cat <<-EOF\n\thello\nEOF"
        segments = tokenize(cmd)
        assert any("cat" in s.text for s in segments)

    def test_heredoc_quoted_delimiter(self):
        """Quoted delimiter means no variable expansion (but we just tokenize)."""
        cmd = "cat <<'END'\n$VAR stays literal\nEND"
        segments = tokenize(cmd)
        assert len(segments) >= 1

    def test_heredoc_in_pipeline(self):
        cmd = "cat <<EOF | grep hello\nfoo\nhello\nEOF"
        segments = tokenize(cmd)
        assert any("grep" in s.text for s in segments)

    def test_heredoc_preserves_following_command(self):
        cmd = "cat <<EOF\ndata\nEOF\necho done"
        segments = tokenize(cmd)
        assert any("echo" in s.text for s in segments)


class TestProcessSubstitution:
    def test_input_process_substitution(self):
        cmd = "diff <(ls dir1) <(ls dir2)"
        segments = tokenize(cmd)
        # The main command is diff; process subs are extracted
        assert any("diff" in s.text for s in segments)
        # Process sub contents should appear as subshell segments
        assert any("ls dir1" in s.text for s in segments if s.seg_type == SegmentType.SUBSHELL)

    def test_output_process_substitution(self):
        cmd = "tee >(grep error > errors.log)"
        segments = tokenize(cmd)
        assert any("tee" in s.text for s in segments)
        assert any("grep error" in s.text for s in segments if s.seg_type == SegmentType.SUBSHELL)

    def test_nested_process_substitution(self):
        cmd = "diff <(sort file1) <(sort file2)"
        segments = tokenize(cmd)
        subshells = [s for s in segments if s.seg_type == SegmentType.SUBSHELL]
        assert len(subshells) >= 2


class TestAnsiCQuoting:
    def test_ansi_c_escape_newline(self):
        """$'...' with escape sequences should be treated as a quoted string."""
        cmd = "echo $'hello\\nworld'"
        segments = tokenize(cmd)
        assert len(segments) >= 1
        # The command should not be split on the escaped newline
        assert any("echo" in s.text for s in segments)

    def test_ansi_c_with_tab(self):
        cmd = "printf $'col1\\tcol2'"
        segments = tokenize(cmd)
        assert len(segments) >= 1

    def test_ansi_c_in_pipeline(self):
        cmd = "echo $'line1\\nline2' | grep line1"
        segments = tokenize(cmd)
        assert any("grep" in s.text for s in segments)


class TestWhitespaceNormalization:
    def test_extra_spaces_normalized(self):
        cmd = "ls    -la     /tmp"
        segments = tokenize(cmd)
        assert len(segments) == 1

    def test_tabs_in_command(self):
        cmd = "echo\thello"
        segments = tokenize(cmd)
        assert len(segments) == 1

    def test_mixed_whitespace(self):
        cmd = "  ls  &&  echo done  "
        segments = tokenize(cmd)
        assert len(segments) == 2
