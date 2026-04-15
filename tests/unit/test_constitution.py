"""Tests for D.U.H. constitution — the single source of truth for model instructions."""

from __future__ import annotations

from pathlib import Path

from duh.constitution import (
    AGENT_OVERLAYS,
    BRIEF,
    IDENTITY,
    PRINCIPLES,
    SAFETY,
    STYLE,
    TOOL_GUIDANCE,
    ConstitutionConfig,
    build_system_prompt,
    export_default_constitution,
)


def test_default_prompt_contains_identity() -> None:
    prompt = build_system_prompt()
    assert "D.U.H." in prompt
    assert "Universal Harness" in prompt


def test_default_prompt_contains_principles() -> None:
    prompt = build_system_prompt()
    assert "Execute, don't deliberate" in prompt
    assert "Read before writing" in prompt
    assert "Test what you build" in prompt


def test_default_prompt_contains_safety() -> None:
    prompt = build_system_prompt()
    assert "Security is non-negotiable" in prompt
    assert "Never commit secrets" in prompt


def test_default_prompt_contains_tool_guidance() -> None:
    prompt = build_system_prompt()
    assert "Read before Edit" in prompt
    assert "Verify after Write" in prompt


def test_default_prompt_contains_style() -> None:
    prompt = build_system_prompt()
    assert "concise" in prompt.lower()


def test_brief_mode_appends_brief() -> None:
    cfg = ConstitutionConfig(brief=True)
    prompt = build_system_prompt(cfg)
    assert "Maximum 3 sentences" in prompt


def test_non_brief_excludes_brief() -> None:
    prompt = build_system_prompt()
    assert "Maximum 3 sentences" not in prompt


def test_coder_agent_overlay() -> None:
    cfg = ConstitutionConfig(agent_type="coder")
    prompt = build_system_prompt(cfg)
    assert "Agent Role: Coder" in prompt
    assert "well-tested code" in prompt


def test_researcher_agent_overlay() -> None:
    cfg = ConstitutionConfig(agent_type="researcher")
    prompt = build_system_prompt(cfg)
    assert "Agent Role: Researcher" in prompt
    assert "Do not modify files" in prompt


def test_planner_agent_overlay() -> None:
    cfg = ConstitutionConfig(agent_type="planner")
    prompt = build_system_prompt(cfg)
    assert "Agent Role: Planner" in prompt
    assert "Do not implement" in prompt


def test_reviewer_agent_overlay() -> None:
    cfg = ConstitutionConfig(agent_type="reviewer")
    prompt = build_system_prompt(cfg)
    assert "Agent Role: Reviewer" in prompt
    assert "bugs and security" in prompt


def test_general_agent_has_no_overlay() -> None:
    cfg = ConstitutionConfig(agent_type="general")
    prompt = build_system_prompt(cfg)
    assert "Agent Role:" not in prompt


def test_all_agent_types_have_overlays() -> None:
    for agent_type in AGENT_OVERLAYS:
        cfg = ConstitutionConfig(agent_type=agent_type)
        prompt = build_system_prompt(cfg)
        assert "D.U.H." in prompt


def test_plugins_section() -> None:
    cfg = ConstitutionConfig(plugins=[
        {"name": "test-plugin", "description": "A test plugin"},
    ])
    prompt = build_system_prompt(cfg)
    assert "test-plugin" in prompt
    assert "Active Plugins" in prompt


def test_skills_section() -> None:
    cfg = ConstitutionConfig(skills=[
        {"name": "commit", "description": "Smart git commit"},
    ])
    prompt = build_system_prompt(cfg)
    assert "/commit" in prompt
    assert "Available Skills" in prompt


def test_environment_section() -> None:
    cfg = ConstitutionConfig(environment={
        "cwd": "/home/user/project",
        "platform": "linux",
        "shell": "bash",
        "python_version": "3.12",
        "git_branch": "main",
        "git_status": "clean",
    })
    prompt = build_system_prompt(cfg)
    assert "/home/user/project" in prompt
    assert "linux" in prompt


def test_memory_section() -> None:
    cfg = ConstitutionConfig(memory="User prefers TDD. Project uses FastAPI.")
    prompt = build_system_prompt(cfg)
    assert "User prefers TDD" in prompt
    assert "Persistent Memory" in prompt


def test_extra_sections() -> None:
    cfg = ConstitutionConfig(extra_sections=["## Custom Rule\nAlways use type hints."])
    prompt = build_system_prompt(cfg)
    assert "Always use type hints" in prompt


def test_custom_identity_overrides() -> None:
    cfg = ConstitutionConfig(custom_identity="You are Bob, a Python expert.")
    prompt = build_system_prompt(cfg)
    assert "You are Bob" in prompt
    assert "Universal Harness" not in prompt


def test_override_file(tmp_path: Path) -> None:
    override = tmp_path / "my-prompt.md"
    override.write_text("You are a custom agent. Do custom things.")
    cfg = ConstitutionConfig(override_file=override)
    prompt = build_system_prompt(cfg)
    assert prompt == "You are a custom agent. Do custom things."
    assert "D.U.H." not in prompt


def test_export_default_constitution(tmp_path: Path) -> None:
    out = tmp_path / "constitution.md"
    export_default_constitution(out)
    content = out.read_text()
    assert "D.U.H." in content
    assert "Execute, don't deliberate" in content
    assert len(content) > 500


def test_none_config_uses_defaults() -> None:
    prompt = build_system_prompt(None)
    assert "D.U.H." in prompt


def test_no_hardcoded_prompts_in_runner() -> None:
    """The runner should import from constitution, not define its own prompts."""
    import duh.cli.runner as runner
    # SYSTEM_PROMPT should come from constitution
    assert "D.U.H." in runner.SYSTEM_PROMPT
    assert "Execute, don't deliberate" in runner.SYSTEM_PROMPT


def test_agent_prompts_come_from_constitution() -> None:
    """Agent prompts should be built from constitution, not hardcoded."""
    from duh.agents import AGENT_PROMPTS
    for agent_type, prompt in AGENT_PROMPTS.items():
        assert "D.U.H." in prompt, f"{agent_type} prompt missing D.U.H. identity"
