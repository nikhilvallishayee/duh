"""duh security init — interactive wizard skeleton (detection + plan + write)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Detection:
    is_python: bool
    is_git_repo: bool
    has_github: bool
    has_docker: bool
    has_go: bool
    available_scanners: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Answers:
    mode: Literal["advisory", "strict", "paranoid"]
    enable_runtime: bool
    extended_scanners: tuple[str, ...]
    generate_ci: bool
    ci_template: Literal["minimal", "standard", "paranoid"]
    install_git_hook: bool
    generate_security_md: bool
    import_legacy: bool
    pin_scanner_versions: bool


@dataclass(frozen=True, slots=True)
class PlannedFile:
    path: Path
    content: str
    mode: int = 0o644

    @property
    def body(self) -> str:
        """Alias for `content`; used by Task 5.8 tests."""
        return self.content


# Alias expected by Task 5.8 tests.
PlanItem = PlannedFile


@dataclass(frozen=True, slots=True)
class WizardResult:
    written: tuple[Path, ...]
    dry_run: bool


def detect(*, project_root: Path) -> Detection:
    is_python = (project_root / "pyproject.toml").exists()
    git_dir = project_root / ".git"
    is_git = git_dir.exists()
    has_github = False
    if is_git:
        cfg = git_dir / "config"
        if cfg.exists():
            try:
                text = cfg.read_text(encoding="utf-8")
                has_github = "github.com" in text
            except OSError:
                pass
    has_docker = shutil.which("docker") is not None
    has_go = shutil.which("go") is not None
    try:
        eps = importlib_metadata.entry_points(group="duh.security.scanners")
        available = tuple(sorted(ep.name for ep in eps))
    except Exception:
        available = ()
    return Detection(
        is_python=is_python,
        is_git_repo=is_git,
        has_github=has_github,
        has_docker=has_docker,
        has_go=has_go,
        available_scanners=available,
    )


def _security_json(answers: Answers) -> dict:
    doc: dict = {
        "version": 1,
        "mode": answers.mode,
        "scanners": {name: {"enabled": True} for name in (
            "ruff-sec", "pip-audit", "detect-secrets", "cyclonedx-sbom",
            "duh-repo", "duh-mcp-schema", "duh-mcp-pin",
            "duh-sandbox-lint", "duh-oauth-lint",
        )},
        "runtime": {"enabled": answers.enable_runtime},
        "ci": {
            "generate_github_actions": answers.generate_ci,
            "template": answers.ci_template,
        },
    }
    return doc


def render_plan(
    *,
    detection: Detection,
    answers: Answers,
    project_root: Path,
) -> list[PlannedFile]:
    from duh.security.ci_templates.github_actions import (
        WorkflowTemplate,
        generate_dependabot,
        generate_workflow,
    )
    from duh.security.ci_templates.security_md import generate as generate_security_md

    plan: list[PlannedFile] = []
    plan.append(PlannedFile(
        path=project_root / ".duh" / "security.json",
        content=json.dumps(_security_json(answers), indent=2),
    ))
    plan.append(PlannedFile(
        path=project_root / ".duh" / "security-exceptions.json",
        content=json.dumps({"version": 1, "exceptions": []}, indent=2),
    ))
    if answers.generate_ci and detection.has_github:
        plan.append(PlannedFile(
            path=project_root / ".github" / "workflows" / "security.yml",
            content=generate_workflow(
                template=WorkflowTemplate(answers.ci_template),
            ),
        ))
        plan.append(PlannedFile(
            path=project_root / ".github" / "dependabot.yml",
            content=generate_dependabot(),
        ))
    if answers.generate_security_md:
        plan.append(PlannedFile(
            path=project_root / "SECURITY.md",
            content=generate_security_md(
                project_name=project_root.name or "your-project",
                latest_version="0.1.0",
            ),
        ))
    return plan


def write_plan(plan: list[PlannedFile], *, dry_run: bool) -> WizardResult:
    if dry_run:
        return WizardResult(written=(), dry_run=True)
    written: list[Path] = []
    for pf in plan:
        pf.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = pf.path.with_suffix(pf.path.suffix + ".tmp")
        tmp.write_text(pf.content, encoding="utf-8")
        tmp.replace(pf.path)
        written.append(pf.path)
    return WizardResult(written=tuple(written), dry_run=False)


from dataclasses import dataclass as _dc


@_dc(frozen=True, slots=True)
class LegacyException:
    id: str
    source: str
    scope: dict


def import_legacy_configs(*, project_root: Path) -> list[LegacyException]:
    out: list[LegacyException] = []
    bandit = project_root / ".bandit"
    if bandit.is_file():
        for line in bandit.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("skips"):
                _, _, rhs = line.partition("=")
                for code in rhs.split(","):
                    code = code.strip()
                    if code:
                        out.append(LegacyException(id=code, source=".bandit", scope={}))
    semgrepignore = project_root / ".semgrepignore"
    if semgrepignore.is_file():
        for line in semgrepignore.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(LegacyException(
                id="SEMGREP-IGNORE", source=".semgrepignore", scope={"file_glob": line},
            ))
    return out
