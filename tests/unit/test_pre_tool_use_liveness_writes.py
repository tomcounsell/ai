"""Hooks write Pillar A liveness fields on every tool boundary (issue #1172).

The PreToolUse hook sets ``current_tool_name`` and bumps ``last_tool_use_at``
on the matching ``AgentSession`` (resolved via the ``AGENT_SESSION_ID`` env).
The PostToolUse hook clears ``current_tool_name`` and bumps the timestamp
again. Both writes are wrapped in try/except — Redis failures must NOT crash
the hook.

A per-session 5s in-memory cooldown bounds Redis write rate under tight tool
loops. The cooldown is best-effort: writes are coalesced, never reordered.
"""

from __future__ import annotations

import asyncio

import pytest

from models.agent_session import AgentSession, SessionType


@pytest.fixture
def liveness_session(monkeypatch):
    """Create a PM session and expose its session_id via AGENT_SESSION_ID."""
    s = AgentSession.create(
        project_key="test-liveness-hooks",
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"liveness-hooks-{id(monkeypatch)}",
        working_dir="/tmp",
    )
    monkeypatch.setenv("AGENT_SESSION_ID", s.session_id)
    yield s
    try:
        s.delete()
    except Exception:
        pass


def _reset_cooldown():
    from agent.hooks import liveness_writers

    liveness_writers._reset_cooldown_for_tests()


def test_pre_tool_use_sets_current_tool_name(liveness_session):
    from agent.hooks.pre_tool_use import pre_tool_use_hook

    _reset_cooldown()

    asyncio.run(
        pre_tool_use_hook(
            input_data={"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}},
            tool_use_id="tool-use-1",
            context=None,
        )
    )

    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert len(refreshed) == 1
    assert refreshed[0].current_tool_name == "Read"
    assert refreshed[0].last_tool_use_at is not None


def test_post_tool_use_clears_current_tool_name(liveness_session):
    from agent.hooks.post_tool_use import post_tool_use_hook
    from agent.hooks.pre_tool_use import pre_tool_use_hook

    _reset_cooldown()

    asyncio.run(
        pre_tool_use_hook(
            input_data={"tool_name": "Bash", "tool_input": {"command": "ls"}},
            tool_use_id="tool-use-2",
            context=None,
        )
    )
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name == "Bash"

    _reset_cooldown()
    asyncio.run(
        post_tool_use_hook(
            input_data={"tool_name": "Bash", "tool_input": {"command": "ls"}},
            tool_use_id="tool-use-2",
            context=None,
        )
    )
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name is None
    assert refreshed[0].last_tool_use_at is not None


def test_hook_silently_no_ops_without_agent_session_id(monkeypatch):
    """No AGENT_SESSION_ID env → write helper silently returns False."""
    from agent.hooks.liveness_writers import record_tool_boundary

    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    _reset_cooldown()
    # Should not raise even with no env var present.
    assert record_tool_boundary(tool_name="Read", clear=False) is False


def test_hook_redis_failure_does_not_crash(liveness_session, monkeypatch):
    """A Popoto/Redis save failure must not propagate out of the writer."""
    from agent.hooks import liveness_writers

    _reset_cooldown()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated Redis outage")

    monkeypatch.setattr(liveness_writers, "_save_tool_boundary", _boom)

    # Returns False but does not raise.
    assert liveness_writers.record_tool_boundary(tool_name="Edit", clear=False) is False


def test_clear_true_bypasses_cooldown_window(liveness_session):
    """Issue #1270: PostToolUse (``clear=True``) must NOT be coalesced by the
    cooldown — otherwise a fast PreToolUse->PostToolUse pair within the 5s window
    leaves ``current_tool_name`` populated and the per-tool timeout sub-loop
    sees a false-positive wedge condition.
    """
    from agent.hooks.liveness_writers import record_tool_boundary

    _reset_cooldown()

    # PreToolUse fires (clear=False) — sets current_tool_name=Read, pumps cooldown.
    assert record_tool_boundary(tool_name="Read", clear=False) is True
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name == "Read"

    # PostToolUse fires immediately (clear=True). Without the bypass, the 5s
    # cooldown would coalesce this into a no-op and current_tool_name would
    # stay populated. With the bypass, the field clears.
    assert record_tool_boundary(tool_name="Read", clear=True) is True
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name is None


def test_clear_false_still_respects_cooldown(liveness_session):
    """Companion to the above: rapid-fire PreToolUse calls remain coalesced."""
    from agent.hooks.liveness_writers import record_tool_boundary

    _reset_cooldown()

    # First PreToolUse fires.
    assert record_tool_boundary(tool_name="Read", clear=False) is True
    # Second PreToolUse within the cooldown window is suppressed.
    assert record_tool_boundary(tool_name="Edit", clear=False) is False
    # current_tool_name reflects only the first write.
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name == "Read"
