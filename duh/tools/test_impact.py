"""TestImpactTool — analyze which tests are affected by changed files.

Given a set of changed files (or auto-detected from git diff), finds test
files that are likely affected via:
  1. Import scanning: grep for 'from <module>' or 'import <module>' in tests/
  2. Naming convention: src/foo.py -> tests/test_foo.py, tests/unit/test_foo.py

Returns a list of suggested test files and a ready-to-run pytest command.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult


def _module_names_from_path(filepath: str) -> list[str]:
    """Extract plausible module names from a file path.

    Given 'duh/tools/bash.py', returns ['bash', 'duh.tools.bash', 'tools.bash'].
    Given 'src/utils/helper.py', returns ['helper', 'src.utils.helper', 'utils.helper'].
    """
    p = Path(filepath)
    if p.suffix != ".py" or p.name == "__init__.py":
        return []

    stem = p.stem  # 'bash'
    # Build dotted module paths of increasing length
    parts = list(p.with_suffix("").parts)
    names = [stem]
    for i in range(len(parts) - 2, -1, -1):
        dotted = ".".join(parts[i:])
        if dotted not in names:
            names.append(dotted)
    return names


def _find_test_files_by_convention(
    changed_file: str, project_root: Path,
) -> list[str]:
    """Find test files matching naming convention: test_<stem>.py."""
    stem = Path(changed_file).stem
    if stem.startswith("test_"):
        return []  # already a test file

    target = f"test_{stem}.py"
    matches: list[str] = []
    tests_dir = project_root / "tests"
    if tests_dir.is_dir():
        for p in tests_dir.rglob(target):
            matches.append(str(p.relative_to(project_root)))
    return sorted(set(matches))


def _find_test_files_by_imports(
    module_names: list[str], project_root: Path,
) -> list[str]:
    """Find test files that import any of the given module names."""
    if not module_names:
        return []

    tests_dir = project_root / "tests"
    if not tests_dir.is_dir():
        return []

    # Build patterns to match import statements
    patterns: list[re.Pattern[str]] = []
    for mod in module_names:
        escaped = re.escape(mod)
        patterns.append(re.compile(
            rf"(?:^|\s)(?:from\s+(?:\S+\.)?{escaped}(?:\s|\.)|import\s+(?:\S+\.)?{escaped}(?:\s|,|$))"
        ))

    matches: list[str] = []
    for test_file in tests_dir.rglob("test_*.py"):
        try:
            text = test_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pat in patterns:
            if pat.search(text):
                matches.append(str(test_file.relative_to(project_root)))
                break

    return sorted(set(matches))


async def _git_changed_files(cwd: str) -> list[str]:
    """Get changed files from git diff --name-only (staged + unstaged + untracked .py)."""
    files: set[str] = set()

    # Unstaged + staged changes
    for cmd in [
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "diff", "HEAD~1", "--name-only"],
    ]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0 and stdout:
                for line in stdout.decode("utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and line.endswith(".py"):
                        files.add(line)
        except Exception:
            continue

    return sorted(files)


class TestImpactTool:
    """Analyze which test files are affected by changed source files."""

    name = "TestImpact"
    description = (
        "Analyze which test files are likely affected by changed source files. "
        "Auto-detects changes from git diff or accepts an explicit file list. "
        "Returns suggested test files and a ready-to-run pytest command."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "changed_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of changed file paths (relative to project root). "
                    "If omitted, auto-detects from git diff."
                ),
            },
        },
        "required": [],
    }

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        project_root = Path(context.cwd or ".")

        changed_files = input.get("changed_files")
        auto_detected = False

        if not changed_files:
            changed_files = await _git_changed_files(str(project_root))
            auto_detected = True

        if not changed_files:
            return ToolResult(
                output="No changed files detected.",
                metadata={"auto_detected": auto_detected, "test_files": []},
            )

        # Filter to .py files only
        py_files = [f for f in changed_files if f.endswith(".py")]
        if not py_files:
            return ToolResult(
                output="No Python files in the changed file list.",
                metadata={"auto_detected": auto_detected, "test_files": []},
            )

        # Separate test files from source files
        test_files_changed: list[str] = []
        source_files: list[str] = []
        for f in py_files:
            basename = Path(f).name
            if basename.startswith("test_") or "/test_" in f:
                test_files_changed.append(f)
            else:
                source_files.append(f)

        affected_tests: set[str] = set()

        # Changed test files are always included
        affected_tests.update(test_files_changed)

        # For each source file, find affected tests
        for src in source_files:
            # By naming convention
            convention_matches = _find_test_files_by_convention(src, project_root)
            affected_tests.update(convention_matches)

            # By import scanning
            mod_names = _module_names_from_path(src)
            import_matches = _find_test_files_by_imports(mod_names, project_root)
            affected_tests.update(import_matches)

        sorted_tests = sorted(affected_tests)

        # Build output
        lines: list[str] = []
        if auto_detected:
            lines.append(f"Auto-detected {len(py_files)} changed Python file(s):")
            for f in py_files:
                lines.append(f"  {f}")
            lines.append("")

        if sorted_tests:
            lines.append(f"Affected test file(s) ({len(sorted_tests)}):")
            for t in sorted_tests:
                lines.append(f"  {t}")
            lines.append("")
            cmd = f"pytest {' '.join(sorted_tests)}"
            lines.append(f"Run command:\n  {cmd}")
        else:
            lines.append("No affected test files found for the changed source files.")

        return ToolResult(
            output="\n".join(lines),
            metadata={
                "auto_detected": auto_detected,
                "changed_files": py_files,
                "source_files": source_files,
                "test_files": sorted_tests,
                "command": f"pytest {' '.join(sorted_tests)}" if sorted_tests else "",
            },
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext,
    ) -> dict[str, Any]:
        return {"allowed": True}
