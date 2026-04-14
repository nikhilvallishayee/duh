"""Color theme and CSS constants for the D.U.H. Textual TUI (ADR-011 Tier 2)."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# CSS — inlined so the app file can reference it directly
# ---------------------------------------------------------------------------

APP_CSS = """
/* ── Screen layout ─────────────────────────────────────────── */
Screen {
    layout: vertical;
}

/* ── Header ────────────────────────────────────────────────── */
#header {
    height: 1;
    background: $primary;
    color: $text;
    content-align: left middle;
    padding: 0 1;
}

/* ── Body: sidebar + message log ───────────────────────────── */
#body {
    layout: horizontal;
    height: 1fr;
}

/* ── Sidebar ────────────────────────────────────────────────── */
#sidebar {
    width: 28;
    background: $surface;
    border-right: solid $primary-darken-2;
    padding: 1;
    display: none;
}

#sidebar.visible {
    display: block;
}

/* ── Message log ────────────────────────────────────────────── */
#message-log {
    width: 1fr;
    background: $background;
    padding: 0 1;
}

/* ── Individual message widgets ─────────────────────────────── */
.message-user {
    background: $primary-darken-3;
    border-left: thick $primary;
    padding: 0 1;
    margin: 1 0 0 0;
    color: $text;
}

.message-assistant {
    background: $surface;
    border-left: thick $success;
    padding: 0 1;
    margin: 1 0 0 0;
    color: $text;
}

.message-role-label {
    text-style: bold;
    color: $text-muted;
    margin-bottom: 0;
}

.message-body {
    padding: 0;
    margin: 0;
}

/* ── Tool call widget ───────────────────────────────────────── */
.tool-call-widget {
    background: $surface-darken-1;
    border-left: thick $warning;
    padding: 0 1;
    margin: 0 0 0 2;
}

.tool-call-label {
    color: $warning;
    text-style: bold;
}

.tool-result-ok {
    color: $success;
}

.tool-result-error {
    color: $error;
}

/* ── Thinking widget ────────────────────────────────────────── */
.thinking-widget {
    background: $surface;
    border-left: dashed $primary-darken-2;
    padding: 0 1;
    margin: 0 0 0 2;
    color: $text-muted;
    text-style: italic;
}

/* ── Input area ─────────────────────────────────────────────── */
#input-area {
    height: auto;
    min-height: 3;
    background: $surface;
    border-top: solid $primary-darken-2;
    layout: horizontal;
    padding: 0 1;
    align: left middle;
}

#prompt-input {
    width: 1fr;
    background: $background;
    border: solid $primary-darken-2;
    padding: 0 1;
}

#prompt-input:focus {
    border: solid $primary;
}

#send-button {
    width: auto;
    min-width: 6;
    margin-left: 1;
    background: $primary;
    color: $text;
}

/* ── Status bar ─────────────────────────────────────────────── */
#statusbar {
    height: 1;
    background: $primary-darken-2;
    color: $text-muted;
    content-align: left middle;
    padding: 0 1;
}

/* ── Spinner / in-progress ──────────────────────────────────── */
.spinner-message {
    color: $warning;
    text-style: bold;
}
"""

# ---------------------------------------------------------------------------
# Named colors (for use in Python code, not CSS)
# ---------------------------------------------------------------------------

COLOR_USER = "cyan"
COLOR_ASSISTANT = "green"
COLOR_TOOL = "yellow"
COLOR_THINKING = "dim"
COLOR_ERROR = "red"
COLOR_STATUS = "dim"
