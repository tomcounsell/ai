"""Unit tests for the at-rest owed-communication health check (durability plan #2494).

A session is "at rest with owed communication" when it has NO live fenced
execution AND its last activity is newer than its last user-facing **authored**
message by more than ``AT_REST_OWED_GRACE_SECONDS``: it did work after it last
spoke, then went idle without reporting back to the human.

The pathology this guards against (2026-07-30 reference incident):

    authored  14:32:49   (last [/user] or [/complete] the session emitted)
    activity  14:41:16   (last stdout / tool-use / turn — 507s AFTER authorship)
    delivered 14:42:49   (response_delivered_at stamp)

Anchoring on **authorship** fires on the 507s gap. Anchoring on **delivery**
would compute ``activity - delivered`` = a negative gap and stay silent, missing
the exact incident the check exists to catch.

Coverage:
  - test_fires_on_authorship_anchor_replaying_reference_incident (a)
  - test_does_not_fire_when_anchored_on_delivery_timestamp (b)
  - test_periodic_sweep_invokes_the_check (c) — no correct-logic-dead-caller
  - test_grace_window_boundary (d)
  - live-fence suppression + scalar-first-over-events-fallback
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.session_health import (
    AT_REST_OWED_GRACE_SECONDS,
    _agent_session_health_check,
    _at_rest_authored_anchor,
    _check_at_rest_owed_communication,
    _evaluate_at_rest_owed_communication,
)

# 2026-07-30 reference-incident wall-clock timestamps.
_AUTHORED = datetime(2026, 7, 30, 14, 32, 49, tzinfo=UTC)
_ACTIVITY = datetime(2026, 7, 30, 14, 41, 16, tzinfo=UTC)
_DELIVERED = datetime(2026, 7, 30, 14, 42, 49, tzinfo=UTC)
# Documented incident gap: activity is 507s after authorship.
_INCIDENT_GAP_S = (_ACTIVITY - _AUTHORED).total_seconds()


def _entry(
    *,
    last_authored_at=None,
    last_stdout_at=None,
    last_tool_use_at=None,
    last_turn_at=None,
    live_fence=None,
    session_events=None,
    status="running",
    sid="test-at-rest",
    project_key="test-at-rest-owed",
):
    """A stand-in AgentSession row for the pure predicate (SimpleNamespace)."""
    return SimpleNamespace(
        agent_session_id=sid,
        id=sid,
        status=status,
        project_key=project_key,
        last_authored_at=last_authored_at,
        last_stdout_at=last_stdout_at,
        last_tool_use_at=last_tool_use_at,
        last_turn_at=last_turn_at,
        live_fence=live_fence,
        session_events=session_events,
    )


def test_incident_gap_is_507_seconds():
    """Guard the fixture: the reference incident's activity-after-authorship gap."""
    assert _INCIDENT_GAP_S == 507.0


# --- (a) fires on the authorship anchor -------------------------------------


def test_fires_on_authorship_anchor_replaying_reference_incident():
    entry = _entry(
        last_authored_at=_AUTHORED,
        last_stdout_at=_ACTIVITY,
        live_fence=None,  # no live fenced execution — at rest
    )
    owed, gap = _evaluate_at_rest_owed_communication(entry)
    assert owed is True
    assert gap == pytest.approx(507.0)


# --- (b) does NOT fire when (wrongly) anchored on the delivery timestamp -----


def test_does_not_fire_when_anchored_on_delivery_timestamp():
    """If the authorship anchor were the DELIVERY stamp, the gap goes negative
    (activity precedes delivery) and the check would stay silent — the exact
    miss this design avoids. Modeled by setting the authored anchor to the
    delivery time."""
    entry = _entry(
        last_authored_at=_DELIVERED,  # pretend authorship == delivery
        last_stdout_at=_ACTIVITY,
        live_fence=None,
    )
    owed, gap = _evaluate_at_rest_owed_communication(entry)
    assert owed is False
    assert gap is not None and gap < 0


# --- (c) the periodic sweep actually invokes the check ----------------------


def test_periodic_sweep_invokes_the_check():
    """No correct-logic-dead-caller: ``_agent_session_health_check`` must await
    ``_check_at_rest_owed_communication`` on every tick."""

    def _empty_filter(*args, **kwargs):
        return iter([])

    fake_query = SimpleNamespace(filter=_empty_filter, all=lambda: iter([]))
    spy = AsyncMock(return_value=0)

    with (
        patch("agent.session_health.AgentSession.query", fake_query),
        patch("agent.session_health.AgentSession.get_by_id", side_effect=lambda _sid: None),
        patch("agent.session_health._filter_hydrated_sessions", side_effect=lambda xs: list(xs)),
        patch("agent.session_health._check_at_rest_owed_communication", spy),
    ):
        asyncio.run(_agent_session_health_check())

    spy.assert_awaited_once()


# --- (d) grace-window boundary ----------------------------------------------


def test_grace_window_boundary():
    now_anchor = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

    # Exactly at the grace window: strict ``>`` → NOT owed.
    at_boundary = _entry(
        last_authored_at=now_anchor,
        last_stdout_at=now_anchor + timedelta(seconds=AT_REST_OWED_GRACE_SECONDS),
        live_fence=None,
    )
    owed_at, _ = _evaluate_at_rest_owed_communication(at_boundary)
    assert owed_at is False

    # One second past the grace window → owed.
    past_boundary = _entry(
        last_authored_at=now_anchor,
        last_stdout_at=now_anchor + timedelta(seconds=AT_REST_OWED_GRACE_SECONDS + 1),
        live_fence=None,
    )
    owed_past, _ = _evaluate_at_rest_owed_communication(past_boundary)
    assert owed_past is True


# --- live-fence suppression -------------------------------------------------


def test_live_fenced_execution_is_never_owed():
    """A session with a live process is doing work now — never flagged, even with
    a wide activity-after-authorship gap."""
    entry = _entry(
        last_authored_at=_AUTHORED,
        last_stdout_at=_ACTIVITY,
        live_fence={"pid": 4242, "create_time": 111.0},
    )
    with patch("agent.pid_fence.fence_is_live", side_effect=lambda pid, ct, **kw: True):
        owed, gap = _evaluate_at_rest_owed_communication(entry)
    assert owed is False
    assert gap is None


# --- authorship anchor: scalar first, events-scan fallback ------------------


def test_authored_anchor_prefers_scalar_over_events():
    """The ``last_authored_at`` scalar wins over the events list (it survives
    independent of the trimmable events record)."""
    entry = _entry(
        last_authored_at=_AUTHORED,
        session_events=[
            {"type": "runner_complete_routed", "ts": _DELIVERED.isoformat()},
        ],
    )
    assert _at_rest_authored_anchor(entry) == pytest.approx(_AUTHORED.timestamp())


def test_authored_anchor_falls_back_to_events_scan_when_scalar_absent():
    """Legacy rows (no scalar) fall back to scanning session_events for the
    latest authored (runner_user_routed / runner_complete_routed) entry."""
    earlier = _AUTHORED
    later = _AUTHORED + timedelta(seconds=60)
    entry = _entry(
        last_authored_at=None,
        session_events=[
            {"type": "runner_user_routed", "ts": earlier.isoformat()},
            {"event_type": "runner_complete_routed", "ts": later.isoformat()},
            {"type": "some_other_event", "ts": _DELIVERED.isoformat()},
        ],
    )
    assert _at_rest_authored_anchor(entry) == pytest.approx(later.timestamp())


@pytest.mark.asyncio
async def test_check_flags_owed_session_end_to_end():
    """The sweep-level check counts a genuinely owed at-rest session."""
    owed_entry = _entry(
        last_authored_at=_AUTHORED,
        last_stdout_at=_ACTIVITY,
        live_fence=None,
        status="running",
    )

    def _filter(*, status):
        return iter([owed_entry]) if status == "running" else iter([])

    fake_query = SimpleNamespace(filter=_filter)

    with (
        patch("agent.session_health.AgentSession.query", fake_query),
        patch("agent.session_health._filter_hydrated_sessions", side_effect=lambda xs: list(xs)),
    ):
        flagged = await _check_at_rest_owed_communication()

    assert flagged == 1
