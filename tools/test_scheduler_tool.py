"""
Test Scheduler Tool - Schedule test runs through the promise queue.

This tool allows scheduling test execution as background tasks,
preventing system overload from running all tests at once.
"""
from typing import List, Optional
from tasks.test_runner_tasks import schedule_test_run


def schedule_tests(
    test_specification: str,
    chat_id: int,
    message_id: int = 0
) -> str:
    """
    Schedule tests to run in the background through the promise queue.
    
    Args:
        test_specification: Description of which tests to run
        chat_id: Telegram chat ID for notifications
        message_id: Original message ID (optional)
        
    Returns:
        str: Confirmation message with promise ID
    """
    # Parse test specification
    test_files = None
    test_pattern = None
    
    # Common test patterns
    if "quick" in test_specification.lower():
        test_files = ["tests/test_agent_quick.py"]
    elif "promise" in test_specification.lower():
        test_files = ["tests/test_promise_system.py"]
    elif "telegram" in test_specification.lower():
        test_pattern = "test_telegram_*.py"
    elif "agent" in test_specification.lower():
        test_pattern = "test_agent_*.py"
    elif "integration" in test_specification.lower():
        test_pattern = "test_*integration*.py"
    elif "all" in test_specification.lower():
        # Run all tests but with strict limits
        test_pattern = "test_*.py"
    else:
        # Try to parse specific file names
        import re
        file_match = re.findall(r'test_\w+\.py', test_specification)
        if file_match:
            test_files = [f"tests/{f}" for f in file_match]
        else:
            # Default to quick tests
            test_files = ["tests/test_agent_quick.py"]
    
    # Schedule the test run
    promise_id = schedule_test_run(
        chat_id=chat_id,
        message_id=message_id,
        test_files=test_files,
        test_pattern=test_pattern,
        max_workers=2  # Limit parallel execution
    )
    
    # Build confirmation message
    if test_files:
        test_desc = f"test files: {', '.join(test_files)}"
    else:
        test_desc = f"tests matching pattern: {test_pattern}"
    
    return f"""✅ **Test Run Scheduled**

I've scheduled your test run as promise #{promise_id}.

**Tests to run**: {test_desc}
**Execution**: Running in background with resource limits

I'll notify you when the tests complete with a summary of results.

_Note: Tests are run with timeouts and parallel limits to prevent system overload._"""


def get_test_suggestions() -> str:
    """
    Get suggestions for available test commands.
    
    Returns:
        str: Formatted list of test options
    """
    return """📋 **Available Test Options**

You can ask me to run tests with these specifications:

**Quick Tests:**
- "Run quick tests" - Runs test_agent_quick.py
- "Test promises" - Runs promise system tests
- "Test telegram" - Runs all Telegram integration tests

**Category Tests:**
- "Test agents" - Runs all agent tests
- "Test integration" - Runs all integration tests

**Specific Tests:**
- "Run test_agent_demo.py" - Run a specific test file
- "Run test_promise_system.py and test_telegram_ping_health.py" - Multiple files

**Full Suite:**
- "Run all tests" - Runs entire test suite (with limits)

All tests are executed in the background with resource controls to prevent system overload."""