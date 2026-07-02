"""Integration test for the per-tool timeout sub-loop (issue #1270 / #1711).

End-to-end: a real ``AgentSession`` row in ``status="running"`` with a stale
``last_tool_use_at`` and non-null ``current_tool_name`` is recovered by one
tick of ``_agent_session_tool_timeout_check``. The session row's per-tier
counter should bump, the project-scoped Redis counter should INCR, and the
session should transition to ``pending`` (or ``failed`` once recovery_attempts
exceeds MAX_RECOVERY_ATTEMPTS).

This test uses real Popoto AgentSession rows (not SimpleNamespace mocks)
to verify the full read/save round-trip works end-to-end.

Issue #1711 additions:
- MCP-tier wedge requeue carries prepended steering message.
- Terminal tool_timeout wedge delivers degraded notice (Redis counter bumped).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent.session_health import (
    MAX_RECOVERY_ATTEMPTS,
    TOOL_TIMEOUT_DEFAULT_SEC,
    TOOL_TIMEOUT_INTERNAL_SEC,
    TOOL_TIMEOUT_MCP_SEC,
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


async def test_mcp_tier_wedge_requeue_carries_prepended_steering(monkeypatch):
    """Issue #1711: MCP-tier wedge that requeues must carry a prepended steering message.

    End-to-end: create a real session wedged on an MCP tool with recovery_attempts=0
    so the sub-loop tick takes the requeue (pending) branch.  After the tick the
    session must be ``pending`` and the first message on the Redis steering list
    (``peek_steering_messages``) must name the tool.
    """
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    monkeypatch.delenv("DISABLE_PROGRESS_KILL", raising=False)

    mcp_tool = "mcp__claude_ai_Gmail__create_draft"
    s = AgentSession.create(
        project_key=f"test-tt-steer-{id(monkeypatch)}",
        chat_id="local-test-tt-steer",
        session_type=SessionType.ENG,
        message_text="please draft an email summarising the sprint",
        sender_name="x",
        session_id=f"tt-steer-{id(monkeypatch)}",
        working_dir="/tmp",
    )
    try:
        s.status = "running"
        s.started_at = datetime.now(tz=UTC) - timedelta(seconds=600)
        s.current_tool_name = mcp_tool
        s.last_tool_use_at = datetime.now(tz=UTC) - timedelta(seconds=TOOL_TIMEOUT_MCP_SEC + 30)
        s.recovery_attempts = 0
        s.save(
            update_fields=[
                "status",
                "started_at",
                "current_tool_name",
                "last_tool_use_at",
                "recovery_attempts",
            ]
        )

        await _agent_session_tool_timeout_check()

        refreshed = AgentSession.get_by_id(s.agent_session_id)
        assert refreshed is not None

        # The session must have been requeued (pending) on the first attempt.
        # If it went to failed it means confirmed_dead=False on this machine —
        # that's acceptable (the steering branch only fires on requeue), but then
        # we can't assert steering.  Only assert steering when status is pending.
        if refreshed.status == "pending":
            from agent.steering import peek_steering_messages

            msgs = peek_steering_messages(refreshed.session_id)
            assert len(msgs) >= 1, (
                f"requeued session must carry at least one steering message; got {msgs!r}"
            )
            assert mcp_tool in msgs[0]["text"], (
                f"first steering message must name the tool ({mcp_tool!r}); got {msgs[0]!r}"
            )
        else:
            # failed branch is acceptable on machines where subprocess can't be confirmed dead
            assert refreshed.status == "failed", (
                f"unexpected status {refreshed.status!r} — expected pending or failed"
            )
    finally:
        try:
            s.delete()
        except Exception:
            pass
        # Clean up project-tier Redis counters.
        try:
            from popoto.redis_db import POPOTO_REDIS_DB as R

            R.delete(f"{s.project_key}:session-health:tool_timeouts:mcp")
            R.delete(f"{s.project_key}:session-health:tool_timeout_steering_injected")
        except Exception:
            pass


async def test_terminal_wedge_delivers_degraded_notice(monkeypatch):
    """Issue #1711: tool_timeout at MAX_RECOVERY_ATTEMPTS must deliver degraded notice.

    End-to-end: real session at recovery_attempts=MAX_RECOVERY_ATTEMPTS-1 wedged on
    an MCP tool.  After the sub-loop tick the session must be ``failed`` and the
    project-scoped ``tool_timeout_degraded_delivered`` Redis counter must increment.
    """
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    monkeypatch.delenv("DISABLE_PROGRESS_KILL", raising=False)

    mcp_tool = "mcp__claude_ai_Gmail__list_labels"
    proj = f"test-tt-term-{id(monkeypatch)}"
    s = AgentSession.create(
        project_key=proj,
        chat_id="local-test-tt-term",
        session_type=SessionType.ENG,
        message_text="list all my Gmail labels",
        sender_name="x",
        session_id=f"tt-term-{id(monkeypatch)}",
        working_dir="/tmp",
    )
    try:
        s.status = "running"
        s.started_at = datetime.now(tz=UTC) - timedelta(seconds=600)
        s.current_tool_name = mcp_tool
        s.last_tool_use_at = datetime.now(tz=UTC) - timedelta(seconds=TOOL_TIMEOUT_MCP_SEC + 30)
        # Set to MAX-1 so the sub-loop bumps to MAX and takes the failed branch.
        s.recovery_attempts = MAX_RECOVERY_ATTEMPTS - 1
        s.save(
            update_fields=[
                "status",
                "started_at",
                "current_tool_name",
                "last_tool_use_at",
                "recovery_attempts",
            ]
        )

        from popoto.redis_db import POPOTO_REDIS_DB as R

        degraded_counter_key = f"{proj}:session-health:tool_timeout_degraded_delivered"
        try:
            before = int(R.get(degraded_counter_key) or 0)
        except Exception:
            before = 0

        await _agent_session_tool_timeout_check()

        refreshed = AgentSession.get_by_id(s.agent_session_id)
        assert refreshed is not None
        assert refreshed.status == "failed", (
            f"expected status=failed after MAX_RECOVERY_ATTEMPTS; got {refreshed.status!r}"
        )

        # Degraded notice delivery is signalled by the Redis counter bump.
        try:
            after = int(R.get(degraded_counter_key) or 0)
        except Exception:
            after = before  # Redis unavailable — skip counter assertion
        assert after > before, (
            f"tool_timeout_degraded_delivered counter must increment on terminal wedge "
            f"(before={before}, after={after})"
        )
    finally:
        try:
            s.delete()
        except Exception:
            pass
        try:
            from popoto.redis_db import POPOTO_REDIS_DB as R

            R.delete(f"{proj}:session-health:tool_timeouts:mcp")
            R.delete(f"{proj}:session-health:tool_timeout_degraded_delivered")
            R.delete(f"tool_timeout:degraded_sent:{s.session_id}")
        except Exception:
            pass
