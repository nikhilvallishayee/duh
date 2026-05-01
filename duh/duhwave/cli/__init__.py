"""``duh wave`` control-plane CLI — ADR-032 §C.

Subcommands wire to concrete handlers in :mod:`duh.duhwave.cli.commands`.
The host daemon (started by ``duh wave start``) lives in
:mod:`duh.duhwave.cli.daemon` and owns the persistent process; CLI
subcommands talk to it over a Unix-domain socket at
``~/.duh/waves/host.sock``.
"""
from __future__ import annotations

from duh.duhwave.cli.entrypoint import main

__all__ = ["main"]
