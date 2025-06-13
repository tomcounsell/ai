"""
Test runner tasks using the promise queue.

This module enables controlled test execution through the promise system,
preventing system overload by running tests asynchronously with resource limits.
"""
import json
import logging
import subprocess
from datetime import datetime
from typing import Dict, List, Optional

from huey import crontab
from .huey_config import huey
from utilities.database import get_database_connection, update_promise_status, get_promise
from tasks.promise_tasks import send_completion_notification

logger = logging.getLogger(__name__)


@huey.task(retries=1, retry_delay=120)
def execute_test_suite(promise_id: int) -> str:
    """
    Execute a test suite with resource limits.
    
    This task runs tests in a controlled manner to prevent system overload.
    Tests are run with timeout and memory limits.
    """
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    # Parse metadata for test configuration
    metadata = json.loads(promise.get('metadata') or '{}')
    test_files = metadata.get('test_files', [])
    test_pattern = metadata.get('test_pattern', 'test_*.py')
    max_workers = metadata.get('max_workers', 2)  # Limit parallel test execution
    timeout = metadata.get('timeout', 300)  # 5 minute default timeout
    
    # Mark as in progress
    update_promise_status(promise_id, 'in_progress')
    logger.info(f"Starting test suite execution for promise {promise_id}")
    
    results = []
    failed_tests = []
    
    try:
        if test_files:
            # Run specific test files
            for test_file in test_files:
                result = _run_single_test(test_file, timeout)
                results.append(result)
                if not result['success']:
                    failed_tests.append(test_file)
        else:
            # Run all tests matching pattern
            import glob
            test_files = glob.glob(f"tests/{test_pattern}")
            
            # Run tests in batches to control resource usage
            batch_size = max_workers
            for i in range(0, len(test_files), batch_size):
                batch = test_files[i:i+batch_size]
                for test_file in batch:
                    result = _run_single_test(test_file, timeout)
                    results.append(result)
                    if not result['success']:
                        failed_tests.append(test_file)
        
        # Compile summary
        total_tests = len(results)
        passed_tests = len([r for r in results if r['success']])
        
        summary = f"""Test Suite Completed

**Summary:**
- Total tests: {total_tests}
- Passed: {passed_tests}
- Failed: {len(failed_tests)}

**Execution Time:** {sum(r['duration'] for r in results):.2f} seconds
"""
        
        if failed_tests:
            summary += f"\n**Failed Tests:**\n"
            for test in failed_tests:
                summary += f"- {test}\n"
        
        # Update promise
        update_promise_status(promise_id, 'completed', result_summary=summary)
        
        # Send notification
        send_completion_notification.schedule(args=(promise_id, summary), delay=1)
        
        return summary
        
    except Exception as e:
        logger.error(f"Test suite execution failed: {str(e)}")
        error_msg = f"Test execution failed: {str(e)}"
        update_promise_status(promise_id, 'failed', error_message=error_msg)
        raise


def _run_single_test(test_file: str, timeout: int) -> Dict:
    """
    Run a single test file with resource limits.
    
    Returns:
        dict: Test result with success status, output, and duration
    """
    start_time = datetime.utcnow()
    
    try:
        # Run test with timeout
        cmd = ["python", "-m", "pytest", test_file, "-v", "--tb=short"]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        duration = (datetime.utcnow() - start_time).total_seconds()
        
        return {
            'test_file': test_file,
            'success': result.returncode == 0,
            'output': result.stdout[:1000],  # Limit output size
            'error': result.stderr[:1000] if result.stderr else None,
            'duration': duration
        }
        
    except subprocess.TimeoutExpired:
        duration = (datetime.utcnow() - start_time).total_seconds()
        return {
            'test_file': test_file,
            'success': False,
            'output': f"Test timed out after {timeout} seconds",
            'error': 'Timeout',
            'duration': duration
        }
    except Exception as e:
        duration = (datetime.utcnow() - start_time).total_seconds()
        return {
            'test_file': test_file,
            'success': False,
            'output': f"Test execution error: {str(e)}",
            'error': str(e),
            'duration': duration
        }


@huey.task(retries=1)
def execute_single_test(promise_id: int) -> str:
    """
    Execute a single test file.
    
    This is more lightweight than the full suite runner.
    """
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    metadata = json.loads(promise.get('metadata') or '{}')
    test_file = metadata.get('test_file')
    
    if not test_file:
        raise ValueError("No test file specified in metadata")
    
    # Mark as in progress
    update_promise_status(promise_id, 'in_progress')
    
    result = _run_single_test(test_file, timeout=300)
    
    if result['success']:
        summary = f"✅ Test passed: {test_file}\n\nDuration: {result['duration']:.2f}s"
        update_promise_status(promise_id, 'completed', result_summary=summary)
    else:
        summary = f"❌ Test failed: {test_file}\n\nError: {result['error']}\n\nOutput:\n{result['output']}"
        update_promise_status(promise_id, 'failed', error_message=summary)
    
    # Send notification
    send_completion_notification.schedule(args=(promise_id, summary), delay=1)
    
    return summary


# Helper function to create test promises
def schedule_test_run(
    chat_id: int,
    message_id: int,
    test_files: Optional[List[str]] = None,
    test_pattern: Optional[str] = None,
    max_workers: int = 2
) -> int:
    """
    Schedule a test run through the promise system.
    
    Args:
        chat_id: Telegram chat ID for notifications
        message_id: Original message ID
        test_files: Specific test files to run
        test_pattern: Pattern to match test files (e.g., 'test_agent_*.py')
        max_workers: Maximum parallel test execution
        
    Returns:
        int: Promise ID for tracking
    """
    from utilities.promise_manager_huey import HueyPromiseManager
    
    manager = HueyPromiseManager()
    
    if test_files:
        task_description = f"Run tests: {', '.join(test_files)}"
    elif test_pattern:
        task_description = f"Run tests matching: {test_pattern}"
    else:
        task_description = "Run all tests"
    
    metadata = {
        'test_files': test_files,
        'test_pattern': test_pattern,
        'max_workers': max_workers
    }
    
    # Create promise with special task type
    promise_id = manager.create_promise(
        chat_id=chat_id,
        message_id=message_id,
        task_description=task_description,
        task_type='analysis',  # Using analysis type for tests
        metadata=metadata
    )
    
    # Schedule execution
    execute_test_suite.schedule(args=(promise_id,), delay=1)
    
    return promise_id


@huey.task(retries=1)
def execute_test_suite_by_name(promise_id: int) -> str:
    """Execute specific test suite types (unit, integration, e2e, intent)."""
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    metadata = json.loads(promise.get('metadata') or '{}')
    suite_type = metadata.get('suite_type', 'unit')
    
    update_promise_status(promise_id, 'in_progress')
    
    if suite_type == 'unit':
        result = _run_unit_tests(promise_id, metadata)
    elif suite_type == 'e2e':
        result = _run_e2e_tests(promise_id, metadata)
    elif suite_type == 'intent':
        result = _run_intent_tests(promise_id, metadata)
    elif suite_type == 'integration':
        result = _run_integration_tests(promise_id, metadata)
    else:
        raise ValueError(f"Unknown suite type: {suite_type}")
    
    return result


@huey.task(retries=1, retry_delay=300)  # 5 min delay for OLLAMA cooldown
def execute_ollama_intensive_tests(promise_id: int) -> str:
    """Execute OLLAMA-heavy tests with rate limiting."""
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    metadata = json.loads(promise.get('metadata') or '{}')
    test_batch_size = metadata.get('batch_size', 3)  # Small batches for OLLAMA
    cooldown_delay = metadata.get('cooldown_delay', 10)  # Seconds between tests
    
    update_promise_status(promise_id, 'in_progress')
    
    # Run with OLLAMA rate limiting
    import time
    test_files = metadata.get('test_files', [])
    results = []
    
    for i, test_file in enumerate(test_files):
        result = _run_single_test(test_file, timeout=120)
        results.append(result)
        
        # Rate limiting for local OLLAMA
        if i < len(test_files) - 1:  # Don't delay after last test
            time.sleep(cooldown_delay)
    
    # Compile results
    passed = len([r for r in results if r['success']])
    summary = f"OLLAMA Tests: {passed}/{len(results)} passed"
    
    update_promise_status(promise_id, 'completed', result_summary=summary)
    send_completion_notification.schedule(args=(promise_id, summary), delay=1)
    
    return summary


@huey.periodic_task(crontab(hour='2', minute='0'))
def nightly_test_run():
    """
    Run a subset of critical tests nightly.
    
    This helps catch regressions without overloading the system.
    """
    critical_tests = [
        'tests/test_agent_quick.py',
        'tests/test_promise_simple.py',
        'tests/test_reaction_fix.py'
    ]
    
    # Create a system promise for nightly tests
    from utilities.database import create_promise
    
    promise_id = create_promise(
        chat_id=0,  # System promise
        message_id=0,
        task_description="Nightly critical test run"
    )
    
    # Update with metadata
    metadata = {
        'test_files': critical_tests,
        'max_workers': 1,  # Run sequentially at night
        'is_nightly': True
    }
    
    with get_database_connection(timeout=30) as conn:
        conn.execute(
            "UPDATE promises SET metadata = ? WHERE id = ?",
            (json.dumps(metadata), promise_id)
        )
        conn.commit()
    
    # Schedule execution
    execute_test_suite.schedule(args=(promise_id,), delay=0)
    
    logger.info(f"Scheduled nightly test run as promise {promise_id}")


def _run_unit_tests(promise_id: int, metadata: dict) -> str:
    """Run lightweight unit tests."""
    unit_tests = [
        'tests/test_agent_quick.py',
        'tests/test_file_reader.py', 
        'tests/test_expanded_emojis.py',
        'tests/test_message_validation_fixes.py'
    ]
    
    results = []
    for test_file in unit_tests:
        result = _run_single_test(test_file, timeout=60)
        results.append(result)
    
    passed = len([r for r in results if r['success']])
    summary = f"Unit Tests: {passed}/{len(results)} passed"
    
    update_promise_status(promise_id, 'completed', result_summary=summary)
    send_completion_notification.schedule(args=(promise_id, summary), delay=1)
    
    return summary


def _run_e2e_tests(promise_id: int, metadata: dict) -> str:
    """Run end-to-end tests using the E2E framework."""
    try:
        # Import E2E framework
        import subprocess
        import tempfile
        
        # Run E2E tests in isolated environment
        cmd = ["python", "tests/run_e2e_tests.py", "--quick"]
        
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for E2E
                cwd=temp_dir
            )
        
        success = result.returncode == 0
        summary = f"E2E Tests: {'PASSED' if success else 'FAILED'}"
        
        if success:
            update_promise_status(promise_id, 'completed', result_summary=summary)
        else:
            update_promise_status(promise_id, 'failed', error_message=result.stderr[:500])
        
        send_completion_notification.schedule(args=(promise_id, summary), delay=1)
        return summary
        
    except Exception as e:
        error_msg = f"E2E test execution failed: {str(e)}"
        update_promise_status(promise_id, 'failed', error_message=error_msg)
        return error_msg


def _run_intent_tests(promise_id: int, metadata: dict) -> str:
    """Run intent classification tests with OLLAMA rate limiting."""
    intent_tests = [
        'tests/test_reaction_fix.py',
        'tests/test_three_reaction_strategy.py'
    ]
    
    results = []
    import time
    
    for i, test_file in enumerate(intent_tests):
        result = _run_single_test(test_file, timeout=180)  # 3 min for OLLAMA tests
        results.append(result)
        
        # OLLAMA cooldown between tests
        if i < len(intent_tests) - 1:
            time.sleep(15)  # 15 second cooldown
    
    passed = len([r for r in results if r['success']])
    summary = f"Intent Tests: {passed}/{len(results)} passed (OLLAMA rate-limited)"
    
    update_promise_status(promise_id, 'completed', result_summary=summary)
    send_completion_notification.schedule(args=(promise_id, summary), delay=1)
    
    return summary


def _run_integration_tests(promise_id: int, metadata: dict) -> str:
    """Run integration tests (API-based, lightweight)."""
    integration_tests = [
        'tests/test_image_tools.py',  # Now lightweight/mocked
        'tests/test_search_current_info_comprehensive.py',  # Converted
        'tests/test_valor_delegation_tool.py'  # Converted
    ]
    
    results = []
    for test_file in integration_tests:
        result = _run_single_test(test_file, timeout=120)
        results.append(result)
    
    passed = len([r for r in results if r['success']])
    summary = f"Integration Tests: {passed}/{len(results)} passed"
    
    update_promise_status(promise_id, 'completed', result_summary=summary)
    send_completion_notification.schedule(args=(promise_id, summary), delay=1)
    
    return summary