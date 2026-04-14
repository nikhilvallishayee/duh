"""duh-sandbox-lint — CVE-2025-59532 sandbox bypass defense.

AST-walk the target tree looking for dynamic string construction (f-string,
.format(), +concat) that flows into `.sb` file writes or Seatbelt/Landlock
profile generators.
"""

from __future__ import annotations

import ast
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier


_SINK_FUNCTIONS = {"generate_profile", "add_rule"}


class _SandboxVisitor(ast.NodeVisitor):
    def __init__(self, source: str, path: Path) -> None:
        self.source = source
        self.path = path
        self.findings: list[Finding] = []
        # Track tainted variable names (assigned from dynamic strings)
        self._tainted: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        # Track variables assigned from dynamic strings.
        if self._is_dynamic_string(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._tainted.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Detect `fh.write(...)` where argument is dynamic or tainted.
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "write"
            and node.args
            and self._is_tainted_or_dynamic(node.args[0])
            and self._context_writes_sb(node)
        ):
            self._emit(node, "write() with dynamic string into .sb profile")
        # Detect sandbox API sinks.
        if isinstance(node.func, ast.Attribute) and node.func.attr in _SINK_FUNCTIONS:
            for arg in node.args:
                if self._is_tainted_or_dynamic(arg):
                    self._emit(node, f"{node.func.attr}() with dynamic string")
        self.generic_visit(node)

    def _is_tainted_or_dynamic(self, node: ast.AST) -> bool:
        if self._is_dynamic_string(node):
            return True
        if isinstance(node, ast.Name) and node.id in self._tainted:
            return True
        return False

    def _is_dynamic_string(self, node: ast.AST) -> bool:
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "format":
                return True
        return False

    def _context_writes_sb(self, node: ast.Call) -> bool:
        # Heuristic: any .sb literal in the file.
        return ".sb" in self.source

    def _emit(self, node: ast.AST, msg: str) -> None:
        self.findings.append(
            Finding.create(
                id="DUH-SANDBOX-UNTRUSTED",
                aliases=("CVE-2025-59532",),
                scanner="duh-sandbox-lint",
                severity=Severity.HIGH,
                message=msg,
                description=(
                    "Untrusted string flows into sandbox profile generation. "
                    "Tag upstream with UntrustedStr (ADR-054)."
                ),
                location=Location(
                    file=str(self.path),
                    line_start=getattr(node, "lineno", 0),
                    line_end=getattr(node, "end_lineno", getattr(node, "lineno", 0)),
                    snippet="",
                ),
            )
        )


class SandboxLintScanner(InProcessScanner):
    name = "duh-sandbox-lint"
    tier: Tier = "minimal"
    default_severity = (Severity.HIGH,)
    _module_name = "json"

    async def _scan_impl(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None,
    ) -> list[Finding]:
        _EXCLUDE = {".venv", "venv", ".tox", "node_modules", "__pycache__", ".git"}
        if changed_files is not None:
            files = list(changed_files)
        else:
            files = [
                p for p in target.rglob("*.py")
                if not any(part in _EXCLUDE for part in p.parts)
            ]
        out: list[Finding] = []
        for path in files:
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue
            visitor = _SandboxVisitor(source, path)
            visitor.visit(tree)
            out.extend(visitor.findings)
        return out
