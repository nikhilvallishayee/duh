"""
E2E Chat Smoke Test: UC API agent runner with D.U.H. backend.

Tests the full flow: AgentRunner -> Claude Agent SDK -> D.U.H. -> Ollama -> response.
Bypasses HTTP auth layer, tests the service layer directly.

Usage:
    DUH_CLI_PATH=bin/duh-sdk-shim \
    CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1 \
    UC_API_DIR=/path/to/universal-companion-api \
    python tests/e2e_uc_chat_smoke.py
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Set D.U.H. backend before imports (env override or derive from project root)
if "DUH_CLI_PATH" not in os.environ:
    os.environ["DUH_CLI_PATH"] = str(_PROJECT_ROOT / "bin" / "duh-sdk-shim")
os.environ.setdefault("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")

# Add UC API to path (must be provided via env var in CI)
UC_API_DIR = os.environ.get("UC_API_DIR", str(_PROJECT_ROOT.parent / "UniversalCompanion" / "universal-companion-api"))
sys.path.insert(0, UC_API_DIR)


async def test_sdk_query_direct():
    """Test the Claude Agent SDK query directly with D.U.H. backend."""
    from claude_agent_sdk import ClaudeAgentOptions, query, AssistantMessage, TextBlock, ResultMessage

    print("--- Test: Direct SDK query via D.U.H. ---")

    options = ClaudeAgentOptions(
        cli_path=os.environ["DUH_CLI_PATH"],
        max_turns=1,
        permission_mode="bypassPermissions",
        system_prompt="You are a helpful assistant. Be concise.",
    )

    got_assistant = False
    got_result = False
    response_text = ""

    start = time.monotonic()
    async for message in query(prompt="What is the meaning of life? Answer in one sentence.", options=options):
        if isinstance(message, AssistantMessage):
            got_assistant = True
            for block in message.content:
                if isinstance(block, TextBlock):
                    response_text = block.text
                    print(f"  Assistant: {block.text[:200]}")
        elif isinstance(message, ResultMessage):
            got_result = True
            print(f"  Result: {message.subtype}, turns={message.num_turns}")

    elapsed = time.monotonic() - start
    print(f"  Duration: {elapsed:.1f}s")

    assert got_assistant, "No AssistantMessage received"
    assert got_result, "No ResultMessage received"
    assert len(response_text) > 0, "Empty response"
    print("  PASS")
    return True


async def test_sdk_with_system_prompt():
    """Test SDK with a custom system prompt (simulates UC agent config)."""
    from claude_agent_sdk import ClaudeAgentOptions, query, AssistantMessage, TextBlock, ResultMessage

    print("--- Test: SDK with custom system prompt ---")

    options = ClaudeAgentOptions(
        cli_path=os.environ["DUH_CLI_PATH"],
        max_turns=1,
        permission_mode="bypassPermissions",
        system_prompt=(
            "You are a Pattern Space assistant for Universal Companion. "
            "You help users explore patterns of consciousness and growth. "
            "Be warm, insightful, and concise."
        ),
    )

    got_assistant = False
    async for message in query(prompt="What is a pattern?", options=options):
        if isinstance(message, AssistantMessage):
            got_assistant = True
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"  Assistant: {block.text[:200]}")
        elif isinstance(message, ResultMessage):
            print(f"  Result: {message.subtype}")

    assert got_assistant, "No AssistantMessage received"
    print("  PASS")
    return True


async def main():
    print("=" * 60)
    print("E2E: UC API Chat with D.U.H. Backend")
    print("=" * 60)
    print(f"  DUH_CLI_PATH: {os.environ.get('DUH_CLI_PATH')}")
    print()

    results = []
    try:
        results.append(("sdk_query", await test_sdk_query_direct()))
        results.append(("system_prompt", await test_sdk_with_system_prompt()))
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        results.append(("error", False))

    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    all_passed = all(ok for _, ok in results)
    print()
    print("  ALL PASSED" if all_passed else "  SOME FAILED")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
