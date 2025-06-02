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


async def test_guidance_response_format():
    """Test the new guidance response format after hanging fix."""
    print("\n🔍 Testing Guidance Response Format (Post-Hanging Fix)")
    print("=" * 50)

    from tools.valor_delegation_tool import spawn_valor_session

    try:
        # Test guidance response format
        result = spawn_valor_session(
            task_description="Fix authentication bug",
            target_directory="/tmp/test-project",
            specific_instructions="Use secure password hashing"
        )

        print("✅ Guidance response generated successfully")
        print(f"Response length: {len(result)} characters")

        # Validate guidance response structure
        required_elements = [
            "💡 **Development Guidance Available**",
            "For the task: **Fix authentication bug**",
            "Implementation Approach:",
            "Specific Help I Can Provide:",
            "Working Directory:",
            "What specific aspect would you like me to help you with first?"
        ]

        missing_elements = []
        for element in required_elements:
            if element not in result:
                missing_elements.append(element)

        if not missing_elements:
            print("✅ All required guidance elements present")
        else:
            print(f"❌ Missing guidance elements: {missing_elements}")

        # Validate no subprocess execution attempts
        subprocess_indicators = [
            "subprocess",
            "Claude Code execution",
            "session completed",
            "CalledProcessError"
        ]

        found_subprocess_refs = []
        for indicator in subprocess_indicators:
            if indicator.lower() in result.lower():
                found_subprocess_refs.append(indicator)

        if not found_subprocess_refs:
            print("✅ No subprocess execution references found (hanging fix validated)")
        else:
            print(f"⚠️ Found subprocess references: {found_subprocess_refs}")

        # Test with different task descriptions
        simple_task = spawn_valor_session("Add logging", "/tmp")
        complex_task = spawn_valor_session("Implement OAuth2 authentication with refresh tokens", "/tmp")

        if "Development Guidance Available" in simple_task and "Development Guidance Available" in complex_task:
            print("✅ Guidance format consistent across different task complexities")
        else:
            print("❌ Guidance format inconsistent")

    except Exception as e:
        print(f"❌ Test failed: {e}")


async def test_workspace_directory_resolution():
    """Test workspace directory resolution with different chat contexts."""
    print("\n🏢 Testing Workspace Directory Resolution")
    print("=" * 50)

    from agents.valor.agent import delegate_coding_task, ValorContext

    try:
        # Test group chat context
        group_context = ValorContext(
            chat_id=67890,
            username="test_user",
            is_group_chat=True
        )

        # Create mock RunContext for testing - simplified approach
        class MockRunContext:
            def __init__(self, deps):
                self.deps = deps
        
        group_run_context = MockRunContext(group_context)
        group_result = delegate_coding_task(
            group_run_context,
            "Create unit tests for authentication"
        )

        print("✅ Group chat delegation completed")
        print(f"Group result contains guidance: {'Development Guidance Available' in group_result}")

        # Test DM context
        dm_context = ValorContext(
            chat_id=12345,
            username="test_user",
            is_group_chat=False
        )
        
        dm_run_context = MockRunContext(dm_context)
        dm_result = delegate_coding_task(
            dm_run_context,
            "Refactor database models"
        )

        print("✅ DM delegation completed")
        print(f"DM result contains guidance: {'Development Guidance Available' in dm_result}")

        # Test no context
        no_context = ValorContext()
        
        no_context_run_context = MockRunContext(no_context)
        no_context_result = delegate_coding_task(
            no_context_run_context,
            "Update dependencies"
        )

        print("✅ No context delegation completed")
        print(f"No context result contains guidance: {'Development Guidance Available' in no_context_result}")

        # Validate all return guidance format
        all_results = [group_result, dm_result, no_context_result]
        guidance_count = sum(1 for result in all_results if "Development Guidance Available" in result)

        if guidance_count == len(all_results):
            print("✅ All context types return guidance format (hanging fix working)")
        else:
            print(f"❌ Only {guidance_count}/{len(all_results)} results returned guidance format")

    except Exception as e:
        print(f"❌ Test failed: {e}")


async def test_no_recursive_spawning():
    """Test that recursive Claude Code spawning is prevented."""
    print("\n🚫 Testing Recursive Spawning Prevention")
    print("=" * 50)

    from tools.valor_delegation_tool import spawn_valor_session

    try:
        # Test multiple rapid calls (should all return guidance, no hanging)
        tasks = [
            "Fix database connection",
            "Add user registration",
            "Implement API rate limiting",
            "Create Docker configuration",
            "Add comprehensive tests"
        ]

        results = []
        for i, task in enumerate(tasks):
            print(f"Testing task {i+1}/{len(tasks)}: {task}")
            result = spawn_valor_session(task, f"/tmp/test{i}")
            results.append(result)

        # Validate all return guidance (no hanging/subprocess execution)
        guidance_count = 0
        subprocess_count = 0

        for result in results:
            if "Development Guidance Available" in result:
                guidance_count += 1
            if any(indicator in result.lower() for indicator in ["subprocess", "claude code execution", "timeout"]):
                subprocess_count += 1

        print(f"✅ {guidance_count}/{len(tasks)} tasks returned guidance format")
        print(f"✅ {subprocess_count}/{len(tasks)} tasks showed subprocess indicators (should be 0)")

        if guidance_count == len(tasks) and subprocess_count == 0:
            print("✅ Recursive spawning prevention working correctly")
        else:
            print("❌ Recursive spawning prevention may have issues")

        # Test rapid sequential calls
        import time
        start_time = time.time()

        for _ in range(5):
            spawn_valor_session("Quick test task", "/tmp")

        end_time = time.time()
        execution_time = end_time - start_time

        print(f"✅ 5 rapid sequential calls completed in {execution_time:.2f}s")
        if execution_time < 1.0:  # Should be very fast since no subprocess
            print("✅ Response time indicates no subprocess execution (guidance only)")
        else:
            print("⚠️ Response time suggests possible subprocess execution")

    except Exception as e:
        print(f"❌ Test failed: {e}")


async def main():
    """Run all tests."""
    print("🚀 Starting Valor Delegation Tool Tests")
    print("=" * 60)

    await test_valor_delegation_tool_basic()
    await test_valor_agent_delegation()
    await test_spacing_change_revert()
    await test_guidance_response_format()
    await test_workspace_directory_resolution()
    await test_no_recursive_spawning()

    print("\n" + "=" * 60)
    print("🏁 All tests completed!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user.")
    except Exception as e:
        print(f"\nTests failed: {e}")
