"""Tests for ``duh.duhwave.spec.parser.parse_swarm`` — ADR-032 §A.

Structural validation only — the parser does not yet evaluate
``${VAR}`` interpolation. Each test calls ``parse_swarm`` against a
real TOML file under ``tmp_path`` so the tomllib path is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from duh.duhwave.spec.parser import (
    BudgetSpec,
    SwarmSpec,
    SwarmSpecError,
    parse_swarm,
)


def _write(path: Path, body: str) -> Path:
    path.write_text(body.strip() + "\n")
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestMinimalSpec:
    def test_minimal_valid_spec_parses(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
[swarm]
name = "tiny"
version = "0.1.0"
description = "smallest valid swarm"
format_version = 1

[[agents]]
id = "solo"
role = "researcher"
model = "sonnet"
""",
        )
        spec = parse_swarm(spec_path)
        assert isinstance(spec, SwarmSpec)
        assert spec.name == "tiny"
        assert spec.version == "0.1.0"
        assert spec.format_version == 1
        assert len(spec.agents) == 1
        assert spec.agents[0].id == "solo"
        assert spec.agents[0].role == "researcher"
        assert spec.agents[0].model == "sonnet"
        # Empty defaults for optional sections.
        assert spec.triggers == ()
        assert spec.edges == ()
        assert spec.secrets == ()


# ---------------------------------------------------------------------------
# Structural errors
# ---------------------------------------------------------------------------


class TestStructuralErrors:
    def test_missing_swarm_section_raises(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
[[agents]]
id = "a"
role = "researcher"
model = "sonnet"
""",
        )
        with pytest.raises(SwarmSpecError, match=r"\[swarm\]"):
            parse_swarm(spec_path)

    def test_empty_agents_raises(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
[swarm]
name = "no-agents"
version = "0.1.0"
description = ""
format_version = 1
""",
        )
        with pytest.raises(SwarmSpecError, match="no agents"):
            parse_swarm(spec_path)

    def test_trigger_targets_unknown_agent(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
[swarm]
name = "bad-trigger"
version = "0.1.0"
description = ""
format_version = 1

[[agents]]
id = "real"
role = "researcher"
model = "sonnet"

[[triggers]]
kind = "webhook"
source = "/x"
target_agent_id = "ghost"
""",
        )
        with pytest.raises(SwarmSpecError) as exc:
            parse_swarm(spec_path)
        # Helpful message identifies the missing agent id.
        assert "ghost" in str(exc.value)

    def test_edge_from_unknown_agent_raises(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
[swarm]
name = "bad-edge-from"
version = "0.1.0"
description = ""
format_version = 1

[[agents]]
id = "a"
role = "researcher"
model = "sonnet"

[[edges]]
from_agent_id = "ghost"
to_agent_id = "a"
""",
        )
        with pytest.raises(SwarmSpecError, match="ghost"):
            parse_swarm(spec_path)

    def test_edge_to_unknown_agent_raises(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
[swarm]
name = "bad-edge-to"
version = "0.1.0"
description = ""
format_version = 1

[[agents]]
id = "a"
role = "researcher"
model = "sonnet"

[[edges]]
from_agent_id = "a"
to_agent_id = "ghost"
""",
        )
        with pytest.raises(SwarmSpecError, match="ghost"):
            parse_swarm(spec_path)


# ---------------------------------------------------------------------------
# Optional sections
# ---------------------------------------------------------------------------


class TestOptionalSections:
    def test_budget_defaults_when_missing(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
[swarm]
name = "default-budget"
version = "0.1.0"
description = ""
format_version = 1

[[agents]]
id = "a"
role = "researcher"
model = "sonnet"
""",
        )
        spec = parse_swarm(spec_path)
        # Defaults from BudgetSpec dataclass.
        assert isinstance(spec.budget, BudgetSpec)
        assert spec.budget.max_tokens_per_hour == 1_000_000
        assert spec.budget.max_usd_per_day == 50.0
        assert spec.budget.max_concurrent_tasks == 4

    def test_budget_overrides_applied(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
[swarm]
name = "tight-budget"
version = "0.1.0"
description = ""
format_version = 1

[[agents]]
id = "a"
role = "researcher"
model = "sonnet"

[budget]
max_tokens_per_hour = 250000
max_usd_per_day = 5.0
max_concurrent_tasks = 1
""",
        )
        spec = parse_swarm(spec_path)
        assert spec.budget.max_tokens_per_hour == 250_000
        assert spec.budget.max_usd_per_day == 5.0
        assert spec.budget.max_concurrent_tasks == 1

    def test_secrets_list_parsed(self, tmp_path: Path):
        # `secrets` is a top-level array. In TOML it must appear at
        # the document root *before* any other table headers (which
        # would otherwise scope subsequent keys under that table).
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
secrets = ["GITHUB_TOKEN", "SLACK_WEBHOOK_URL"]

[swarm]
name = "with-secrets"
version = "0.1.0"
description = ""
format_version = 1

[[agents]]
id = "a"
role = "researcher"
model = "sonnet"
""",
        )
        spec = parse_swarm(spec_path)
        assert spec.secrets == ("GITHUB_TOKEN", "SLACK_WEBHOOK_URL")

    def test_agent_tools_and_expose_lists(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "swarm.toml",
            """
[swarm]
name = "agent-tools"
version = "0.1.0"
description = ""
format_version = 1

[[agents]]
id = "a"
role = "researcher"
model = "sonnet"
tools = ["Read", "Grep"]
expose = ["search"]
system_prompt = "You research things."
""",
        )
        spec = parse_swarm(spec_path)
        a = spec.agents[0]
        assert a.tools == ("Read", "Grep")
        assert a.expose == ("search",)
        assert a.system_prompt == "You research things."
