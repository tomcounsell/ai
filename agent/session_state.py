"""Shared mutable session-tracking state for the worker — prevents circular imports between executor and health modules."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


# Callbacks registered by the bridge for sending messages and reactions
SendCallback = Callable[[str, str, int, Any], Awaitable[None]]  # (chat_id, text, reply_to, session)
ReactionCallback = Callable[[str, int, str | None], Awaitable[None]]
ResponseCallback = Callable[[object, str, str, int], Awaitable[None]]


@dataclass
class SessionHandle:
    """Per-session lookup handle for the two-tier no-progress detector (#1036).

    Populated at the top of ``_execute_agent_session`` and removed in its
    ``finally`` block. The ``task`` field is the ``asyncio.Task`` running the
    session-scoped SDK work so the health check can cancel it when both Tier 1
    heartbeats go stale AND all Tier 2 reprieve gates fail.

    ``task`` is ``None`` until ``BackgroundTask.run()`` creates the
    session-scoped task at ``agent/messenger.py:198``. Early setup code runs on
    the worker-loop task itself; cancelling that would tear down the entire
    worker (plan spike-1, #1039 review). Between registration and
    ``BackgroundTask.run()`` there is nothing session-scoped to cancel — the
    health check must check ``task is not None`` before calling ``.cancel()``.

    The ``pid`` field is populated by the messenger's ``on_sdk_started``
    callback once the SDK subprocess is spawned; it is used by the Tier 2
    process-alive / has-children gates via psutil.

    Lifecycle contract:
      * Single writer: ``_execute_agent_session`` for its own session id.
      * Multi reader: ``_agent_session_health_check`` and
        ``_tier2_reprieve_signal`` (both look up by id, tolerate missing).
      * ``_active_sessions.pop()`` happens in ``_execute_agent_session``'s
        ``finally`` block, always.
    """

    task: asyncio.Task | None = None
    pid: int | None = None


# Registry of in-flight session executions, keyed by agent_session_id.
#
# Written only by ``_execute_agent_session`` (register at entry before any
# raise site; pop in ``finally``). Read by the health check (to look up the
# cancellable task on kill) and by ``_tier2_reprieve_signal`` (to read the
# pid for Tier 2 gates).
_active_sessions: dict[str, SessionHandle] = {}

# Async worker tasks, keyed by worker_key.
_active_workers: dict[str, asyncio.Task] = {}

# Per-worker asyncio Events used to wake sleeping workers when new sessions arrive.
_active_events: dict[str, asyncio.Event] = {}

# Tracks worker_keys for which asyncio.create_task() has been called but the task
# has not yet registered itself in _active_workers (i.e., the spawn is in-flight).
# Since _ensure_worker() is synchronous (no await), check-and-set within the
# function is atomic in the cooperative event loop, preventing duplicate workers
# from being spawned when the health check iterates multiple pending sessions
# sharing the same worker_key before either task is live in _active_workers.
_starting_workers: set[str] = set()

# Global concurrency ceiling: limits total simultaneously executing sessions
# across all chat_ids. Initialized in _run_worker() before any worker loop
# starts, so it is always available when _worker_loop() first awaits it.
# None sentinel means no ceiling (pre-initialization or testing).
_global_session_semaphore: asyncio.Semaphore | None = None

# Graceful shutdown coordination: when set, worker loops finish their current
# session and exit instead of waiting for new work.
_shutdown_requested: bool = False

# Callbacks registered by the bridge for sending messages and reactions
_send_callbacks: dict[str | tuple[str, str], SendCallback] = {}
_reaction_callbacks: dict[str | tuple[str, str], ReactionCallback] = {}
_response_callbacks: dict[str | tuple[str, str], ResponseCallback] = {}
