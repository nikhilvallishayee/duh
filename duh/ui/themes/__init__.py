"""D.U.H. TUI theme CSS files (ADR-073 Wave 3 #10).

Each ``*.tcss`` file in this directory corresponds to a registered theme
in :mod:`duh.ui.theme_manager`.  The CSS files override Textual CSS
variables (``$primary``, ``$surface``, etc.) and widget-specific classes
so that switching themes in the TUI produces a visibly different look.

The actual colour palette is driven by Textual's built-in theme registry
(see ``textual.theme.BUILTIN_THEMES``) — the CSS files exist so power
users can tweak widget styling per theme without rebuilding D.U.H.
"""
