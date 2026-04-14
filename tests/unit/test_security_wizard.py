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
