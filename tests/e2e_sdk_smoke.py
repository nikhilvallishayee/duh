"""
E2E Smoke Test: Claude Agent SDK -> D.U.H. backend

Tests that the Claude Agent SDK can launch D.U.H. as its CLI backend,
send a simple prompt via the stream-json NDJSON protocol, and receive
a parsed response.

Usage:
    CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1 /Users/nomind/Code/duh/.venv/bin/python3 tests/e2e_sdk_smoke.py
"""

import asyncio
import os
import sys
import traceback

DUH_SHIM = "/Users/nomind/Code/duh/bin/duh-sdk-shim"

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    UserMessage,
    query,
)


async def run_smoke_test() -> bool:
    """Run the SDK smoke test against D.U.H."""

    print("=" * 60)
    print("E2E Smoke Test: Claude Agent SDK -> D.U.H.")
    print("=" * 60)
    print()

    prompt = "What is 2+2? Reply with just the number."
    print(f"Prompt: {prompt!r}")
    print(f"CLI shim: {DUH_SHIM}")
    print()

    options = ClaudeAgentOptions(
        cli_path=DUH_SHIM,
        max_turns=1,
        permission_mode="bypassPermissions",
    )

    messages_received = []
    got_assistant = False
    got_result = False

    print("--- Messages from SDK ---")
    try:
        async for message in query(prompt=prompt, options=options):
            messages_received.append(message)

            if isinstance(message, SystemMessage):
                print(f"  [system] subtype={message.subtype}")

            elif isinstance(message, UserMessage):
                content = message.content if isinstance(message.content, str) else "<blocks>"
                print(f"  [user] {content}")

            elif isinstance(message, AssistantMessage):
                got_assistant = True
                print(f"  [assistant] model={message.model}")
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"    text: {block.text!r}")
                    else:
                        print(f"    block: {type(block).__name__}")

            elif isinstance(message, ResultMessage):
                got_result = True
                print(f"  [result] subtype={message.subtype}")
                print(f"    is_error={message.is_error}")
                print(f"    num_turns={message.num_turns}")
                print(f"    duration_ms={message.duration_ms}")
                print(f"    session_id={message.session_id}")
                if message.result is not None:
                    print(f"    result={message.result!r}")

            else:
                print(f"  [unknown] {type(message).__name__}: {message}")

    except Exception as e:
        print(f"\n  ERROR during query: {type(e).__name__}: {e}")
        traceback.print_exc()
        print()

    print("--- End Messages ---")
    print()

    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  Total messages received: {len(messages_received)}")
    print(f"  Got AssistantMessage:    {got_assistant}")
    print(f"  Got ResultMessage:       {got_result}")

    passed = got_assistant and got_result and len(messages_received) > 0
    if passed:
        print()
        print("  PASS: SDK successfully launched D.U.H., got a response, and parsed it.")
    else:
        print()
        print("  FAIL: Missing expected messages.")
        if not got_assistant:
            print("    - No AssistantMessage received")
        if not got_result:
            print("    - No ResultMessage received")

    print()
    return passed


def main() -> int:
    passed = asyncio.run(run_smoke_test())
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
