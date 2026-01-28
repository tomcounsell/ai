#!/usr/bin/env python3
"""
Diagnostic script to test Claude Agent SDK directly.

Run this to debug SDK issues in isolation from the Telegram bridge.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Ensure user site-packages is available
user_site = Path.home() / "Library/Python/3.12/lib/python/site-packages"
if user_site.exists() and str(user_site) not in sys.path:
    sys.path.insert(0, str(user_site))

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

# Load env
env_path = project_root / ".env"
load_dotenv(env_path)

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)

# Enable SDK debug logging
logging.getLogger("claude_agent_sdk").setLevel(logging.DEBUG)


async def test_sdk_direct():
    """Test SDK directly without the bridge."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
    )

    print("\n=== Testing Claude Agent SDK ===")
    print(f"ANTHROPIC_API_KEY set: {bool(os.getenv('ANTHROPIC_API_KEY'))}")
    print(f"API Key prefix: {os.getenv('ANTHROPIC_API_KEY', '')[:15]}...")
    print(f"Working directory: {project_root}")
    print()

    # Simple options - minimal configuration
    options = ClaudeAgentOptions(
        system_prompt="You are a helpful assistant. Keep responses very brief.",
        cwd=str(project_root),
        permission_mode="bypassPermissions",
        max_turns=1,
        env={
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
        },
    )

    print("Options created, starting query...")
    print()

    response_parts = []

    try:
        async with ClaudeSDKClient(options) as client:
            print("Client connected")
            await client.query("Say 'Hello SDK test!' and nothing else.")
            print("Query sent, receiving response...")

            async for msg in client.receive_response():
                print(f"\n--- Message type: {type(msg).__name__} ---")

                if isinstance(msg, AssistantMessage):
                    print(f"Model: {msg.model}")
                    print(f"Content blocks: {len(msg.content)}")
                    for i, block in enumerate(msg.content):
                        print(f"  Block {i}: {type(block).__name__}")
                        if isinstance(block, TextBlock):
                            response_parts.append(block.text)
                            print(f"    Text: {block.text[:100]}...")

                elif isinstance(msg, ResultMessage):
                    print(f"Subtype: {msg.subtype}")
                    print(f"Duration: {msg.duration_ms}ms")
                    print(f"API Duration: {msg.duration_api_ms}ms")
                    print(f"Turns: {msg.num_turns}")
                    print(f"Session ID: {msg.session_id}")
                    print(f"Cost: ${msg.total_cost_usd:.4f}" if msg.total_cost_usd else "Cost: N/A")
                    print(f"Is Error: {msg.is_error}")
                    print(f"Result: {msg.result}")
                    if msg.is_error:
                        print("\n!!! ERROR DETECTED !!!")
                        print(f"Error result value: {repr(msg.result)}")

                else:
                    print(f"Raw message: {msg}")

    except Exception as e:
        print(f"\n!!! EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    print("\n=== Results ===")
    print(f"Response parts collected: {len(response_parts)}")
    if response_parts:
        full_response = "\n".join(response_parts)
        print(f"Full response:\n{full_response}")
    else:
        print("No response text collected!")


async def test_sdk_with_stderr():
    """Test SDK with stderr capture enabled."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
    )

    print("\n=== Testing SDK with stderr capture ===")

    stderr_lines = []
    def stderr_callback(line: str):
        stderr_lines.append(line)
        print(f"[STDERR] {line}")

    options = ClaudeAgentOptions(
        system_prompt="You are a helpful assistant.",
        cwd=str(project_root),
        permission_mode="bypassPermissions",
        max_turns=1,
        stderr=stderr_callback,
        extra_args={"debug-to-stderr": None},  # Enable debug mode
        env={
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
        },
    )

    response_parts = []

    try:
        async with ClaudeSDKClient(options) as client:
            await client.query("Say 'Test with debug!' and nothing else.")

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            response_parts.append(block.text)
                elif isinstance(msg, ResultMessage):
                    print(f"\nResult: is_error={msg.is_error}, result={repr(msg.result)}")

    except Exception as e:
        print(f"\n!!! EXCEPTION: {type(e).__name__}: {e}")

    print(f"\n=== Stderr captured: {len(stderr_lines)} lines ===")
    for line in stderr_lines[:20]:  # First 20 lines
        print(f"  {line}")
    if len(stderr_lines) > 20:
        print(f"  ... and {len(stderr_lines) - 20} more lines")


async def test_sdk_via_agent():
    """Test using the ValorAgent wrapper (same as bridge uses)."""
    from agent import ValorAgent

    print("\n=== Testing via ValorAgent wrapper ===")

    agent = ValorAgent(working_dir=project_root)

    try:
        response = await agent.query("Say 'Hello from ValorAgent!' and nothing else.")
        print(f"Response length: {len(response)} chars")
        print(f"Response: {response}")
    except Exception as e:
        print(f"\n!!! EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", choices=["direct", "stderr", "agent", "all"], default="all")
    args = parser.parse_args()

    if args.test in ("direct", "all"):
        asyncio.run(test_sdk_direct())

    if args.test in ("stderr", "all"):
        asyncio.run(test_sdk_with_stderr())

    if args.test in ("agent", "all"):
        asyncio.run(test_sdk_via_agent())
