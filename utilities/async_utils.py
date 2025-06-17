"""
Async utility functions.
"""

import asyncio
from typing import Any, Coroutine


def anyio_run_sync(coro: Coroutine) -> Any:
    """
    Run an async coroutine synchronously.
    
    Wrapper around asyncio.run for compatibility.
    """
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            # We're already in an event loop, create a new task
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(coro)
        raise