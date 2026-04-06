"""Adapters — concrete implementations of ports.

Each adapter wraps an external SDK/service and translates it into
D.U.H.'s uniform interface. The kernel never imports these directly;
they're injected via Deps.
"""
