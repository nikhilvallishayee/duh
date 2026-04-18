# ADR-074: TUI End-to-End Testing Strategy

**Status**: Proposed
**Date**: 2026-04-18
**Supersedes**: None
**Context**: Post ADR-073 TUI Parity Sprint — the TUI now has broad unit + in-process coverage, but zero tests catch real terminal rendering bugs (e.g. the Wave 2.7 RichRenderer cursor-rewind failure in non-TTY)

## Context

ADR-073 shipped three waves of TUI work ending with 5841 passing tests. Coverage today is:

| Tier | Mechanism | What it tests | Speed | Blind spot |
|------|-----------|---------------|-------|------------|
| Unit | widgets in isolation | State, handlers, classmethods | ms | Doesn't mount |
| In-process pilot | `app.run_test()` | Event routing, widget queries, submissions | ~100ms | Mocks the driver — no real ANSI output |

This leaves three categories of real bugs unreachable:

- **Escape-code regressions**: Wave 2.7 fixed a CSI-sequence leak that corrupted piped output. Nothing in the unit/pilot layer would catch that bug returning.
- **Visual regressions**: a theme change, a layout shift, a widget alignment break. Today there's no automated way to detect them.
- **Signal / lifecycle regressions**: Ctrl+C handling, PTY teardown, zombie processes, terminal restoration (cooked/raw mode, cursor visibility). These only manifest when the binary runs against a real terminal.

The industry-standard answer is a three-tier TUI E2E pyramid:

1. **Visual snapshot testing** (SVG/HTML captures diffed on CI) — catches visual regressions per-PR, fast, runs on every CI.
2. **Real PTY + terminal emulator** (`pexpect` + `pyte`) — spawns the real `duh` binary, sends real keystrokes, parses the real ANSI stream into a 2D screen buffer. Catches escape-code bugs, cursor bugs, non-TTY behavior.
3. **tmux-based multi-pane** (`libtmux`) — runs the binary inside a real `tmux` pane; captures what a human would actually see. Useful for mouse events, multi-pane interactions, and terminal-specific quirks that PTY + `pyte` doesn't model.

This ADR commits to shipping all three tiers.

## Decision

Ship three tiers of TUI E2E testing, each on its own CI cadence, each independently mergeable.

### Tier A: Visual snapshot testing

Add `pytest-textual-snapshot` as a dev dependency. Capture SVG snapshots of every key TUI state and diff them on CI.

**Files to create:**
- `tests/snapshots/` — new directory, parallel to `tests/unit/`
- `tests/snapshots/conftest.py` — shared fixtures (a boot script that launches `DuhApp` with a canned stub engine)
- `tests/snapshots/scripts/` — small `.py` scripts that launch the app in specific states (welcome, mid-stream, tool exec, permission modal, command palette, theme selector, plan mode)
- `tests/snapshots/__snapshots__/` — gitignored on creation, committed after review

**Screens to snapshot (Phase 1, ten total):**
1. Welcome banner (fresh start, no history)
2. Welcome banner with resumed session (5 messages pre-loaded)
3. Streaming response mid-text (50% through a paragraph)
4. Tool call with spinner animating (frame captured deterministically)
5. Tool result — success, DEFAULT style
6. Tool result — error, DEFAULT style
7. Tool result — success, CONCISE style
8. Tool result — success, VERBOSE style
9. Permission modal (Bash command pending approval)
10. Command palette (Ctrl+K open, filter = "mem")

**Second batch (after Phase 1 lands):**
11. Theme selector (Ctrl+T open)
12. Each of the 5 themes applied to welcome screen (5 snapshots)
13. Multi-line TextArea (3 lines of input)
14. Transcript virtualization placeholder ("… 42 older messages archived")
15. Live token counter mid-stream

**CI integration**: Snapshot job runs on every PR. Visual diff surfaces as a build artifact + PR comment. Dev workflow: `pytest tests/snapshots/ --snapshot-update` to regenerate after an intentional change, then review the SVG diff in the PR.

### Tier B: Real PTY + pyte emulation

Add `pexpect` + `pyte` as dev dependencies. Run the real `duh` binary inside a pseudo-TTY and parse the ANSI stream into a grid.

**Files to create:**
- `tests/integration/test_tui_pty.py` — 8 new tests
- `tests/integration/conftest.py` — `pty_duh()` fixture that spawns the binary with `DUH_STUB_PROVIDER=1` and a specific terminal size
- `tests/integration/pty_helpers.py` — `read_screen(child, screen, duration)`, `wait_for_text(screen, text, timeout)`, `capture_display(screen)`

**Scenarios to cover:**
1. `/help` renders without escape-code leakage (visible text contains command names, no raw `\033[`)
2. `/style concise` takes effect visibly (subsequent tool output on screen is shorter)
3. Ctrl+C graceful exit (no zombie process, exit code 0)
4. Multi-line Shift+Enter visible in terminal grid (textarea shows 2+ rows)
5. Non-TTY mode: `echo "/help" | duh -p` emits no CSI codes (direct assertion on byte stream)
6. Large tool output respects terminal width (no wrap corruption at 80-col vs 200-col)
7. Command palette (Ctrl+K) renders modal, Esc dismisses, focus returns to input
8. Theme switch (Ctrl+T → select → Enter) visibly changes colors (background color byte at a known position differs)

**Timing**: These tests are inherently slower (1-3s each). Mark them with `@pytest.mark.slow` and exclude from the default pytest run. CI runs them on a dedicated `slow-tests` job that fires nightly and on PRs touching `duh/ui/`, `duh/cli/repl*.py`, or the snapshot tests.

### Tier C: tmux-based multi-pane

Add `libtmux` + `tmux` (system dep) for tests that need real terminal semantics — mouse events, multi-pane interactions, real scroll history, terminal resize mid-run.

**Files to create:**
- `tests/integration/test_tui_tmux.py` — 6 tests, gated behind `@pytest.mark.tmux`
- `tests/integration/tmux_helpers.py` — `start_duh_in_tmux(cmd, width=120, height=40)`, `send_keys(pane, keys)`, `capture_pane(pane) -> str`

**Scenarios to cover:**
1. Real scroll history survives terminal resize (grow from 80×24 to 120×40, previous messages still visible on scrollback)
2. Mouse click on command palette item selects it (once we ship mouse support)
3. Long tool output visible via scrollback (`capture_pane -S -1000` returns full history)
4. Ctrl+C → prompt returns → session intact (TUI can accept more input after interrupt)
5. Terminal resize mid-stream (grow/shrink during text_delta; no crash, layout reflows)
6. Two `duh` instances in split panes don't interfere (session IDs distinct, outputs isolated)

**CI gating**: `@pytest.mark.tmux` is excluded by default. A dedicated `tmux-tests` job runs on macOS + Linux runners with `tmux` installed. Fires on release-candidate PRs and nightly. Windows runners skip this tier (tmux is Unix-only).

### Test selection + CI matrix

| Tier | Marker | Default pytest | PR CI | Nightly CI |
|------|--------|----------------|-------|------------|
| Unit (existing) | none | ✅ | ✅ | ✅ |
| In-process pilot (existing) | none | ✅ | ✅ | ✅ |
| A — Snapshots | `snapshot` | ✅ | ✅ | ✅ |
| B — PTY | `slow` | ❌ | ✅ | ✅ |
| C — tmux | `tmux` | ❌ | ❌ | ✅ |

The `slow` marker already exists per Wave 1/2 tests. The new `snapshot` and `tmux` markers get registered in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "slow: slow-running integration tests (excluded from default)",
    "snapshot: visual regression tests using pytest-textual-snapshot",
    "tmux: tmux-based integration tests requiring the tmux binary",
]
```

### Snapshot review ergonomics

Generated SVG snapshots can be large. To keep PR reviews readable:

1. Snapshot files live in `tests/snapshots/__snapshots__/` with one subdirectory per test module.
2. CI uploads the diff as an artifact; PR bot comments a link.
3. For developers: `pytest tests/snapshots/ --snapshot-update` regenerates locally. Review the SVGs visually (many editors render SVG inline), commit the updated snapshots.
4. Large rewrites: use `--snapshot-ignore-missing` on the first run to create baselines without failing.

### Dependency footprint

```toml
[project.optional-dependencies]
tui-e2e = [
    "pytest-textual-snapshot>=1.0,<2",
    "pexpect>=4.9,<5",
    "pyte>=0.8,<1",
    "libtmux>=0.35,<1",
]
```

`tui-e2e` is opt-in. CI installs it explicitly for the snapshot/slow/tmux jobs; a developer running `pip install -e ".[dev]"` doesn't get it by default unless they also ask for `".[tui-e2e]"`.

## Consequences

### Positive

- Wave 2.7 RichRenderer-style bugs (escape-code leaks in non-TTY) become automatically detected.
- Every PR that changes the TUI gets a visual diff, making theme / layout changes reviewable.
- Multi-pane behavior (scrollback, resize, interrupt recovery) gets real coverage.
- Three tiers give a cost/coverage tradeoff — fast snapshots catch most regressions; slow PTY catches the rest; tmux reserves the expensive machinery for what nothing else can cover.
- Optional install keeps local dev fast.

### Negative

- `pexpect` + `tmux` don't run on Windows runners. Windows testers lose Tier B+C coverage. Mitigation: snapshot tests (Tier A) work everywhere and already catch 70-80% of regressions.
- Snapshots add maintenance: every intentional UI change requires regenerating baselines. Mitigation: make the regen command trivial (`--snapshot-update`), and require snapshot updates in the same commit as the code change.
- CI time increases. Mitigation: Tier B+C run nightly, not per-PR by default. PR CI only picks up snapshots.
- Flakiness risk: timing-sensitive PTY tests can flake under load. Mitigation: generous timeouts, `wait_for_text()` polling helpers, no `sleep()` without a specific purpose.

### Neutral

- Adds a `slow-tests` and `tmux-tests` CI job to the workflow matrix. Estimated 5–10 min each nightly.
- Phase 1 snapshots (10 screens) land as an MVP in the first PR; Phase 2 (15 more) in a follow-up. Total ~25 snapshots is enough for thorough regression coverage without becoming a burden.

## References

- ADR-073 TUI Parity Sprint (just completed)
- ADR-011 REPL architecture
- `pytest-textual-snapshot`: https://github.com/Textualize/pytest-textual-snapshot
- `pyte`: https://github.com/selectel/pyte (ECMA-48 / ANSI X3.64 terminal emulator in pure Python)
- `pexpect`: https://pexpect.readthedocs.io/ (PTY control library)
- `libtmux`: https://libtmux.git-pull.com/ (programmatic tmux driver)
- Rust / Ratatui equivalent: `ratatui::testing::TestBackend` — not used here but referenced for symmetry
