"""Integration tests for BYOB scheduler-layer real-Chrome serialization.

Covers issue #1256, Decision 2 (rev4): the worker session-pick loop defers a
``requires_real_chrome=True`` candidate when another running session already
holds the real-Chrome slot. No file lock, no per-process collision guard --
purely scheduler-layer defer.

Tests use the ``redis_test_db`` fixture (autouse=True in conftest.py) for
isolation. Real Popoto + real Redis (test DB) -- no mocks.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from agent.agent_session_queue import _pop_agent_session
from agent.session_pickup import _real_chrome_slot_busy
from models.agent_session import AgentSession


def _create_test_session(**overrides) -> AgentSession:
    """Create an AgentSession with sensible defaults for these tests."""
    defaults = {
        "project_key": "test-byob",
        "status": "pending",
        "priority": "high",
        "created_at": time.time(),
        "session_id": f"test-byob-{int(time.time() * 1000000)}",
        "working_dir": "/tmp/test",
        "message_text": "test message",
        "sender_name": "Test",
        "chat_id": "123",
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


# ---------------------------------------------------------------------------
# Field round-trip
# ---------------------------------------------------------------------------


def test_field_round_trips_through_save_and_load():
    """The new requires_real_chrome field survives Popoto save() / get_by_id()."""
    session = _create_test_session(requires_real_chrome=True)
    loaded = AgentSession.get_by_id(session.id)
    assert loaded is not None
    # Popoto Field stores bools as strings: 'True' / 'False'. Both forms are
    # accepted to match how _truthy() in agent.session_pickup canonicalizes.
    assert loaded.requires_real_chrome in (True, "True")


def test_field_defaults_to_false_when_not_set():
    """Existing call sites that don't pass the field still work; default is False."""
    session = _create_test_session()  # no requires_real_chrome arg
    loaded = AgentSession.get_by_id(session.id)
    assert loaded is not None
    # Default value -- on a fresh in-memory instance Popoto returns the
    # Python default (False). After Redis round-trip it may return the
    # string "False". Both forms must be falsy through _truthy.
    assert loaded.requires_real_chrome in (False, "False")


# ---------------------------------------------------------------------------
# _real_chrome_slot_busy() helper
# ---------------------------------------------------------------------------


def test_slot_busy_false_when_no_real_chrome_session():
    """No requires_real_chrome session anywhere -> slot is free."""
    _create_test_session()  # ordinary session
    assert _real_chrome_slot_busy() is False


def test_slot_busy_false_when_real_chrome_session_is_pending():
    """Pending sessions don't hold the slot -- only running/active/dormant do."""
    _create_test_session(requires_real_chrome=True, status="pending")
    assert _real_chrome_slot_busy() is False


def test_slot_busy_true_when_real_chrome_session_running():
    """A running session with requires_real_chrome=True holds the slot."""
    _create_test_session(requires_real_chrome=True, status="running")
    assert _real_chrome_slot_busy() is True


def test_slot_busy_true_when_real_chrome_session_active():
    """An active session with requires_real_chrome=True holds the slot."""
    _create_test_session(requires_real_chrome=True, status="active")
    assert _real_chrome_slot_busy() is True


def test_slot_busy_true_when_real_chrome_session_dormant():
    """A dormant session still holds the slot -- it owns the Chrome tab."""
    _create_test_session(requires_real_chrome=True, status="dormant")
    assert _real_chrome_slot_busy() is True


def test_slot_busy_false_when_real_chrome_session_completed():
    """Completed sessions release the slot."""
    _create_test_session(requires_real_chrome=True, status="completed", completed_at=time.time())
    assert _real_chrome_slot_busy() is False


# ---------------------------------------------------------------------------
# Scheduler-layer serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_chrome_session_pops_when_slot_free():
    """A real-Chrome candidate pops normally when no other session holds the slot."""
    _create_test_session(
        requires_real_chrome=True,
        chat_id="123",
        message_text="needs real chrome",
    )
    popped = await _pop_agent_session("123")
    assert popped is not None
    assert popped.requires_real_chrome in (True, "True")
    assert popped.status == "running"


@pytest.mark.asyncio
async def test_real_chrome_session_defers_when_slot_busy():
    """A pending real-Chrome candidate is deferred when another holds the slot.

    Setup: one running session with requires_real_chrome=True (already
    holding the slot), and one pending session also requiring real Chrome.
    The scheduler must skip the pending one entirely; it stays pending.
    """
    # Holder: already running, holds the slot
    _create_test_session(
        requires_real_chrome=True,
        status="running",
        started_at=datetime.now(tz=UTC) - timedelta(seconds=30),
        chat_id="999",  # different chat so the pop loop sees only the candidate
        session_id="holder",
        message_text="holder",
    )
    # Candidate: pending, also wants real Chrome
    candidate = _create_test_session(
        requires_real_chrome=True,
        chat_id="123",
        session_id="candidate",
        message_text="candidate",
    )

    popped = await _pop_agent_session("123")
    # Must defer -- candidate stays pending, _pop returns None
    assert popped is None

    # Candidate is still pending after the defer
    reloaded = AgentSession.get_by_id(candidate.id)
    assert reloaded is not None
    assert reloaded.status == "pending"


@pytest.mark.asyncio
async def test_ordinary_session_not_blocked_by_real_chrome_session():
    """An ordinary (requires_real_chrome=False) candidate is not deferred.

    Setup: one running real-Chrome session, one pending ordinary session.
    The ordinary session must pop normally -- the gate only affects
    requires_real_chrome=True candidates.
    """
    # Holder: real-Chrome, running.
    _create_test_session(
        requires_real_chrome=True,
        status="running",
        started_at=datetime.now(tz=UTC) - timedelta(seconds=30),
        chat_id="999",
        session_id="holder-rc",
        message_text="holder",
    )
    # Candidate: ordinary work.
    _create_test_session(
        requires_real_chrome=False,
        chat_id="123",
        session_id="ordinary-candidate",
        message_text="ordinary",
    )

    popped = await _pop_agent_session("123")
    assert popped is not None
    assert popped.requires_real_chrome in (False, "False")
    assert popped.status == "running"


@pytest.mark.asyncio
async def test_real_chrome_session_pops_after_holder_completes():
    """Once the slot-holder finishes, a deferred candidate becomes eligible."""
    # Holder, running.
    holder = _create_test_session(
        requires_real_chrome=True,
        status="running",
        started_at=datetime.now(tz=UTC) - timedelta(seconds=30),
        chat_id="999",
        session_id="holder-then-done",
        message_text="holder",
    )
    candidate = _create_test_session(
        requires_real_chrome=True,
        chat_id="123",
        session_id="deferred",
        message_text="will pop later",
    )

    # Initial pop: deferred.
    popped = await _pop_agent_session("123")
    assert popped is None
    reloaded = AgentSession.get_by_id(candidate.id)
    assert reloaded is not None
    assert reloaded.status == "pending"

    # Holder completes.
    holder.status = "completed"
    holder.completed_at = time.time()
    holder.save()

    # Now the candidate should pop.
    popped2 = await _pop_agent_session("123")
    assert popped2 is not None
    assert popped2.session_id == "deferred"
    assert popped2.status == "running"
