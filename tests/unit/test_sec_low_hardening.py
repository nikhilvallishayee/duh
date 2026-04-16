"""SEC-LOW / INFO hardening regression tests (QE Analysis #8).

Covers four findings rolled up as "SEC low hardening":

* SEC-MEDIUM-7 — security-critical dependencies have upper bounds in
  ``pyproject.toml`` so a new major can't silently land in CI.
* SEC-LOW-1 — ``eval`` dangerous-command regex is anchored to command
  position only (start of command or after a separator/pipe/subshell
  opener). It no longer fires on ``safe_eval``, ``my_eval``, quoted
  occurrences, or positional arguments.
* SEC-LOW-3 + INFO-2 — :class:`UntrustedStr` overrides ``__format__`` so
  single-variable f-strings and ``format()`` calls preserve taint.
* SEC-INFO-3 — :meth:`MCPExecutor.run` wraps its output string in
  :class:`UntrustedStr` with ``TaintSource.MCP_OUTPUT`` so downstream
  consumers see the taint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore[no-redef]

from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.tools.bash_security import classify_command, is_dangerous


# ===========================================================================
# SEC-MEDIUM-7 — Dependency upper bounds
# ===========================================================================


class TestDependencyUpperBounds:
    """Security-critical deps must cap at a known major.

    Rationale: anthropic, openai, litellm, httpx, and mcp are on the trust
    boundary (auth, transport, remote code). A surprise major bump can
    bypass our compatibility tests; requiring an explicit upper bound
    forces a human review when a new major lands.
    """

    _PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

    @pytest.fixture(scope="class")
    def deps(self) -> list[str]:
        with self._PYPROJECT.open("rb") as fh:
            data = tomllib.load(fh)
        return list(data["project"]["dependencies"])

    def _find(self, deps: list[str], name: str) -> str:
        for spec in deps:
            # very shallow parse -- we only care about the name prefix
            head = spec.split(",")[0].split(">")[0].split("<")[0].split("=")[0].strip()
            if head == name:
                return spec
        raise AssertionError(f"dependency '{name}' not found in pyproject.toml")

    @pytest.mark.parametrize(
        "dep",
        ["anthropic", "httpx", "openai", "litellm", "mcp"],
    )
    def test_security_critical_deps_have_upper_bound(
        self, deps: list[str], dep: str
    ) -> None:
        spec = self._find(deps, dep)
        assert "<" in spec, (
            f"security-critical dep '{dep}' must have an upper bound "
            f"(SEC-MEDIUM-7); got: {spec!r}"
        )

    @pytest.mark.parametrize(
        "dep",
        ["pydantic", "rich", "textual", "websockets", "pdfplumber"],
    )
    def test_other_deps_constrained_to_major(
        self, deps: list[str], dep: str
    ) -> None:
        spec = self._find(deps, dep)
        assert "<" in spec, (
            f"dep '{dep}' should be constrained to the current major "
            f"(SEC-MEDIUM-7); got: {spec!r}"
        )


# ===========================================================================
# SEC-LOW-1 — eval regex anchored to command position
# ===========================================================================


class TestEvalRegexIsPositional:
    """``eval`` must be flagged only when it appears as a command, not as
    a substring, identifier prefix/suffix, or quoted argument.

    Legitimate patterns that must NOT fire:
      * ``safe_eval``, ``my_eval`` -- underscore-joined identifiers
      * ``beval``, ``evaluate`` -- extension of the word
      * ``eval_model`` -- underscore suffix
      * ``echo "eval tests"`` -- eval inside a quoted argument
      * ``git commit -m "eval regex"`` -- same
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            "eval $(decode payload)",
            "eval foo bar",
            "ls | eval foo",
            "x && eval foo",
            "x || eval foo",
            "x; eval foo",
            "a=$(eval foo)",
            "(eval foo)",
        ],
    )
    def test_eval_as_command_is_dangerous(self, cmd: str) -> None:
        result = classify_command(cmd)
        assert result["risk"] == "dangerous", (
            f"expected 'dangerous' for real eval invocation: {cmd!r}; "
            f"got {result!r}"
        )
        assert "eval" in result["reason"].lower()

    @pytest.mark.parametrize(
        "cmd",
        [
            # Identifier prefix/suffix collisions (previous false positives
            # in the literature; we keep them here as regression guards).
            "echo safe_eval",
            "echo my_eval",
            "echo eval_model",
            "echo evaluate",
            "echo beval",
            # eval as a positional arg to another command
            "cat eval foo.txt",
            # eval inside quoted strings
            'echo "eval foo"',
            'git commit -m "eval tests"',
            "printf '%s' 'eval hi'",
        ],
    )
    def test_non_command_eval_is_not_dangerous(self, cmd: str) -> None:
        # Not dangerous -- must be "safe" or "moderate" (never blocked on eval).
        result = classify_command(cmd)
        assert result["risk"] != "dangerous" or "eval" not in result["reason"].lower(), (
            f"false positive: {cmd!r} classified as dangerous for eval: "
            f"{result!r}"
        )
        assert not (is_dangerous(cmd) and "eval" in result.get("reason", "").lower()), (
            f"is_dangerous false-positive on eval substring for {cmd!r}"
        )


# ===========================================================================
# SEC-LOW-3 + INFO-2 — f-string / format() preserves taint
# ===========================================================================


class TestFStringTaintPropagation:
    def test_format_returns_untrusted_str(self) -> None:
        tainted = UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
        out = tainted.__format__("")
        assert isinstance(out, UntrustedStr)
        assert out.source == TaintSource.MODEL_OUTPUT

    def test_format_preserves_spec(self) -> None:
        tainted = UntrustedStr("abc", TaintSource.TOOL_OUTPUT)
        padded = tainted.__format__(">10")
        assert isinstance(padded, UntrustedStr)
        assert padded.source == TaintSource.TOOL_OUTPUT
        assert padded == "       abc"

    def test_builtin_format_preserves_taint(self) -> None:
        tainted = UntrustedStr("data", TaintSource.FILE_CONTENT)
        result = format(tainted, "")
        assert isinstance(result, UntrustedStr)
        assert result.source == TaintSource.FILE_CONTENT

    def test_single_var_fstring_preserves_taint(self) -> None:
        tainted = UntrustedStr("payload", TaintSource.MCP_OUTPUT)
        result = f"{tainted}"
        assert isinstance(result, UntrustedStr), (
            "single-variable f-string must preserve UntrustedStr subclass"
        )
        assert result.source == TaintSource.MCP_OUTPUT

    def test_single_var_fstring_with_spec_preserves_taint(self) -> None:
        tainted = UntrustedStr("x", TaintSource.NETWORK)
        result = f"{tainted:>5}"
        assert isinstance(result, UntrustedStr)
        assert result.source == TaintSource.NETWORK
        assert result == "    x"

    def test_untainted_source_preserved(self) -> None:
        # SYSTEM is in UNTAINTED_SOURCES -- preserving it is still correct.
        trusted = UntrustedStr("sys", TaintSource.SYSTEM)
        result = format(trusted, "")
        assert isinstance(result, UntrustedStr)
        assert result.source == TaintSource.SYSTEM


# ===========================================================================
# SEC-INFO-3 — MCPExecutor.run() wraps output with MCP_OUTPUT taint
# ===========================================================================


# Local fake types for MCPExecutor.run() tests.  We do NOT inject a fake
# ``mcp`` package into sys.modules the way tests/unit/test_mcp_executor.py
# does — that approach conflicts badly with co-loaded test files because
# the module cache and import state end up interleaved.  Instead we rely
# on the real mcp package being installed (which it is -- it's a direct
# project dependency), and we intercept only the bits of ``connect()``
# that would actually talk to a subprocess.
from duh.adapters.mcp_executor import (  # noqa: E402
    MCPExecutor,
    MCPServerConfig,
    MCPToolInfo,
    _wrap_mcp_output,
)


@dataclass
class _FakeContentBlock:
    text: str = ""


@dataclass
class _FakeCallToolResult:
    content: list[_FakeContentBlock] = field(default_factory=list)


class _FakeSession:
    """Minimal stand-in for ``mcp.ClientSession`` for run() tests.

    Only implements the two methods ``MCPExecutor.run()`` actually uses
    after a successful connect: ``call_tool`` and (for the connect path,
    though we bypass it entirely) nothing else.
    """

    def __init__(self) -> None:
        self._call_results: dict[str, _FakeCallToolResult] = {}

    async def call_tool(
        self, name: str, *, arguments: dict[str, Any] | None = None
    ) -> _FakeCallToolResult:
        if name in self._call_results:
            return self._call_results[name]
        raise RuntimeError(f"no result configured for {name}")

    async def __aexit__(self, *args: Any) -> None:
        return None


class TestMCPOutputTaintWrapping:
    """``MCPExecutor.run()`` must return an ``UntrustedStr`` tagged
    ``MCP_OUTPUT`` so callers who forward the value elsewhere inherit the
    taint. Previously, ``_wrap_mcp_output`` existed but was unused on the
    happy path, and the run() method returned a bare ``str``."""

    def test_wrap_mcp_output_wraps_plain_str(self) -> None:
        wrapped = _wrap_mcp_output("raw server response")
        assert isinstance(wrapped, UntrustedStr)
        assert wrapped.source == TaintSource.MCP_OUTPUT

    def test_wrap_mcp_output_is_idempotent(self) -> None:
        pre = UntrustedStr("already", TaintSource.MCP_OUTPUT)
        wrapped = _wrap_mcp_output(pre)
        assert wrapped is pre or wrapped == pre
        assert wrapped.source == TaintSource.MCP_OUTPUT

    def _wire_executor(
        self,
        server_name: str,
        tool_name: str,
        session: _FakeSession,
    ) -> MCPExecutor:
        """Build an executor with a pre-wired fake connection.

        This sidesteps ``connect()`` entirely so these tests do not depend
        on the subprocess / stdio / unicode-validation plumbing exercised
        by tests/unit/test_mcp_executor.py.  We only care about what
        ``run()`` does with the session's result.
        """
        from duh.adapters.mcp_executor import MCPConnection  # local import

        executor = MCPExecutor(
            {server_name: MCPServerConfig(command="echo", args=[])}
        )
        qualified = f"mcp__{server_name}__{tool_name}"
        info = MCPToolInfo(name=tool_name, server_name=server_name)
        executor._tool_index[qualified] = info
        # Keep the per-server index in sync for code paths that consult it
        # (e.g. PERF-14 / ``_mark_degraded``).
        server_tools = getattr(executor, "_server_tools", None)
        if isinstance(server_tools, dict):
            server_tools.setdefault(server_name, set()).add(qualified)

        executor._connections[server_name] = MCPConnection(
            server_name=server_name,
            config=executor._servers[server_name],
            session=session,
            tools=[info],
        )
        return executor

    @pytest.mark.asyncio
    async def test_run_wraps_output_with_mcp_output_taint(self) -> None:
        session = _FakeSession()
        session._call_results["greet"] = _FakeCallToolResult(
            content=[_FakeContentBlock(text="Hello, world!")]
        )
        executor = self._wire_executor("srv", "greet", session)

        result = await executor.run("mcp__srv__greet", {"name": "test"})

        assert isinstance(result, UntrustedStr), (
            "MCPExecutor.run must return UntrustedStr so MCP output "
            "carries taint downstream"
        )
        assert result.source == TaintSource.MCP_OUTPUT
        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_run_wraps_empty_output(self) -> None:
        session = _FakeSession()
        session._call_results["empty"] = _FakeCallToolResult(content=[])
        executor = self._wire_executor("srv", "empty", session)

        result = await executor.run("mcp__srv__empty", {})
        assert isinstance(result, UntrustedStr)
        assert result.source == TaintSource.MCP_OUTPUT
        assert result == ""
