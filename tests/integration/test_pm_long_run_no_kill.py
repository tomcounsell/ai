"""Acceptance criterion (issue #1172): a PM session running 4+ hours with
active tool use and no result event is NOT killed.

The test simulates the long-running scenario by:
1. Creating an AgentSession with started_at = now - 4h.
2. Setting last_heartbeat_at = now - 30s (fresh heartbeat).
3. Setting last_tool_use_at = now - 30s (active tool use, no result event).
4. Running ``_agent_session_health_check`` and asserting the session
   remains in ``status="running"``.

This guards against any regression that re-introduces a wall-clock kill
or stdout/turn-staleness inference. Issue #1172 retired all such paths;
fresh heartbeats are sufficient evidence of progress regardless of
duration or stdout cadence.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from agent.agent_session_queue import (
    _active_workers,
    _agent_session_health_check,
)
from models.agent_session import AgentSession


@pytest.fixture(autouse=True)
def _cleanup_workers():
    yield
    for k in list(_active_workers.keys()):
        task = _active_workers.pop(k, None)
        if task and not task.done():
            try:
                task.cancel()
            except RuntimeError:
                pass
    try:
        for s in AgentSession.query.all():
            pk = getattr(s, "project_key", None)
            if isinstance(pk, str) and pk == "test-pm-long-run":
                try:
                    s.delete()
                except Exception:
                    pass
    except Exception:
        pass


def _make_long_running_session() -> AgentSession:
    return AgentSession.create(
        project_key="test-pm-long-run",
        chat_id="long-run-chat",
        status="running",
        priority="normal",
        created_at=time.time() - 4 * 3600,
        started_at=datetime.now(tz=UTC) - timedelta(hours=4),
        last_heartbeat_at=datetime.now(tz=UTC) - timedelta(seconds=30),
        last_sdk_heartbeat_at=datetime.now(tz=UTC) - timedelta(seconds=30),
        last_tool_use_at=datetime.now(tz=UTC) - timedelta(seconds=30),
        # No last_turn_at — never produced a result event.
        last_turn_at=None,
        # No stdout in the last 2 hours — would have tripped the deleted
        # STDOUT_FRESHNESS_WINDOW path.
        last_stdout_at=datetime.now(tz=UTC) - timedelta(hours=2),
        turn_count=12,
        log_path="/tmp/pm-long.log",
        claude_session_uuid="abc-uuid",
        session_id="pm-long-running-id",
        working_dir="/tmp/long",
        message_text="long-running PM session under test",
        sender_name="Test",
        telegram_message_id=1,
    )


@pytest.mark.asyncio
async def test_long_running_pm_with_fresh_heartbeat_is_not_killed():
    s = _make_long_running_session()

    # Worker is alive — fresh heartbeat is the dispositive evidence.
    live_task = asyncio.Future()
    _active_workers[s.worker_key] = live_task

    await _agent_session_health_check()

    running = AgentSession.query.filter(project_key="test-pm-long-run", status="running")
    assert len(running) == 1
    assert running[0].session_id == s.session_id

    pending = AgentSession.query.filter(project_key="test-pm-long-run", status="pending")
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_long_running_pm_with_only_sdk_heartbeat_fresh_is_not_killed():
    """Dual-heartbeat OR check: SDK heartbeat alone is enough (#1036)."""
    s = _make_long_running_session()
    s.last_heartbeat_at = datetime.now(tz=UTC) - timedelta(seconds=600)  # stale
    s.last_sdk_heartbeat_at = datetime.now(tz=UTC) - timedelta(seconds=30)  # fresh
    s.save(update_fields=["last_heartbeat_at", "last_sdk_heartbeat_at"])

    live_task = asyncio.Future()
    _active_workers[s.worker_key] = live_task

    await _agent_session_health_check()

    running = AgentSession.query.filter(project_key="test-pm-long-run", status="running")
    assert len(running) == 1
