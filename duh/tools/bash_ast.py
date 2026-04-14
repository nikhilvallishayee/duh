"""Bash AST parser — structural tokenizer for shell command classification.

Tokenizes shell commands into segments by splitting on pipes (|), logical
operators (&&, ||), semicolons (;), and subshell constructs ($(...) and
backticks).  Each segment is then classified independently via the regex
patterns in bash_security.py.

The AST parser provides structural awareness that pure regex lacks:
- A dangerous command hidden after a pipe is caught.
- Safe wrapper commands (timeout, time, nice, etc.) are stripped.
- Subshell fanout is capped to prevent DoS.

Usage:
    from duh.tools.bash_ast import ast_classify
    result = ast_classify("ls && rm -rf /")
    # result == {"risk": "dangerous", "reason": "Recursive forced deletion (rm -rf)"}
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from duh.tools.bash_security import Classification

# Maximum number of segments a single command can produce.
# Prevents DoS via absurdly long compound commands.
MAX_SUBCOMMANDS: int = 50

# Wrapper commands that are safe to strip before classification.
# Each entry is (word, number of arguments to skip after the word).
# "timeout 30 <cmd>" → skip "timeout" and "30"
# "time <cmd>" → skip "time"
_WRAPPERS: dict[str, int] = {
    "timeout": -1,  # -1 = skip all flags/args until a non-flag token
    "time": 0,
    "nice": -1,
    "nohup": 0,
    "env": -1,
    "stdbuf": -1,
}


class SegmentType(str, Enum):
    """Type of a tokenized shell segment."""
    SIMPLE = "simple"
    SUBSHELL = "subshell"


@dataclass(frozen=True)
class Segment:
    """A single segment of a tokenized shell command."""
    text: str
    seg_type: SegmentType


# ---------------------------------------------------------------------------
# Comment stripping
# ---------------------------------------------------------------------------

def strip_comments(cmd: str) -> str:
    """Remove full-line comments (lines starting with optional whitespace + #).

    Does NOT strip inline comments — ``echo hi # bye`` is kept intact
    because ``#`` inside a command is ambiguous (could be a parameter).
    """
    lines = cmd.split("\n")
    result: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            result.append("")
        else:
            result.append(line)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Wrapper stripping
# ---------------------------------------------------------------------------

def strip_wrappers(cmd: str) -> str:
    """Remove safe wrapper commands from the front of *cmd*.

    Wrapper commands like ``timeout``, ``time``, ``nice``, ``nohup``,
    ``env``, and ``stdbuf`` are peeled off so the *inner* command is
    what gets classified.

    ``timeout 30 curl http://x.com``  ->  ``curl http://x.com``
    ``nice -n 10 python train.py``    ->  ``python train.py``
    ``time make build``               ->  ``make build``
    """
    if not cmd or not cmd.strip():
        return cmd

    changed = True
    while changed:
        changed = False
        parts = cmd.strip().split(None, 1)
        if not parts:
            break
        word = parts[0]
        if word not in _WRAPPERS:
            break

        skip_mode = _WRAPPERS[word]
        rest = parts[1] if len(parts) > 1 else ""

        if skip_mode == 0:
            # Just skip the wrapper word itself
            cmd = rest
            changed = True
        elif skip_mode == -1:
            # Skip all flag-like args (starting with -), numeric args,
            # and key=value args until we hit a token that looks like
            # a command name.
            tokens = rest.split()
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                if tok.startswith("-"):
                    # It's a flag. Some flags consume the next token as value.
                    # Heuristic: if it's a short flag like -n, skip next too
                    # if next token doesn't start with - and isn't a command
                    i += 1
                    if (
                        i < len(tokens)
                        and not tokens[i].startswith("-")
                        and "=" not in tok
                    ):
                        # Check if the next token is a numeric value (flag arg)
                        # or a real command.  Numeric values are consumed.
                        if tokens[i].replace(".", "").isdigit():
                            i += 1
                        # Otherwise it's probably the inner command — stop
                elif "=" in tok:
                    # key=value style (like env FOO=bar)
                    # For env, skip these; for others, stop
                    if word == "env":
                        i += 1
                    else:
                        break
                elif tok.replace(".", "").isdigit():
                    # Bare numeric argument (e.g., `timeout 30 <cmd>`)
                    i += 1
                else:
                    # Looks like the actual command
                    break
            cmd = " ".join(tokens[i:])
            changed = True

    return cmd


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# ANSI-C quoting: $'...' (must be matched before regular single quotes)
_ANSI_C_RE = re.compile(r"""\$'(?:[^'\\]|\\.)*'""")

# Regex to find quote boundaries (single, double, escaped chars)
_QUOTE_RE = re.compile(r"""(?:'[^']*'|"(?:[^"\\]|\\.)*"|\\.)""")

# Heredoc patterns: <<EOF, <<-EOF, <<'EOF', <<"EOF"
_HEREDOC_RE = re.compile(
    r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|(\w+))"
)

# Process substitution: <(...) and >(...)
_PROC_SUB_RE = re.compile(r"[<>]\(")

# Regex to split on shell operators: &&, ||, |, ;
# Order matters: && and || must be matched before | alone.
_OPERATOR_RE = re.compile(r"\s*(?:&&|\|\||\||;)\s*")


def _mask_quotes(cmd: str) -> tuple[str, str]:
    """Replace quoted strings with placeholders so operators inside quotes
    are not treated as segment separators.

    Also masks ANSI-C $'...' strings before regular quotes.

    Returns (masked_cmd, original_cmd) where masked_cmd has quotes replaced
    with null bytes of the same length.
    """
    masked = list(cmd)
    # Mask ANSI-C quoting first (before regular quotes eat the $'...')
    for m in _ANSI_C_RE.finditer(cmd):
        for i in range(m.start(), m.end()):
            masked[i] = "\x00"
    # Then mask regular quotes
    for m in _QUOTE_RE.finditer(cmd):
        for i in range(m.start(), m.end()):
            masked[i] = "\x00"
    return "".join(masked), cmd


def _extract_subshells(cmd: str, masked: str) -> tuple[str, list[str]]:
    """Extract $(...) and `...` subshells from the command.

    Returns the command with subshells replaced by placeholders, and
    a list of the extracted subshell contents.
    """
    subshells: list[str] = []

    # Handle $(...) — need to track nesting depth
    result_chars = list(cmd)
    i = 0
    while i < len(masked):
        if masked[i:i+2] == "$(" and masked[i] != "\x00":
            depth = 1
            start = i
            j = i + 2
            while j < len(masked) and depth > 0:
                if masked[j] == "(" and masked[j-1:j+1] != "\\(":
                    depth += 1
                elif masked[j] == ")" and masked[j-1:j+1] != "\\)":
                    depth -= 1
                j += 1
            if depth == 0:
                inner = cmd[start+2:j-1]
                subshells.append(inner)
                for k in range(start, j):
                    result_chars[k] = "\x01"
                masked = masked[:start] + "\x01" * (j - start) + masked[j:]
            i = j
        else:
            i += 1

    cmd = "".join(result_chars)

    # Handle backticks
    result_chars = list(cmd)
    i = 0
    while i < len(masked):
        if masked[i] == "`" and masked[i] != "\x00":
            j = i + 1
            while j < len(masked) and masked[j] != "`":
                j += 1
            if j < len(masked):
                inner = cmd[i+1:j]
                subshells.append(inner)
                for k in range(i, j+1):
                    result_chars[k] = "\x01"
                masked = masked[:i] + "\x01" * (j + 1 - i) + masked[j+1:]
                i = j + 1
            else:
                i += 1
        else:
            i += 1

    cmd = "".join(result_chars)
    return cmd, subshells


def _extract_heredocs(cmd: str, masked: str) -> tuple[str, list[str]]:
    """Extract heredoc bodies from the command.

    Handles ``<<EOF...EOF``, ``<<-EOF...EOF``, ``<<'EOF'...EOF``,
    ``<<"EOF"...EOF``.

    Returns the command with heredoc bodies removed, and a list of
    the heredoc body contents.
    """
    heredoc_bodies: list[str] = []
    lines = cmd.split("\n")
    masked_lines = masked.split("\n")
    result_lines: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        masked_line = masked_lines[i] if i < len(masked_lines) else line

        # Check for heredoc start in the masked line
        m = _HEREDOC_RE.search(masked_line)
        if m:
            delimiter = m.group(1) or m.group(2) or m.group(3)
            # Keep the command line: text before the heredoc marker + text
            # after it (e.g. "cat <<EOF | grep hello" keeps "cat | grep hello")
            before = line[:m.start()].rstrip()
            after = line[m.end():].lstrip()
            result_lines.append(f"{before} {after}".strip() if after else before)
            # Collect heredoc body
            body_lines: list[str] = []
            i += 1
            while i < len(lines):
                if lines[i].strip() == delimiter:
                    break
                body_lines.append(lines[i])
                i += 1
            heredoc_bodies.append("\n".join(body_lines))
        else:
            result_lines.append(line)
        i += 1

    return "\n".join(result_lines), heredoc_bodies


def _extract_process_subs(cmd: str, masked: str) -> tuple[str, list[str]]:
    """Extract ``<(...)`` and ``>(...)`` process substitutions.

    Returns the command with process subs replaced by placeholders,
    and a list of the extracted inner contents.
    """
    subshells: list[str] = []
    result_chars = list(cmd)
    i = 0

    while i < len(masked):
        if (i + 1 < len(masked)
                and masked[i] in "<>"
                and masked[i + 1] == "("
                and masked[i] != "\x00"):
            # Make sure this is a process substitution, not $( or a regular
            # redirect.  Process subs are <( or >( where the preceding char
            # is not $.
            if i > 0 and masked[i - 1] == "$":
                i += 1
                continue
            depth = 1
            start = i
            j = i + 2
            while j < len(masked) and depth > 0:
                if masked[j] == "(" and masked[j] != "\x00":
                    depth += 1
                elif masked[j] == ")" and masked[j] != "\x00":
                    depth -= 1
                j += 1
            if depth == 0:
                inner = cmd[start + 2:j - 1]
                subshells.append(inner)
                for k in range(start, j):
                    result_chars[k] = "\x01"
                masked = masked[:start] + "\x01" * (j - start) + masked[j:]
            i = j
        else:
            i += 1

    return "".join(result_chars), subshells


def tokenize(cmd: str) -> list[Segment]:
    """Tokenize a shell command into segments.

    Splits on ``|``, ``&&``, ``||``, ``;``, and extracts ``$(...)`` and
    backtick subshells as separate segments.  Also handles heredocs,
    process substitutions ``<(...)`` / ``>(...)``, and ANSI-C ``$'...'``
    quoting.

    Raises ValueError if the number of segments exceeds MAX_SUBCOMMANDS.
    """
    # Strip full-line comments first
    cmd = strip_comments(cmd)

    if not cmd or not cmd.strip():
        return []

    masked, original = _mask_quotes(cmd)

    # Extract heredocs before splitting
    cmd, _heredoc_bodies = _extract_heredocs(cmd, masked)
    masked, _ = _mask_quotes(cmd)  # re-mask after heredoc removal

    # Extract process substitutions
    cmd, proc_subs = _extract_process_subs(cmd, masked)
    masked, _ = _mask_quotes(cmd)  # re-mask after process sub removal

    # Extract $(...) and backtick subshells
    cmd_no_sub, subshells = _extract_subshells(cmd, masked)

    # Build the masked version without subshells for splitting
    masked_for_split, _ = _mask_quotes(cmd_no_sub)

    parts = _OPERATOR_RE.split(masked_for_split)

    segments: list[Segment] = []
    for part in parts:
        # Replace placeholders back to get readable text
        clean = part.replace("\x01", "").strip()
        if clean:
            segments.append(Segment(text=clean, seg_type=SegmentType.SIMPLE))

    # Add subshell contents as separate segments
    for sub in subshells:
        sub_stripped = sub.strip()
        if sub_stripped:
            segments.append(Segment(text=sub_stripped, seg_type=SegmentType.SUBSHELL))

    # Add process substitution contents as subshell segments
    for sub in proc_subs:
        sub_stripped = sub.strip()
        if sub_stripped:
            segments.append(Segment(text=sub_stripped, seg_type=SegmentType.SUBSHELL))

    total = len(segments)
    if total > MAX_SUBCOMMANDS:
        raise ValueError(
            f"Subcommand fanout cap exceeded: {total} segments "
            f"(max {MAX_SUBCOMMANDS}). Possible DoS attempt."
        )

    return segments


# ---------------------------------------------------------------------------
# Structural classifier
# ---------------------------------------------------------------------------

# Risk levels in ascending severity
_RISK_ORDER = {"safe": 0, "moderate": 1, "dangerous": 2}
_RISK_NAMES = {0: "safe", 1: "moderate", 2: "dangerous"}


def ast_classify(cmd: str, *, shell: str = "bash") -> "Classification":
    """Classify a shell command using structural AST analysis.

    Tokenizes the command into segments, strips wrappers from each,
    then classifies each segment via the regex patterns in
    :mod:`duh.tools.bash_security`.

    Also runs the regex classifier on the full (wrapper-stripped) command
    to catch cross-segment patterns like ``curl ... | bash``.

    Returns the highest-risk classification found across all segments.
    If tokenization fails (e.g., fanout cap exceeded), returns dangerous.
    """
    from duh.tools.bash_security import _regex_classify, is_env_var_safe

    if not cmd or not cmd.strip():
        return {"risk": "safe", "reason": ""}

    try:
        segments = tokenize(cmd)
    except ValueError as exc:
        return {"risk": "dangerous", "reason": str(exc)}

    if not segments:
        return {"risk": "safe", "reason": ""}

    worst_risk = 0
    worst_reason = ""

    # First: run full-command regex to catch cross-segment patterns
    # like "curl ... | bash" that span pipe boundaries.
    stripped_full = strip_comments(cmd).strip()
    if stripped_full:
        full_result = _regex_classify(stripped_full, shell=shell)
        full_level = _RISK_ORDER.get(full_result["risk"], 0)
        if full_level > worst_risk:
            worst_risk = full_level
            worst_reason = full_result["reason"]
        if worst_risk == 2:
            return {"risk": "dangerous", "reason": worst_reason}

    # Then: classify each segment individually (with wrappers stripped)
    for seg in segments:
        # Strip wrappers before classifying the inner command
        inner = strip_wrappers(seg.text)
        if not inner.strip():
            continue

        # Check for env var assignments (VAR=value cmd) — catch binary hijacks
        _env_assign = re.match(r"^(\w+)=", inner)
        if _env_assign and not is_env_var_safe(_env_assign.group(1)):
            var_name = _env_assign.group(1)
            return {"risk": "dangerous", "reason": f"Binary hijack via {var_name}"}

        result = _regex_classify(inner, shell=shell)
        risk_level = _RISK_ORDER.get(result["risk"], 0)

        if risk_level > worst_risk:  # pragma: no cover - full-regex catches it first
            worst_risk = risk_level
            worst_reason = result["reason"]

        # Short-circuit: can't get worse than dangerous
        if worst_risk == 2:  # pragma: no cover - full-regex catches it first
            return {"risk": "dangerous", "reason": worst_reason}

    return {"risk": _RISK_NAMES.get(worst_risk, "safe"), "reason": worst_reason}
