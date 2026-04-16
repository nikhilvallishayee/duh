"""
E2E: claude-flow executor compatibility with D.U.H. backend.

Tests that D.U.H.'s shim produces output compatible with claude-flow's
SwarmExecutor, which spawns CLI subprocesses with:
  - `-p <prompt>` for prompt mode
  - `--dangerously-skip-permissions` for auto-approval
  - `--output-format json` for structured output
  - `--allowedTools <tools>` for tool filtering
  - `--model <model>` for model selection

Usage:
    python tests/e2e_claudeflow_smoke.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DUH_SHIM = os.environ.get("DUH_SHIM", str(_PROJECT_ROOT / "bin" / "duh-sdk-shim"))


def run_duh(*args, timeout=15):
    return subprocess.run(
        [DUH_SHIM, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def test_print_mode():
    """claude-flow passes -p <prompt> --dangerously-skip-permissions."""
    print("--- Test: Print mode (claude-flow executor path) ---")
    r = run_duh("-p", "What is 2+2? Reply with just the number.",
                "--dangerously-skip-permissions", "--max-turns", "1")
    assert r.returncode == 0, f"Exit code {r.returncode}: {r.stderr[:200]}"
    output = r.stdout.strip()
    assert len(output) > 0, "Empty output"
    print(f"  Output: {output[:100]}")
    print("  PASS")
    return True


def test_json_output():
    """claude-flow passes --output-format json."""
    print("--- Test: JSON output format ---")
    r = run_duh("-p", "Say hello",
                "--dangerously-skip-permissions", "--max-turns", "1",
                "--output-format", "json")
    assert r.returncode == 0, f"Exit code {r.returncode}: {r.stderr[:200]}"
    events = json.loads(r.stdout)
    assert isinstance(events, list), "Expected JSON array"
    types = [e.get("type") for e in events]
    assert "assistant" in types, f"Missing assistant event in {types}"
    print(f"  Events: {len(events)} ({', '.join(set(types))})")
    print("  PASS")
    return True


def test_stream_json():
    """claude-flow can use stream-json for real-time output."""
    print("--- Test: Stream-JSON (NDJSON) mode ---")
    input_data = (
        '{"type":"control_request","request_id":"r1","request":{"subtype":"initialize"}}\n'
        '{"type":"user","session_id":"","message":{"role":"user","content":"Say hello"},"parent_tool_use_id":null}\n'
    )
    r = subprocess.run(
        [DUH_SHIM, "--input-format", "stream-json", "--output-format", "stream-json",
         "--dangerously-skip-permissions", "--max-turns", "1"],
        input=input_data, capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, f"Exit code {r.returncode}: {r.stderr[:200]}"
    messages = [json.loads(l) for l in r.stdout.strip().split("\n") if l.strip()]
    types = [m.get("type") for m in messages]
    assert "control_response" in types, f"Missing control_response in {types}"
    assert "assistant" in types, f"Missing assistant in {types}"
    assert "result" in types, f"Missing result in {types}"
    print(f"  Messages: {len(messages)} ({', '.join(types)})")
    print("  PASS")
    return True


def test_model_flag():
    """claude-flow passes --model <model>."""
    print("--- Test: Model flag ---")
    r = run_duh("-p", "Say hi", "--dangerously-skip-permissions",
                "--max-turns", "1", "--model", "qwen2.5-coder:1.5b")
    assert r.returncode == 0, f"Exit code {r.returncode}: {r.stderr[:200]}"
    assert len(r.stdout.strip()) > 0
    print(f"  Output: {r.stdout.strip()[:100]}")
    print("  PASS")
    return True


def test_allowed_tools_flag():
    """claude-flow passes --allowedTools <tools>. D.U.H. should accept without crashing."""
    print("--- Test: allowedTools flag (SDK compat) ---")
    r = run_duh("-p", "What is 2+2?", "--dangerously-skip-permissions",
                "--max-turns", "1", "--allowedTools", "Read,Write,Bash",
                "--output-format", "json")
    assert r.returncode == 0, f"Exit code {r.returncode}: {r.stderr[:200]}"
    print("  PASS (flag accepted)")
    return True


def main():
    print("=" * 60)
    print("E2E: claude-flow Executor Compatibility with D.U.H.")
    print("=" * 60)
    print(f"  Shim: {DUH_SHIM}")
    print()

    results = []
    for test in [test_print_mode, test_json_output, test_stream_json,
                 test_model_flag, test_allowed_tools_flag]:
        try:
            results.append((test.__name__, test()))
        except Exception as e:
            print(f"  FAIL: {e}")
            results.append((test.__name__, False))

    print()
    print("=" * 60)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    all_ok = all(ok for _, ok in results)
    print(f"\n  {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
