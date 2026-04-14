"""T3: RuFlow (claude-flow) orchestration test.

Validates that D.U.H. can be orchestrated by external tools.
Tests that claude-flow can invoke D.U.H. via the SDK shim as a CLI backend.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
DUH_SHIM = str(PROJECT_ROOT / "bin" / "duh-sdk-shim")
# Use whatever Python is currently running the test suite. On dev machines
# that's the venv interpreter; on CI it's the GitHub-managed Python.
DUH_PYTHON = sys.executable


def _run(
    cmd: list[str],
    timeout: int = 15,
    stub_provider: bool = True,
    **kwargs,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess. By default DUH_STUB_PROVIDER=1 is injected so the
    invoked CLI uses a deterministic in-process stub instead of needing
    real API credentials."""
    env = kwargs.pop("env", None) or os.environ.copy()
    if stub_provider:
        env["DUH_STUB_PROVIDER"] = "1"
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=env, **kwargs,
    )


class TestRuFlowAvailable:
    """Verify claude-flow CLI is available."""

    def test_claude_flow_version(self):
        # Skip cleanly if npx isn't on PATH (CI without Node).
        if shutil.which("npx") is None:
            pytest.skip("npx not available")
        try:
            result = _run(["npx", "--no-install", "@claude-flow/cli", "--version"], timeout=15)
        except subprocess.TimeoutExpired:
            pytest.skip("claude-flow CLI lookup timed out")
        if result.returncode != 0:
            pytest.skip("claude-flow CLI not available")
        assert "claude-flow" in result.stdout.lower()


class TestDuhAsOrchestrationTarget:
    """D.U.H. can be invoked as a subprocess by an orchestrator."""

    def test_duh_print_mode_from_subprocess(self):
        """Orchestrator can invoke D.U.H. in print mode and get output."""
        result = _run(
            [DUH_PYTHON, "-m", "duh", "-p", "What is 2+2? Reply with just the number.",
             "--dangerously-skip-permissions", "--max-turns", "1"],
            cwd=str(PROJECT_ROOT),
            timeout=30,
        )
        assert result.returncode == 0
        # Should get some output (model response)
        assert len(result.stdout.strip()) > 0

    def test_duh_stream_json_from_subprocess(self):
        """Orchestrator can invoke D.U.H. in stream-json mode and parse NDJSON."""
        input_lines = (
            '{"type":"control_request","request_id":"r1","request":{"subtype":"initialize"}}\n'
            '{"type":"user","session_id":"","message":{"role":"user","content":"What is 2+2? Reply with just the number."},"parent_tool_use_id":null}\n'
        )
        result = _run(
            [DUH_PYTHON, "-m", "duh",
             "--input-format", "stream-json",
             "--output-format", "stream-json",
             "--dangerously-skip-permissions",
             "--max-turns", "1"],
            input=input_lines,
            cwd=str(PROJECT_ROOT),
            timeout=30,
        )
        assert result.returncode == 0

        # Parse NDJSON output
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        messages = [json.loads(l) for l in lines]
        types = [m.get("type") for m in messages]

        # Must have control_response, assistant, and result
        assert "control_response" in types, f"Missing control_response in {types}"
        assert "assistant" in types, f"Missing assistant in {types}"
        assert "result" in types, f"Missing result in {types}"

        # Result should be success
        result_msg = [m for m in messages if m.get("type") == "result"][0]
        assert result_msg["is_error"] is False

    def test_duh_shim_exists_and_executable(self):
        """The SDK shim exists and is executable."""
        if not os.path.isfile(DUH_SHIM):
            pytest.skip(f"Shim not present at {DUH_SHIM}")
        assert os.access(DUH_SHIM, os.X_OK), f"Shim not executable: {DUH_SHIM}"

    def test_multiple_sequential_invocations(self):
        """Orchestrator can invoke D.U.H. multiple times in sequence."""
        for i in range(3):
            input_lines = (
                '{"type":"control_request","request_id":"r1","request":{"subtype":"initialize"}}\n'
                f'{{"type":"user","session_id":"","message":{{"role":"user","content":"Say the number {i+1}."}},"parent_tool_use_id":null}}\n'
            )
            result = _run(
                [DUH_PYTHON, "-m", "duh",
                 "--input-format", "stream-json",
                 "--output-format", "stream-json",
                 "--dangerously-skip-permissions",
                 "--max-turns", "1"],
                input=input_lines,
                cwd=str(PROJECT_ROOT),
                timeout=30,
            )
            assert result.returncode == 0
            messages = [json.loads(l) for l in result.stdout.strip().split("\n") if l.strip()]
            result_msgs = [m for m in messages if m.get("type") == "result"]
            assert len(result_msgs) == 1
            assert result_msgs[0]["is_error"] is False
