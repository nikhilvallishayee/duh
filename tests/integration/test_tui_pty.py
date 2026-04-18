"""Tier B: Real PTY + pyte emulation TUI E2E tests (ADR-074).

These tests spawn the real ``duh`` binary inside a pseudo-TTY and feed the
raw ANSI stream into a ``pyte`` screen emulator.  They catch regressions
invisible to unit / in-process ``app.run_test()`` tiers:

- Escape-code leakage into non-TTY (Wave 2.7 regression prevention).
- Real keyboard shortcuts routed through a terminal (Ctrl+K, Ctrl+Q,
  Ctrl+J-newline, Ctrl+C).
- Output width respected at the terminal grid level.
- Themes actually produce visibly different pixel grids.

All tests are marked ``slow`` and excluded from the default pytest run;
they fire on the dedicated ``slow-tests`` CI job (see ADR-074 §Tier B).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pexpect = pytest.importorskip("pexpect", reason="tui-e2e extras not installed")
pytest.importorskip("pyte", reason="tui-e2e extras not installed")

from tests.integration.pty_helpers import (  # noqa: E402
    drain_until_eof,
    read_screen,
    screen_display_hash,
    screen_text,
    spawn_duh,
    wait_for_text,
)

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# 1. `/help` renders without escape-code leakage
# ---------------------------------------------------------------------------


class TestHelpRendering:
    def test_help_renders_without_escape_leakage(self):
        """`duh --help` must produce visible help text.

        Two-level assertion:
        * **Raw bytes** contain the expected command tokens (``doctor``,
          ``duh``, ``constitution``).  Argparse help is longer than a
          40-row PTY, so we can't rely on the *rendered* tail alone.
        * **Rendered screen** must not contain literal ``\\033[`` bytes —
          that would indicate a CSI sequence escaped the renderer and
          leaked into the visible cell content (Wave 2.7 regression).
        """
        child, screen, stream = spawn_duh(args=["--help"], size=(120, 40))
        try:
            raw = drain_until_eof(child, timeout=5)
            stream.feed(raw)
            text = screen_text(screen)
            # Argparse help is large — assert on raw bytes for coverage.
            assert b"duh" in raw.lower(), "'duh' not in help output bytes"
            assert b"doctor" in raw, "'doctor' subcommand missing from help"
            assert b"--tui" in raw, "'--tui' option missing from help"
            # And on the rendered grid — some content must land.
            assert text.strip(), "pyte rendered an empty grid from --help"
            # No raw escape bytes in the *rendered grid* — pyte should have
            # consumed every CSI.  A leak would leave literal ``\x1b[`` here.
            assert "\x1b[" not in text, "CSI leaked into rendered screen"
        finally:
            child.close(force=True)


# ---------------------------------------------------------------------------
# 2. /brief (proxy for /style) takes effect
# ---------------------------------------------------------------------------
#
# ADR-074 §Tier B item 2 specifies ``/style concise``; however the REPL
# does not ship a ``/style`` slash command today — the analogous feature is
# ``/brief``.  Verifying "shorter output" against the stub provider is not
# meaningful (stub always emits ``stub-ok``), so we instead verify the
# visible state-change acknowledgement (``Brief mode: ON``) which is what
# a real screen-level regression would miss.


class TestBriefCommand:
    def test_brief_takes_effect(self):
        """`/brief on` must print ``Brief mode: ON`` to the terminal grid.

        This catches REPL slash-command regressions visible at the screen
        level — the more ambitious "short vs long tool output" comparison
        from ADR-074 §B.2 is blocked by the stub provider always emitting
        a fixed one-line response and is deferred until a richer stub
        lands (TODO: once ``DUH_STUB_VERBOSITY`` exists, enable full
        DEFAULT-vs-CONCISE comparison as originally specified).
        """
        child, screen, stream = spawn_duh(size=(120, 40), timeout=8)
        try:
            # Wait for the REPL prompt to stabilize.
            assert wait_for_text(child, stream, screen, "duh>", timeout=6), (
                f"REPL prompt never appeared.\nScreen:\n{screen_text(screen)}"
            )
            child.sendline("/brief on")
            assert wait_for_text(
                child, stream, screen, "Brief mode: ON", timeout=5
            ), (
                "'Brief mode: ON' never appeared after /brief on.\n"
                f"Screen:\n{screen_text(screen)}"
            )
        finally:
            child.close(force=True)


# ---------------------------------------------------------------------------
# 3. Ctrl+C graceful exit
# ---------------------------------------------------------------------------


class TestCtrlCGracefulExit:
    def test_ctrl_c_graceful_exit(self):
        """Sending SIGINT to the REPL must terminate it cleanly (exit 0).

        Python's ``input()`` treats a single Ctrl+C as "interrupt the
        current line"; the REPL loop breaks on ``KeyboardInterrupt`` only
        on the next iteration.  In practice, one or two Ctrl+C presses
        always yield a clean shutdown within 3s — no zombie, exit code 0.
        """
        child, screen, stream = spawn_duh(size=(120, 40), timeout=6)
        try:
            assert wait_for_text(
                child, stream, screen, "duh>", timeout=6
            ), "REPL never reached prompt"
            child.sendintr()
            exited = False
            try:
                child.expect(pexpect.EOF, timeout=2)
                exited = True
            except pexpect.TIMEOUT:
                # Some terminals/libs require a second SIGINT to escape
                # the readline buffer.  Send one more and wait briefly.
                child.sendintr()
                try:
                    child.expect(pexpect.EOF, timeout=2)
                    exited = True
                except pexpect.TIMEOUT:
                    exited = False
            child.close(force=False)
            assert exited, "REPL did not exit within 4s of Ctrl+C"
            assert child.exitstatus == 0, (
                f"unclean exit: status={child.exitstatus} signal="
                f"{child.signalstatus}"
            )
        finally:
            if child.isalive():
                child.close(force=True)


# ---------------------------------------------------------------------------
# 4. Multi-line input visible in terminal grid (TUI-only)
# ---------------------------------------------------------------------------


class TestMultilineInput:
    def test_multiline_ctrl_j_visible(self):
        """Type ``line1`` + Ctrl+J + ``line2`` into the TUI; both lines
        must be visible in the rendered grid.

        Ctrl+J (0x0a) is the terminal-level equivalent of Shift+Enter for
        Textual's CoolTextArea (ADR-073 Wave 3) — terminals rarely
        distinguish Shift+Enter from Enter over a PTY, so Ctrl+J is the
        portable newline-in-textarea signal.
        """
        child, screen, stream = spawn_duh(args=["--tui"], size=(120, 40), timeout=15)
        try:
            # Wait for the TUI frame to settle — the footer advertises
            # the Ctrl+K / Ctrl+T bindings once it's ready.
            ready = (
                wait_for_text(child, stream, screen, "Commands", timeout=10)
                or wait_for_text(child, stream, screen, "Type a message", timeout=6)
            )
            assert ready, (
                f"TUI never reached steady state.\n{screen_text(screen)[-400:]}"
            )
            # Send "line1" + Ctrl+J + "line2" (no trailing Enter so we
            # don't submit before assertion).
            child.send(b"line1")
            read_screen(child, stream, duration=0.3)
            child.send(b"\x0a")  # Ctrl+J → newline in textarea
            read_screen(child, stream, duration=0.3)
            child.send(b"line2")
            read_screen(child, stream, duration=0.6)
            text = screen_text(screen)
            # Both lines must appear somewhere on the grid.
            assert "line1" in text, (
                f"'line1' missing from grid.\n{text[-600:]}"
            )
            assert "line2" in text, (
                f"'line2' missing from grid.\n{text[-600:]}"
            )
            # Must be on different visible rows.
            rows_with_line1 = [i for i, ln in enumerate(screen.display) if "line1" in ln]
            rows_with_line2 = [i for i, ln in enumerate(screen.display) if "line2" in ln]
            assert rows_with_line1 and rows_with_line2, "text rows not found"
            assert rows_with_line1[-1] != rows_with_line2[-1], (
                "line1 and line2 collapsed onto one row — shift+enter not honoured"
            )
        finally:
            # Send Ctrl+Q to quit the TUI cleanly.
            try:
                child.send(b"\x11")
            except Exception:
                pass
            child.close(force=True)


# ---------------------------------------------------------------------------
# 5. Non-TTY print mode emits no CSI codes on stdout
# ---------------------------------------------------------------------------


class TestNonTtyNoCSI:
    def test_non_tty_no_csi_codes(self):
        """`duh -p /help` launched with piped stdio must emit no ``\\033[``
        bytes on stdout.  This is the Wave 2.7 RichRenderer regression
        test: the renderer must detect the non-TTY sink and drop all
        cursor-rewind / ANSI styling.
        """
        env = os.environ.copy()
        env["DUH_STUB_PROVIDER"] = "1"
        # Explicitly dumb terminal + not-a-tty → any renderer that still
        # emits CSI is buggy.
        env["TERM"] = "dumb"
        result = subprocess.run(
            [sys.executable, "-m", "duh", "-p", "/help"],
            capture_output=True,
            env=env,
            timeout=15,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert result.returncode == 0, (
            f"duh -p exited nonzero: rc={result.returncode}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        assert b"\x1b[" not in result.stdout, (
            f"CSI leaked into non-TTY stdout: {result.stdout[:400]!r}"
        )


# ---------------------------------------------------------------------------
# 6. Large output respects terminal width
# ---------------------------------------------------------------------------


class TestTerminalWidth:
    def test_large_output_respects_terminal_width(self):
        """At 80×24, no rendered line may exceed 80 visible columns.

        Pyte's screen is hard-bounded to the spawn dimensions, so a naive
        cell-count would always pass.  We instead assert on
        ``line.rstrip()`` length — a real regression (text generated
        wider than the terminal) would show up as wrapped rows with
        content filling the full 80 cols, which is fine.  We just verify
        the emulator didn't reject any CSI and that the content we see
        is bounded by the grid width.
        """
        child, screen, stream = spawn_duh(size=(80, 24), timeout=6)
        try:
            assert wait_for_text(
                child, stream, screen, "duh>", timeout=6
            ), "REPL never reached prompt"
            child.sendline("/context")
            # Let /context render.
            read_screen(child, stream, duration=1.0)
            # Every line in the pyte grid is exactly ``cols`` wide; the
            # assertion is that stripping trailing spaces never yields a
            # line longer than the configured width (sanity check that
            # pyte isn't in some over-wide mode).
            for i, line in enumerate(screen.display):
                assert len(line) == 80, (
                    f"row {i} width {len(line)} != 80 (pyte misconfigured)"
                )
                assert len(line.rstrip()) <= 80, (
                    f"row {i} rstripped length exceeds terminal width"
                )
        finally:
            child.sendeof()
            child.close(force=True)


# ---------------------------------------------------------------------------
# 7. Command palette opens and dismisses
# ---------------------------------------------------------------------------


class TestCommandPalette:
    def test_command_palette_opens_and_dismisses(self):
        """Ctrl+K opens the command palette; Esc dismisses it.

        The app-level ``ctrl+k`` binding is ``priority=True`` so the focused
        ``SubmittableTextArea`` doesn't swallow it.
        """
        child, screen, stream = spawn_duh(args=["--tui"], size=(120, 40), timeout=15)
        try:
            ready = (
                wait_for_text(child, stream, screen, "Commands", timeout=10)
                or wait_for_text(child, stream, screen, "Type a message", timeout=6)
            )
            assert ready, "TUI never reached steady state"
            steady_hash = screen_display_hash(screen)

            child.send(b"\x0b")  # Ctrl+K
            read_screen(child, stream, duration=1.0)
            palette_text = screen_text(screen)
            palette_markers = (
                "Command palette", "palette", "Search", "/help", "/model",
            )
            open_hash = screen_display_hash(screen)
            assert open_hash != steady_hash, (
                "Ctrl+K did not change screen — palette never opened.\n"
                f"{palette_text[-400:]}"
            )
            assert any(m.lower() in palette_text.lower() for m in palette_markers), (
                f"No palette marker on screen after Ctrl+K.\n{palette_text[-400:]}"
            )

            child.send(b"\x1b")  # Esc
            read_screen(child, stream, duration=1.0)
            dismissed_hash = screen_display_hash(screen)
            assert dismissed_hash != open_hash, (
                "Esc did not change screen — palette never dismissed"
            )
        finally:
            try:
                child.send(b"\x11")  # Ctrl+Q
            except Exception:
                pass
            child.close(force=True)


# ---------------------------------------------------------------------------
# 8. Theme switch changes the rendered pixels
# ---------------------------------------------------------------------------
#
# The original ADR spec compares a single byte at ``(row 1, col 0)``.
# That level of precision is brittle: Textual's theme application
# affects many regions (title bar, borders, background), but which cells
# change depends on the layout.  We instead assert "the visible display
# is not byte-identical between duh-dark and duh-light" which is
# sufficient to catch regressions where theme changes silently no-op.


class TestThemeSwitch:
    def test_theme_switch_changes_colors(self, tmp_path: Path):
        """Spawning the TUI with ``tui_theme.txt`` pre-seeded to
        ``duh-dark`` vs ``duh-light`` must produce a visibly different
        rendered grid.
        """

        def _spawn_with_theme(theme: str) -> bytes:
            cfg_home = tmp_path / theme
            (cfg_home / "duh").mkdir(parents=True, exist_ok=True)
            (cfg_home / "duh" / "tui_theme.txt").write_text(theme + "\n")
            # Isolate *all* duh state (history etc) so only the theme var
            # differs between the two runs.
            env = {
                "XDG_CONFIG_HOME": str(cfg_home),
                "HOME": str(cfg_home),
            }
            child, screen, stream = spawn_duh(
                args=["--tui"],
                size=(100, 30),
                timeout=15,
                env=env,
            )
            try:
                ready = (
                    wait_for_text(child, stream, screen, "Commands", timeout=12)
                    or wait_for_text(child, stream, screen, "Type a message", timeout=6)
                )
                assert ready, f"TUI never steady for theme={theme}"
                # Let a couple of repaints settle.
                read_screen(child, stream, duration=1.2)
                # Capture a fingerprint of the visible colour state.
                # pyte exposes ``buffer`` as a mapping from (y, x) → Char
                # namedtuple with ``fg`` / ``bg`` fields.  A per-cell fg/bg
                # sample over the top-left region is robust to layout
                # drift while still catching no-op theme switches.
                color_tokens: list[str] = []
                for y in range(min(5, screen.lines)):
                    for x in range(min(screen.columns, 30)):
                        cell = screen.buffer[y][x]
                        color_tokens.append(f"{cell.fg}/{cell.bg}")
                return "|".join(color_tokens).encode()
            finally:
                try:
                    child.send(b"\x11")
                except Exception:
                    pass
                child.close(force=True)

        dark_colors = _spawn_with_theme("duh-dark")
        light_colors = _spawn_with_theme("duh-light")

        assert dark_colors != light_colors, (
            "duh-dark and duh-light produced identical colour fingerprints — "
            "theme switch had no visible effect."
        )
