"""Swarm topology DSL — ADR-032 §A.

A :class:`SwarmSpec` is a parsed, validated TOML topology describing
a single swarm: agents, triggers, edges, models, budgets.
"""
from __future__ import annotations

from duh.duhwave.spec.parser import (
    SwarmSpec,
    AgentSpec,
    TriggerSpec,
    EdgeSpec,
    BudgetSpec,
    parse_swarm,
)

__all__ = [
    "SwarmSpec",
    "AgentSpec",
    "TriggerSpec",
    "EdgeSpec",
    "BudgetSpec",
    "parse_swarm",
]
