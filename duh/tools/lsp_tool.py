"""LSPTool — best-effort Language Server Protocol queries via static analysis.

Provides go-to-definition, find-references, hover info, and symbol listing
without requiring a running LSP server.  Uses ast.parse for Python files
and regex-based heuristics for other languages.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_file(path: Path) -> str | None:
    """Read a file, returning None on failure."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _is_python(path: Path) -> bool:
    return path.suffix in (".py", ".pyi")


# ---------------------------------------------------------------------------
# Python AST helpers
# ---------------------------------------------------------------------------

def _python_symbols(source: str) -> list[dict[str, Any]]:
    """Extract top-level symbols from Python source using ast."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    symbols: list[dict[str, Any]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            sig = _function_signature(node)
            doc = ast.get_docstring(node) or ""
            symbols.append({
                "name": node.name,
                "kind": "function",
                "line": node.lineno,
                "signature": sig,
                "docstring": doc,
            })
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node) or ""
            symbols.append({
                "name": node.name,
                "kind": "class",
                "line": node.lineno,
                "signature": f"class {node.name}",
                "docstring": doc,
            })
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = _assign_names(node)
            for n in names:
                symbols.append({
                    "name": n,
                    "kind": "variable",
                    "line": node.lineno,
                    "signature": n,
                    "docstring": "",
                })
    return symbols


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Reconstruct a function signature string."""
    args = node.args
    parts: list[str] = []

    # positional args
    for i, arg in enumerate(args.args):
        name = arg.arg
        ann = ""
        if arg.annotation:
            ann = f": {ast.unparse(arg.annotation)}"
        # check for default
        defaults_offset = len(args.args) - len(args.defaults)
        if i >= defaults_offset:
            default = ast.unparse(args.defaults[i - defaults_offset])
            parts.append(f"{name}{ann} = {default}")
        else:
            parts.append(f"{name}{ann}")

    # *args
    if args.vararg:
        va = args.vararg
        ann = f": {ast.unparse(va.annotation)}" if va.annotation else ""
        parts.append(f"*{va.arg}{ann}")

    # **kwargs
    if args.kwarg:
        kw = args.kwarg
        ann = f": {ast.unparse(kw.annotation)}" if kw.annotation else ""
        parts.append(f"**{kw.arg}{ann}")

    ret = ""
    if node.returns:
        ret = f" -> {ast.unparse(node.returns)}"

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(parts)}){ret}"


def _assign_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """Extract variable names from an assignment."""
    if isinstance(node, ast.AnnAssign) and node.target:
        if isinstance(node.target, ast.Name):
            return [node.target.id]
        return []

    names: list[str] = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
    return names


# ---------------------------------------------------------------------------
# Regex-based fallback for non-Python files
# ---------------------------------------------------------------------------

# Patterns that match common definition sites across languages
_DEF_PATTERNS: list[re.Pattern[str]] = [
    # function / method definitions
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|def|fn|func)\s+(\w+)"),
    # class / struct / interface / enum / type
    re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?(?:class|struct|interface|enum|type)\s+(\w+)"),
    # const / let / var assignments (JS/TS)
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)"),
    # Go-style top-level func
    re.compile(r"^func\s+(?:\([^)]*\)\s+)?(\w+)"),
    # Rust pub fn / fn
    re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)"),
]


def _regex_symbols(source: str) -> list[dict[str, Any]]:
    """Extract top-level symbols using regex (language-agnostic fallback)."""
    symbols: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lineno, line in enumerate(source.splitlines(), start=1):
        for pat in _DEF_PATTERNS:
            m = pat.match(line)
            if m:
                name = m.group(1)
                if name not in seen:
                    seen.add(name)
                    symbols.append({
                        "name": name,
                        "kind": "symbol",
                        "line": lineno,
                        "signature": line.strip(),
                        "docstring": "",
                    })
    return symbols


# ---------------------------------------------------------------------------
# Core actions
# ---------------------------------------------------------------------------

def _find_symbol_at(source: str, line: int, character: int) -> str | None:
    """Extract the identifier at the given position."""
    lines = source.splitlines()
    if line < 1 or line > len(lines):
        return None
    text = lines[line - 1]
    if character < 0 or character >= len(text):
        # Try to grab the whole line's last word as fallback
        if character >= len(text) and text.strip():
            character = len(text) - 1
        else:
            return None

    # Expand from character position to find the full identifier
    start = character
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        start -= 1
    end = character
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    word = text[start:end]
    return word if word else None


def _action_definition(
    file_path: Path, source: str, symbol: str
) -> ToolResult:
    """Find where *symbol* is defined — class, function, or variable."""
    # For Python files, use AST
    if _is_python(file_path):
        syms = _python_symbols(source)
    else:
        syms = _regex_symbols(source)

    matches = [s for s in syms if s["name"] == symbol]

    if not matches:
        # Fallback: grep through the file for common definition patterns
        pattern = re.compile(
            rf"(?:def|class|function|fn|func|const|let|var|type|struct|interface|enum)\s+{re.escape(symbol)}\b"
        )
        for lineno, line in enumerate(source.splitlines(), start=1):
            if pattern.search(line):
                matches.append({
                    "name": symbol,
                    "kind": "symbol",
                    "line": lineno,
                    "signature": line.strip(),
                })

    if not matches:
        return ToolResult(
            output=f"No definition found for '{symbol}' in {file_path}",
            metadata={"found": False},
        )

    lines: list[str] = []
    for m in matches:
        lines.append(f"{file_path}:{m['line']} — {m.get('signature', m['name'])}")

    return ToolResult(
        output="\n".join(lines),
        metadata={"found": True, "count": len(matches)},
    )


def _action_references(
    file_path: Path, source: str, symbol: str
) -> ToolResult:
    """Find all lines where *symbol* appears as a whole word."""
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    hits: list[str] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        if pattern.search(line):
            hits.append(f"{file_path}:{lineno}: {line.rstrip()}")

    if not hits:
        return ToolResult(
            output=f"No references to '{symbol}' found in {file_path}",
            metadata={"count": 0},
        )

    return ToolResult(
        output="\n".join(hits),
        metadata={"count": len(hits)},
    )


def _action_hover(
    file_path: Path, source: str, symbol: str
) -> ToolResult:
    """Show signature and docstring for *symbol*."""
    if _is_python(file_path):
        syms = _python_symbols(source)
    else:
        syms = _regex_symbols(source)

    for s in syms:
        if s["name"] == symbol:
            parts: list[str] = [s["signature"]]
            if s.get("docstring"):
                parts.append("")
                parts.append(s["docstring"])
            return ToolResult(
                output="\n".join(parts),
                metadata={"found": True, "kind": s["kind"]},
            )

    return ToolResult(
        output=f"No hover info for '{symbol}' in {file_path}",
        metadata={"found": False},
    )


def _action_symbols(file_path: Path, source: str) -> ToolResult:
    """List all top-level symbols in the file."""
    if _is_python(file_path):
        syms = _python_symbols(source)
    else:
        syms = _regex_symbols(source)

    if not syms:
        return ToolResult(
            output=f"No symbols found in {file_path}",
            metadata={"count": 0},
        )

    lines: list[str] = []
    for s in syms:
        lines.append(f"  {s['line']:>4}  {s['kind']:<10}  {s['name']}")
    header = f"Symbols in {file_path} ({len(syms)} total):\n"

    return ToolResult(
        output=header + "\n".join(lines),
        metadata={"count": len(syms)},
    )


# ---------------------------------------------------------------------------
# LSPTool
# ---------------------------------------------------------------------------

class LSPTool:
    """Query language-server-style info via static analysis.

    Actions:
        definition  — find where a symbol is defined (class/function/variable)
        references  — find all usages of a symbol
        hover       — show function signature or docstring
        symbols     — list all top-level symbols in a file
    """

    name = "LSP"
    description = (
        "Query language server for go-to-definition, find-references, "
        "hover info, and symbol listing.  Uses static analysis (ast/regex) "
        "rather than a running LSP server."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["definition", "references", "hover", "symbols"],
                "description": "The LSP action to perform.",
            },
            "file": {
                "type": "string",
                "description": "Path to the file to analyze.",
            },
            "line": {
                "type": "integer",
                "description": "1-based line number of the symbol.",
                "minimum": 1,
            },
            "character": {
                "type": "integer",
                "description": "0-based character offset on the line.",
                "minimum": 0,
            },
        },
        "required": ["action", "file"],
    }

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        action = input.get("action", "")
        file_str = input.get("file", "")
        line = input.get("line", 0)
        character = input.get("character", 0)

        if action not in ("definition", "references", "hover", "symbols"):
            return ToolResult(
                output=f"Unknown action: {action!r}. Use one of: definition, references, hover, symbols",
                is_error=True,
            )

        if not file_str:
            return ToolResult(output="'file' is required", is_error=True)

        file_path = Path(file_str)
        if not file_path.is_absolute():
            file_path = Path(context.cwd) / file_path

        if not file_path.exists():
            return ToolResult(
                output=f"File not found: {file_path}", is_error=True
            )
        if not file_path.is_file():
            return ToolResult(
                output=f"Not a file: {file_path}", is_error=True
            )

        source = _read_file(file_path)
        if source is None:
            return ToolResult(
                output=f"Could not read file: {file_path}", is_error=True
            )

        # For "symbols" we don't need a symbol name
        if action == "symbols":
            return _action_symbols(file_path, source)

        # For other actions, resolve the symbol at (line, character)
        symbol = _find_symbol_at(source, line, character)
        if not symbol:
            return ToolResult(
                output=f"No identifier found at line {line}, character {character}",
                is_error=True,
            )

        if action == "definition":
            return _action_definition(file_path, source, symbol)
        elif action == "references":
            return _action_references(file_path, source, symbol)
        elif action == "hover":
            return _action_hover(file_path, source, symbol)

        # Unreachable, but satisfy exhaustiveness
        return ToolResult(output="Unknown action", is_error=True)  # pragma: no cover

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
