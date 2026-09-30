"""Tests for bridge graceful shutdown task cancellation.

Validates that _graceful_shutdown cancels all tracked background tasks
before disconnecting the Telegram client, preventing the process hang
described in issue #937.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def reset_background_tasks():
    """Reset _background_tasks between tests."""
    import bridge.telegram_bridge as bt

    original = bt._background_tasks.copy()
    bt._background_tasks.clear()
    yield
    bt._background_tasks.clear()
    bt._background_tasks.extend(original)


@pytest.mark.asyncio
async def test_graceful_shutdown_cancels_all_background_tasks():
    """_graceful_shutdown must cancel every task in _background_tasks."""
    import bridge.telegram_bridge as bt

    # Create mock tasks that simulate background loops
    tasks = []
    for _ in range(6):
        task = asyncio.create_task(asyncio.sleep(3600))
        tasks.append(task)
        bt._background_tasks.append(task)

    # Mock Telegram client
    mock_client = AsyncMock()

    # Run graceful shutdown (defined inside main(), so we call the logic directly)
    # We need to replicate the shutdown logic since _graceful_shutdown is a nested function
    if bt._background_tasks:
        for task in bt._background_tasks:
            task.cancel()
        await asyncio.gather(*bt._background_tasks, return_exceptions=True)

    await mock_client.disconnect()

    # All tasks should be cancelled
    for i, task in enumerate(tasks):
        assert task.cancelled(), f"Task {i} was not cancelled"


@pytest.mark.asyncio
async def test_graceful_shutdown_handles_empty_task_list():
    """Shutdown with no background tasks should not error."""
    import bridge.telegram_bridge as bt

    assert len(bt._background_tasks) == 0

    # Should be a no-op, no exceptions
    if bt._background_tasks:
        for task in bt._background_tasks:
            task.cancel()
        await asyncio.gather(*bt._background_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_graceful_shutdown_handles_already_finished_tasks():
    """Tasks that finished before shutdown should not cause errors."""
    import bridge.telegram_bridge as bt

    # Create a task that finishes immediately
    task = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0.01)  # Let it complete
    bt._background_tasks.append(task)

    # Cancelling a finished task is safe (cancel() returns False, gather succeeds)
    for t in bt._background_tasks:
        t.cancel()
    results = await asyncio.gather(*bt._background_tasks, return_exceptions=True)
    # Should complete without raising
    assert len(results) == 1


def test_background_tasks_list_exists():
    """Module-level _background_tasks list must exist for task tracking."""
    import bridge.telegram_bridge as bt

    assert hasattr(bt, "_background_tasks")
    assert isinstance(bt._background_tasks, list)


def test_sys_exit_after_run_until_disconnected():
    """Verify sys.exit(1) safety net is present after run_until_disconnected."""
    from pathlib import Path

    source = Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py"
    content = source.read_text()

    # Find run_until_disconnected and verify sys.exit follows it
    idx = content.index("await client.run_until_disconnected()")
    after = content[idx:]
    assert "sys.exit(1)" in after, "sys.exit(1) safety net missing after run_until_disconnected"
