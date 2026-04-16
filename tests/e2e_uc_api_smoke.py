"""
E2E Smoke Test: Universal Companion API with D.U.H. backend.

Tests that the UC API server can start with D.U.H. as the CLI backend
(via DUH_CLI_PATH env var) and handle basic requests.

Usage:
    UC_API_DIR=/path/to/universal-companion-api python tests/e2e_uc_api_smoke.py
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DUH_SHIM = os.environ.get("DUH_SHIM", str(_PROJECT_ROOT / "bin" / "duh-sdk-shim"))
UC_API_DIR = os.environ.get("UC_API_DIR", str(_PROJECT_ROOT.parent / "UniversalCompanion" / "universal-companion-api"))
UC_VENV_PYTHON = os.environ.get("UC_VENV_PYTHON", f"{UC_API_DIR}/.venv/bin/python3")
PORT = 8099  # Use non-standard port to avoid conflicts


def test_health_endpoint():
    """Test that the UC API health endpoint responds."""
    import httpx

    print("--- Test: Health endpoint ---")
    try:
        r = httpx.get(f"http://localhost:{PORT}/health", timeout=5)
        print(f"  Status: {r.status_code}")
        print(f"  Body: {r.text[:200]}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        print("  PASS")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_docs_endpoint():
    """Test that FastAPI docs are accessible."""
    import httpx

    print("--- Test: Docs endpoint ---")
    try:
        r = httpx.get(f"http://localhost:{PORT}/docs", timeout=5)
        print(f"  Status: {r.status_code}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        assert "Universal Companion" in r.text or "swagger" in r.text.lower()
        print("  PASS")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def main() -> int:
    print("=" * 60)
    print("E2E: Universal Companion API with D.U.H. backend")
    print("=" * 60)
    print(f"  DUH shim: {DUH_SHIM}")
    print(f"  UC API:   {UC_API_DIR}")
    print(f"  Port:     {PORT}")
    print()

    # Check prerequisites
    if not os.path.isfile(DUH_SHIM):
        print(f"ERROR: D.U.H. shim not found: {DUH_SHIM}")
        return 1
    if not os.path.isdir(UC_API_DIR):
        print(f"ERROR: UC API not found: {UC_API_DIR}")
        return 1

    # Start the UC API server with D.U.H. backend
    env = {
        **os.environ,
        "DUH_CLI_PATH": DUH_SHIM,
        "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1",
        "PORT": str(PORT),
        "LOG_LEVEL": "WARNING",
    }

    print("Starting UC API server...")
    server = subprocess.Popen(
        [UC_VENV_PYTHON, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=UC_API_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to start
    import httpx
    started = False
    for i in range(20):
        time.sleep(1)
        try:
            r = httpx.get(f"http://localhost:{PORT}/health", timeout=2)
            if r.status_code == 200:
                started = True
                break
        except Exception:
            pass
        # Check if process died
        if server.poll() is not None:
            out = server.stdout.read().decode()[:500] if server.stdout else ""
            err = server.stderr.read().decode()[:500] if server.stderr else ""
            print(f"Server died with code {server.returncode}")
            print(f"  stdout: {out}")
            print(f"  stderr: {err}")
            return 1

    if not started:
        print("ERROR: Server did not start within 20 seconds")
        server.terminate()
        server.wait()
        return 1

    print(f"Server started on port {PORT}")
    print()

    # Run tests
    results = []
    try:
        results.append(("health", test_health_endpoint()))
        results.append(("docs", test_docs_endpoint()))
    finally:
        print()
        print("Stopping server...")
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        print("Server stopped.")

    # Summary
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    all_passed = all(ok for _, ok in results)
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")

    print()
    if all_passed:
        print("  ALL TESTS PASSED: UC API runs with D.U.H. backend")
    else:
        print("  SOME TESTS FAILED")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
