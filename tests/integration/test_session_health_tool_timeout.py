"""Integration test for the per-tool timeout sub-loop (issue #1270).

End-to-end: a real ``AgentSession`` row in ``status="running"`` with a stale
``last_tool_use_at`` and non-null ``current_tool_name`` is recovered by one
tick of ``_agent_session_tool_timeout_check``. The session row's per-tier
counter should bump, the project-scoped Redis counter should INCR, and the
session should transition to ``pending`` (or ``failed`` once recovery_attempts
exceeds MAX_RECOVERY_ATTEMPTS).

This test uses real Popoto AgentSession rows (not SimpleNamespace mocks)
to verify the full read/save round-trip works end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent.session_health import (
    TOOL_TIMEOUT_DEFAULT_SEC,
    TOOL_TIMEOUT_INTERNAL_SEC,
    _agent_session_tool_timeout_check,
)
from models.agent_session import AgentSession, SessionType


@pytest.fixture
def wedged_session(monkeypatch):
    """Real AgentSession row that LOOKS wedged on a default-tier tool."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    monkeypatch.delenv("DISABLE_PROGRESS_KILL", raising=False)

    s = AgentSession.create(
        project_key=f"test-tool-timeout-{id(monkeypatch)}",
        chat_id="local-test-tool-timeout",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"tool-timeout-int-{id(monkeypatch)}",
        working_dir="/tmp",
    )
    # Bring the session to running with the wedge state set.
    s.status = "running"
    s.started_at = datetime.now(tz=UTC) - timedelta(seconds=600)
    s.current_tool_name = "Bash"
    s.last_tool_use_at = datetime.now(tz=UTC) - timedelta(seconds=TOOL_TIMEOUT_DEFAULT_SEC + 30)
    s.save(
        update_fields=[
            "status",
            "started_at",
            "current_tool_name",
            "last_tool_use_at",
        ]
    )

    yield s

    try:
        s.delete()
    except Exception:
        pass


async def test_subloop_recovers_default_tier_wedge_end_to_end(wedged_session):
    """One tick of the sub-loop on a real wedged session row.

    Asserts:
      - session transitions out of ``running``
      - ``tool_timeout_count_default`` bumps from 0 to 1
      - project Redis counter ``...:tool_timeouts:default`` >= 1
    """
    from popoto.redis_db import POPOTO_REDIS_DB as R

    counter_key = f"{wedged_session.project_key}:session-health:tool_timeouts:default"
    # Snapshot the counter (it might be 0 or pre-existing from prior test runs in
    # the same Redis db).
    try:
        before = int(R.get(counter_key) or 0)
    except Exception:
        before = 0

    await _agent_session_tool_timeout_check()

    # Re-read the session row from Popoto.
    refreshed = AgentSession.get_by_id(wedged_session.agent_session_id)
    assert refreshed is not None
    # Status moved off "running" — either "pending" (recovered) or "failed"
    # (MAX_RECOVERY_ATTEMPTS hit).
    assert refreshed.status != "running", (
        f"session still running after sub-loop tick (status={refreshed.status})"
    )
    # Per-tier counter bumped.
    assert refreshed.tool_timeout_count_default >= 1, (
        f"expected tool_timeout_count_default>=1, got {refreshed.tool_timeout_count_default}"
    )
    # Project Redis counter incremented.
    after = int(R.get(counter_key) or 0)
    assert after > before, (
        f"expected Redis counter {counter_key} to increase from {before}, got {after}"
    )

    # Cleanup the project-tier Redis counter so subsequent test runs start clean.
    try:
        R.delete(counter_key)
    except Exception:
        pass


async def test_subloop_kill_switch_skips_real_wedged_session(wedged_session, monkeypatch):
    """``TOOL_TIMEOUT_TIERS_DISABLED=1`` short-circuits even for a real wedge."""
    monkeypatch.setenv("TOOL_TIMEOUT_TIERS_DISABLED", "1")

    await _agent_session_tool_timeout_check()

    refreshed = AgentSession.get_by_id(wedged_session.agent_session_id)
    assert refreshed.status == "running", "kill switch should leave session untouched"
    assert refreshed.tool_timeout_count_default == 0


async def test_subloop_no_op_on_fresh_session(monkeypatch):
    """A session whose ``last_tool_use_at`` is fresh is left alone."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)

    s = AgentSession.create(
        project_key=f"test-tool-timeout-fresh-{id(monkeypatch)}",
        chat_id="local-test-tool-timeout-fresh",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"tool-timeout-fresh-{id(monkeypatch)}",
        working_dir="/tmp",
    )
    try:
        s.status = "running"
        s.started_at = datetime.now(tz=UTC) - timedelta(seconds=600)
        s.current_tool_name = "Read"
        # Fresh — well within the 30s internal budget.
        s.last_tool_use_at = datetime.now(tz=UTC) - timedelta(
            seconds=max(1, TOOL_TIMEOUT_INTERNAL_SEC // 2)
        )
        s.save(
            update_fields=[
                "status",
                "started_at",
                "current_tool_name",
                "last_tool_use_at",
            ]
        )

        await _agent_session_tool_timeout_check()

        refreshed = AgentSession.get_by_id(s.agent_session_id)
        assert refreshed.status == "running"
        assert refreshed.tool_timeout_count_internal == 0
    finally:
        try:
            s.delete()
        except Exception:
            pass
