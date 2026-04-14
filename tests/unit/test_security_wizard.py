"""Tests for wizard detection, dry-run, atomic writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from duh.security.wizard import (
    Answers,
    Detection,
    WizardResult,
    detect,
    render_plan,
    write_plan,
)


def test_detect_on_empty_project(tmp_path: Path) -> None:
    det = detect(project_root=tmp_path)
    assert det.is_python is False
    assert det.is_git_repo is False
    assert det.has_github is False


def test_detect_python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    det = detect(project_root=tmp_path)
    assert det.is_python is True


def test_detect_github_repo(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        "[remote \"origin\"]\n\turl = git@github.com:a/b.git\n"
    )
    det = detect(project_root=tmp_path)
    assert det.is_git_repo is True
    assert det.has_github is True


def test_render_plan_lists_files(tmp_path: Path) -> None:
    det = Detection(
        is_python=True, is_git_repo=True, has_github=True,
        has_docker=False, has_go=False,
        available_scanners=("ruff-sec", "pip-audit"),
    )
    answers = Answers(
        mode="strict", enable_runtime=True, extended_scanners=(),
        generate_ci=True, ci_template="standard",
        install_git_hook=True, generate_security_md=True,
        import_legacy=False, pin_scanner_versions=True,
    )
    plan = render_plan(detection=det, answers=answers, project_root=tmp_path)
    paths = [str(p.path) for p in plan]
    assert any("security.json" in p for p in paths)
    assert any("security.yml" in p for p in paths)
    assert any("SECURITY.md" in p for p in paths)


def test_dry_run_does_not_touch_disk(tmp_path: Path) -> None:
    det = Detection(
        is_python=True, is_git_repo=True, has_github=True,
        has_docker=False, has_go=False,
        available_scanners=("ruff-sec",),
    )
    answers = Answers(
        mode="advisory", enable_runtime=False, extended_scanners=(),
        generate_ci=False, ci_template="minimal",
        install_git_hook=False, generate_security_md=False,
        import_legacy=False, pin_scanner_versions=False,
    )
    plan = render_plan(detection=det, answers=answers, project_root=tmp_path)
    result = write_plan(plan, dry_run=True)
    assert result.dry_run is True
    assert not (tmp_path / ".duh" / "security.json").exists()


from duh.security.wizard import import_legacy_configs


def test_import_bandit_config(tmp_path: Path) -> None:
    (tmp_path / ".bandit").write_text(
        "[bandit]\nskips = B602,B607\n"
    )
    exceptions = import_legacy_configs(project_root=tmp_path)
    ids = {e.id for e in exceptions}
    assert "B602" in ids
    assert "B607" in ids


def test_import_semgrepignore(tmp_path: Path) -> None:
    (tmp_path / ".semgrepignore").write_text("# comment\ntests/\nvendor/**\n")
    exceptions = import_legacy_configs(project_root=tmp_path)
    # Converted into file_glob scopes; one entry per non-comment line
    assert len(exceptions) >= 2


def test_import_missing_files_returns_empty(tmp_path: Path) -> None:
    assert import_legacy_configs(project_root=tmp_path) == []


def test_real_run_writes_files_atomically(tmp_path: Path) -> None:
    det = Detection(
        is_python=True, is_git_repo=False, has_github=False,
        has_docker=False, has_go=False,
        available_scanners=("ruff-sec",),
    )
    answers = Answers(
        mode="strict", enable_runtime=True, extended_scanners=(),
        generate_ci=False, ci_template="minimal",
        install_git_hook=False, generate_security_md=False,
        import_legacy=False, pin_scanner_versions=True,
    )
    plan = render_plan(detection=det, answers=answers, project_root=tmp_path)
    result = write_plan(plan, dry_run=False)
    assert result.dry_run is False
    cfg_path = tmp_path / ".duh" / "security.json"
    assert cfg_path.exists()
    data = json.loads(cfg_path.read_text())
    assert data["mode"] == "strict"


def test_render_plan_emits_workflow_body_from_generator(tmp_path: Path) -> None:
    det = Detection(
        is_python=True, is_git_repo=True, has_github=True,
        has_docker=False, has_go=False,
        available_scanners=("ruff-sec",),
    )
    answers = Answers(
        mode="strict", enable_runtime=True, extended_scanners=(),
        generate_ci=True, ci_template="paranoid",
        install_git_hook=False, generate_security_md=True,
        import_legacy=False, pin_scanner_versions=True,
    )
    plan = render_plan(detection=det, answers=answers, project_root=tmp_path)
    by_path = {str(item.path): item.body for item in plan}
    wf = next(v for k, v in by_path.items() if k.endswith("security.yml"))
    assert "name: Security" in wf
    assert "scorecard:" in wf  # paranoid template
    assert "codeql:" in wf
    md = next(v for k, v in by_path.items() if k.endswith("SECURITY.md"))
    assert "# Security Policy" in md
    db = next(v for k, v in by_path.items() if k.endswith("dependabot.yml"))
    assert "version: 2" in db


def test_render_plan_skips_ci_when_generate_ci_false(tmp_path: Path) -> None:
    det = Detection(
        is_python=True, is_git_repo=True, has_github=True,
        has_docker=False, has_go=False,
        available_scanners=("ruff-sec",),
    )
    answers = Answers(
        mode="advisory", enable_runtime=False, extended_scanners=(),
        generate_ci=False, ci_template="minimal",
        install_git_hook=False, generate_security_md=False,
        import_legacy=False, pin_scanner_versions=False,
    )
    plan = render_plan(detection=det, answers=answers, project_root=tmp_path)
    paths = [str(item.path) for item in plan]
    assert not any(p.endswith("security.yml") for p in paths)
    assert not any(p.endswith("SECURITY.md") for p in paths)
