"""Consistency harness for benchmark 3.

Runs against each agent's worktree AFTER the agent finishes. Emits
consistency.json with: symbol_existence, signature_match, import_check,
coverage, overall pass_rate.

Not shown to agents during the run.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path


def extract_inline_identifiers(md_text: str) -> list[str]:
    """Find `duh.something.like.this` in backtick code spans."""
    out = []
    for match in re.finditer(r"`(duh(?:\.[A-Za-z_][A-Za-z0-9_]*)+)`", md_text):
        out.append(match.group(1))
    return out


def extract_signatures(md_text: str) -> list[tuple[str, str]]:
    """Find ```def name(...):``` style signatures in code fences.

    Returns list of (name, signature_source).
    """
    out = []
    # Match either markdown-fenced python code blocks OR `def foo(...)` inline.
    for block_match in re.finditer(r"```(?:python)?\n(.+?)```", md_text, re.S):
        block = block_match.group(1)
        for sig in re.finditer(r"def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", block):
            out.append((sig.group(1), sig.group(0)))
        for sig in re.finditer(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[\(:]", block):
            out.append((sig.group(1), sig.group(0)))
    return out


def extract_imports(md_text: str) -> list[str]:
    """Find `from duh.X import Y` and `import duh.X` in code blocks."""
    out = []
    for block_match in re.finditer(r"```(?:python)?\n(.+?)```", md_text, re.S):
        block = block_match.group(1)
        for m in re.finditer(r"^\s*from\s+(duh[\w\.]*)\s+import\s+([\w,\s]+)", block, re.M):
            mod = m.group(1)
            for sym in (s.strip() for s in m.group(2).split(",")):
                if sym and sym != "*":
                    out.append(f"{mod}.{sym}")
        for m in re.finditer(r"^\s*import\s+(duh[\w\.]*)", block, re.M):
            out.append(m.group(1))
    return out


def resolve(dotted: str, duh_root: Path) -> bool:
    """Check whether `duh.something` resolves in the pinned source tree."""
    parts = dotted.split(".")
    if parts[0] != "duh":
        return False
    # Start from duh_root, walk until we hit a .py file then attribute-match.
    current = duh_root
    mod_depth = 0
    for i in range(1, len(parts)):
        if (current / parts[i]).is_dir():
            current = current / parts[i]
            mod_depth = i
            continue
        if (current / f"{parts[i]}.py").is_file():
            mod_depth = i
            mod_file = current / f"{parts[i]}.py"
            # Attribute walk inside the file.
            try:
                tree = ast.parse(mod_file.read_text())
            except Exception:
                return False
            defined = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined.add(node.name)
                elif isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            defined.add(tgt.id)
            remaining = parts[mod_depth + 1:]
            if not remaining:
                return True
            # Only support one level of attribute beyond the module for now.
            return remaining[0] in defined
        return False
    # Fell off without hitting a .py → it's a package reference only.
    return current.is_dir()


def public_symbols_in(duh_root: Path, sub: str) -> set[str]:
    """Collect public classes / functions / vars in duh/<sub>/*.py."""
    out = set()
    target = duh_root / sub
    if not target.is_dir():
        return out
    for py in target.rglob("*.py"):
        if py.name.startswith("_"):
            continue
        try:
            tree = ast.parse(py.read_text())
        except Exception:
            continue
        for node in tree.body:  # module-level only
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    out.add(node.name)
    return out


def main():
    if len(sys.argv) != 3:
        print("usage: check.py <worktree> <output_path>", file=sys.stderr)
        sys.exit(2)
    wt = Path(sys.argv[1]).resolve()
    out_path = Path(sys.argv[2])

    docs_dir = wt / "docs-new"
    duh_root = wt / "duh"

    if not docs_dir.is_dir():
        out_path.write_text(json.dumps({
            "error": "no docs-new/ directory produced",
            "pass_rate": 0.0,
        }, indent=2))
        return

    md_text = "\n".join(p.read_text(errors="replace") for p in docs_dir.rglob("*.md"))

    # 1. Symbol existence (inline identifiers).
    symbols = extract_inline_identifiers(md_text)
    sym_total = len(symbols)
    sym_ok = sum(1 for s in symbols if resolve(s, duh_root))

    # 2. Signature names — check that each `def name` cited by the docs
    #    actually exists somewhere in duh/. Coarse but cheap.
    defined = public_symbols_in(duh_root, "") | public_symbols_in(duh_root, "kernel") | public_symbols_in(duh_root, "ports") | public_symbols_in(duh_root, "adapters") | public_symbols_in(duh_root, "tools") | public_symbols_in(duh_root, "providers")
    sigs = extract_signatures(md_text)
    sig_total = len(sigs)
    sig_ok = sum(1 for name, _ in sigs if name in defined)

    # 3. Import existence — run a Python syntax/import check for each.
    imports = extract_imports(md_text)
    imp_total = len(imports)
    imp_ok = 0
    for i in imports:
        # Lightweight — just check the path resolves.
        if resolve(i, duh_root):
            imp_ok += 1

    # 4. Coverage — what fraction of public symbols in kernel+ports are
    #    mentioned in any doc at all?
    public = public_symbols_in(duh_root, "kernel") | public_symbols_in(duh_root, "ports")
    mentioned = {s for s in public if s and re.search(rf"\b{re.escape(s)}\b", md_text)}
    cov_total = len(public)
    cov_ok = len(mentioned)

    overall_total = sym_total + sig_total + imp_total + cov_total
    overall_ok = sym_ok + sig_ok + imp_ok + cov_ok
    pass_rate = (overall_ok / overall_total) if overall_total else 0.0

    result = {
        "symbol_existence": {"ok": sym_ok, "total": sym_total,
                             "rate": (sym_ok / sym_total) if sym_total else 0.0},
        "signature_match": {"ok": sig_ok, "total": sig_total,
                            "rate": (sig_ok / sig_total) if sig_total else 0.0},
        "import_check": {"ok": imp_ok, "total": imp_total,
                         "rate": (imp_ok / imp_total) if imp_total else 0.0},
        "coverage": {"ok": cov_ok, "total": cov_total,
                     "rate": (cov_ok / cov_total) if cov_total else 0.0},
        "overall_ok": overall_ok,
        "overall_total": overall_total,
        "pass_rate": pass_rate,
        "docs_files": [str(p.relative_to(wt)) for p in docs_dir.rglob("*.md")],
    }
    out_path.write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
