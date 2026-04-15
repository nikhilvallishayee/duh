"""Tests for D.U.H. ASCII art logos."""

from __future__ import annotations

import io

from duh.ui.logo import (
    LOGO_COMPACT,
    LOGO_LARGE,
    LOGO_MINI,
    print_logo,
)


def test_large_logo_is_nonempty() -> None:
    assert len(LOGO_LARGE.strip()) > 50
    assert "____" in LOGO_LARGE
    assert "Universal" in LOGO_LARGE


def test_compact_logo_is_nonempty() -> None:
    assert len(LOGO_COMPACT.strip()) > 10
    assert "Universal" in LOGO_COMPACT


def test_mini_logo_is_nonempty() -> None:
    assert "D.U.H." in LOGO_MINI
    assert "Universal" in LOGO_MINI


def test_print_logo_large_no_color() -> None:
    buf = io.StringIO()
    print_logo("large", color=False, file=buf)
    out = buf.getvalue()
    assert "____" in out
    assert "\033[" not in out  # no ANSI


def test_print_logo_compact_no_color() -> None:
    buf = io.StringIO()
    print_logo("compact", color=False, file=buf)
    out = buf.getvalue()
    assert "Universal" in out
    assert "\033[" not in out


def test_print_logo_mini_no_color() -> None:
    buf = io.StringIO()
    print_logo("mini", color=False, file=buf)
    out = buf.getvalue()
    assert "D.U.H." in out
    assert "\033[" not in out


def test_print_logo_no_color_on_non_tty() -> None:
    """StringIO has no isatty → color should be disabled even if color=True."""
    buf = io.StringIO()
    print_logo("large", color=True, file=buf)
    out = buf.getvalue()
    assert "\033[" not in out


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_print_logo_color_on_tty() -> None:
    buf = _FakeTTY()
    print_logo("compact", color=True, file=buf)
    out = buf.getvalue()
    assert "\033[" in out  # ANSI codes present


def test_print_logo_large_color_on_tty() -> None:
    buf = _FakeTTY()
    print_logo("large", color=True, file=buf)
    out = buf.getvalue()
    assert "\033[" in out
    assert "____" in out


def test_print_logo_mini_color_on_tty() -> None:
    buf = _FakeTTY()
    print_logo("mini", color=True, file=buf)
    out = buf.getvalue()
    assert "\033[" in out
    # D, U, H each wrapped in ANSI — check they're all present
    assert "D" in out and "U" in out and "H" in out
    assert "niversal" in out


def test_default_style_is_compact() -> None:
    buf = io.StringIO()
    print_logo(file=buf, color=False)
    out = buf.getvalue()
    assert "Universal" in out
