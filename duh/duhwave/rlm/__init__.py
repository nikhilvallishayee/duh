"""RLM context engine — ADR-028.

Treats large prompts as variables inside a sandboxed Python REPL
rather than as text fed to the model. The agent receives REPL-
manipulation tools (Peek / Search / Slice / Recurse / Synthesize)
instead of the raw text.

Cited literature: Zhang, Kraska, Khattab — *Recursive Language
Models* (arXiv 2512.24601, Jan 2026).
"""
from __future__ import annotations

from duh.duhwave.rlm.handles import Handle, HandleStore
from duh.duhwave.rlm.policy import ContextMode, choose_context_mode
from duh.duhwave.rlm.repl import RecurseRunner, RLMRepl, RLMReplError

__all__ = [
    "Handle",
    "HandleStore",
    "RLMRepl",
    "RLMReplError",
    "RecurseRunner",
    "ContextMode",
    "choose_context_mode",
]
