#!/usr/bin/env python3
"""
Test the Valor delegation tool integration.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from agents.valor.agent import ValorContext, run_valor_agent
from tools.valor_delegation_tool import execute_valor_delegation


async def test_valor_delegation_tool_basic():
    """Test basic Valor delegation tool functionality."""
    print("🧪 Testing Valor Delegation Tool - Basic Functionality")
    print("=" * 50)

    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Testing in directory: {temp_dir}")

        # Simple prompt to create a file
        prompt = """
        Create a simple test file called 'hello.txt' with the content 'Hello from Claude Code!'
        Use the Write tool to create this file.
        """

        try:
            result = execute_valor_delegation(
                prompt=prompt,
                working_directory=temp_dir,
                allowed_tools=["Write", "Read", "LS"],
                timeout=30,
            )

            print("✅ Valor delegation execution completed")
            print(f"Result: {result[:200]}..." if len(result) > 200 else f"Result: {result}")

            # Check if file was created
            hello_file = Path(temp_dir) / "hello.txt"
            if hello_file.exists():
                content = hello_file.read_text()
                print(f"✅ File created with content: {content}")
            else:
                print("❌ File was not created")

        except Exception as e:
            print(f"❌ Test failed: {e}")


async def test_valor_agent_delegation():
    """Test Valor agent delegating tasks to specialized sessions."""
    print("\n🤖 Testing Valor Agent - Session Delegation")
    print("=" * 50)

    # Create context
    context = ValorContext(chat_id=12345, username="test_user")

    # Test query that should trigger Claude Code delegation
    query = """I need you to create a simple Python script in /tmp that prints 'Hello World'
    and saves the current timestamp to a file called 'timestamp.txt'.
    Use git to commit the changes."""

    try:
        print(f"Query: {query}")
        print("-" * 30)

        response = await run_valor_agent(query, context)
        print(f"Valor Response: {response}")

        if "Claude Code session completed" in response:
            print("✅ Successfully delegated to Claude Code")
        else:
            print("ℹ️  Response received (may not have triggered delegation)")

    except Exception as e:
        print(f"❌ Test failed: {e}")


async def test_spacing_change_revert():
    """Test creating a file with spacing issues that linter will fix."""
    print("\n🔧 Testing Linter Integration - Spacing Changes")
    print("=" * 50)

    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Testing in directory: {temp_dir}")

        # Create a Python file with deliberate spacing issues
        prompt = """
        Create a Python file called 'test_spacing.py' with the following content that has spacing issues:

        ```python
        def hello_world( ):
            print("Hello World")


        if __name__=="__main__":
            hello_world( )
        ```

        Then run 'ruff format test_spacing.py' to see the linter fix the spacing.
        """

        try:
            result = execute_valor_delegation(
                prompt=prompt,
                working_directory=temp_dir,
                allowed_tools=["Write", "Bash", "Read"],
                timeout=30,
            )

            print("✅ Valor delegation execution with linter test completed")
            print(f"Result preview: {result[:300]}...")

            # Check if file exists and has been formatted
            test_file = Path(temp_dir) / "test_spacing.py"
            if test_file.exists():
                content = test_file.read_text()
                print(f"✅ File created. Content length: {len(content)} chars")

                # Check if spacing was fixed (no space before parentheses)
                if "def hello_world():" in content and 'if __name__ == "__main__":' in content:
                    print("✅ Linter successfully fixed spacing issues")
                else:
                    print("ℹ️  File exists but spacing fixes unclear")
            else:
                print("❌ Test file was not created")

        except Exception as e:
            print(f"❌ Test failed: {e}")


async def main():
    """Run all tests."""
    print("🚀 Starting Valor Delegation Tool Tests")
    print("=" * 60)

    await test_valor_delegation_tool_basic()
    await test_valor_agent_delegation()
    await test_spacing_change_revert()

    print("\n" + "=" * 60)
    print("🏁 All tests completed!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user.")
    except Exception as e:
        print(f"\nTests failed: {e}")
