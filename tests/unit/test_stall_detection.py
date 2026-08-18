"""Unit tests for stall detection in monitoring/session_watchdog.py.

Tests check_stalled_sessions (detection) and fix_unhealthy_session (abandon).
The old stall retry mechanisms (_recover_stalled_pending, _kill_stalled_worker,
_enqueue_stall_retry) were deleted in the bridge-resilience refactor.
Recovery is now handled by the unified _agent_session_health_check in agent/agent_session_queue.py.

Also covers the session liveness tick counter (#2716) that replaced the dead
⏳ stall reaction: tick derivation, the ceiling, re-anchoring, reaction-slot
precedence at the relay drain, and payload schema parity across every
hand-mirrored outbox writer.
"""

import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitoring.session_watchdog import (
    STALL_THRESHOLD_ACTIVE,
    STALL_THRESHOLD_PENDING,
    STALL_THRESHOLD_RUNNING,
    STALL_THRESHOLDS,
    _to_timestamp,
    check_stalled_sessions,
    fix_unhealthy_session,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_session(
    session_id="test-stall-001",
    status="active",
    started_at="DEFAULT",
    created_at="DEFAULT",
    updated_at="DEFAULT",
    project_key="test",
    chat_id="12345",
    agent_session_id="session-001",
    history=None,
):
    now = time.time()
    ns = SimpleNamespace(
        session_id=session_id,
        agent_session_id=agent_session_id,
        status=status,
        started_at=now - 60 if started_at == "DEFAULT" else started_at,
        created_at=now - 120 if created_at == "DEFAULT" else created_at,
        updated_at=now if updated_at == "DEFAULT" else updated_at,
        project_key=project_key,
        chat_id=chat_id,
    )
    _history = history or []
    ns._get_history_list = lambda: _history
    ns.log_lifecycle_transition = MagicMock()
    ns.save = MagicMock()
    ns.delete = MagicMock()
    return ns


def _mock_query_for_sessions(sessions_by_status):
    def filter_fn(**kwargs):
        status = kwargs.get("status", "")
        return sessions_by_status.get(status, [])

    return SimpleNamespace(filter=filter_fn)


def _stalled_session_ids(result):
    return [s["session_id"] for s in result]


# ===================================================================
# Constants
# ===================================================================


class TestStallConstants:
    def test_pending_threshold(self):
        assert STALL_THRESHOLD_PENDING == 300

    def test_running_threshold(self):
        assert STALL_THRESHOLD_RUNNING == 2700

    def test_active_threshold(self):
        assert STALL_THRESHOLD_ACTIVE == 600

    def test_stall_thresholds_dict(self):
        assert STALL_THRESHOLDS == {
            "pending": 300,
            "running": 2700,
            "active": 600,
        }


# ===================================================================
# check_stalled_sessions
# ===================================================================


class TestCheckStalledSessions:
    def test_no_sessions_returns_empty(self):
        mock_query = _mock_query_for_sessions({})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_healthy_pending_not_stalled(self):
        now = time.time()
        session = _make_agent_session(
            status="pending",
            created_at=now - 60,
            started_at=None,
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_stalled_pending_detected(self):
        now = time.time()
        session = _make_agent_session(
            session_id="stalled-pending",
            status="pending",
            created_at=now - (STALL_THRESHOLD_PENDING + 60),
            started_at=None,
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert "stalled-pending" in _stalled_session_ids(result)

    def test_stalled_running_detected(self):
        now = time.time()
        session = _make_agent_session(
            session_id="stalled-running",
            status="running",
            started_at=now - (STALL_THRESHOLD_RUNNING + 60),
            created_at=now - (STALL_THRESHOLD_RUNNING + 120),
        )
        mock_query = _mock_query_for_sessions({"running": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert "stalled-running" in _stalled_session_ids(result)

    def test_stalled_active_no_recent_activity(self):
        now = time.time()
        session = _make_agent_session(
            session_id="stalled-active",
            status="active",
            updated_at=now - (STALL_THRESHOLD_ACTIVE + 60),
            started_at=now - 3600,
        )
        mock_query = _mock_query_for_sessions({"active": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert "stalled-active" in _stalled_session_ids(result)

    def test_active_with_recent_activity_not_stalled(self):
        now = time.time()
        session = _make_agent_session(
            status="active",
            updated_at=now - 30,
            started_at=now - 3600,
        )
        mock_query = _mock_query_for_sessions({"active": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_query_exception_returns_empty(self):
        mock_query = MagicMock()
        mock_query.filter.side_effect = Exception("Redis down")
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []


# ===================================================================
# fix_unhealthy_session (simplified — no retry, just abandon)
# ===================================================================


class TestFixUnhealthySession:
    @pytest.mark.asyncio
    async def test_silent_session_abandoned(self):
        """Silent sessions are abandoned directly (no retry mechanism)."""
        now = time.time()
        session = _make_agent_session(
            session_id="abandon-test",
            status="active",
            updated_at=now - 2000,
            started_at=now - 3000,
        )
        assessment = {
            "healthy": False,
            "issues": ["Silent for 33 minutes"],
            "severity": "warning",
        }

        with patch(
            "monitoring.session_watchdog._safe_abandon_session",
            return_value=True,
        ) as mock_abandon:
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_abandon.assert_called_once()

    @pytest.mark.asyncio
    async def test_critical_issues_abandoned_with_issue(self):
        """Critical issues are abandoned and a GitHub issue is created."""
        now = time.time()
        session = _make_agent_session(
            session_id="critical-test",
            status="active",
            updated_at=now - 100,
            started_at=now - 500,
        )
        assessment = {
            "healthy": False,
            "issues": [
                "Looping: Bash called 5 times",
                "Error cascade: 5 errors",
            ],
            "severity": "critical",
        }

        with (
            patch("monitoring.session_watchdog._safe_abandon_session") as mock_abandon,
            patch(
                "monitoring.session_watchdog.create_session_issue",
                new_callable=AsyncMock,
            ) as mock_issue,
        ):
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_abandon.assert_called_once()
            mock_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_long_running_session_abandoned(self):
        """Long-running sessions (>2h) are abandoned."""
        now = time.time()
        session = _make_agent_session(
            session_id="long-test",
            status="active",
            updated_at=now - 100,  # Recent activity
            started_at=now - 8000,  # >2 hours
        )
        assessment = {
            "healthy": False,
            "issues": ["Running for 2 hours"],
            "severity": "warning",
        }

        with patch(
            "monitoring.session_watchdog._safe_abandon_session",
            return_value=True,
        ) as mock_abandon:
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_abandon.assert_called_once()


# ===================================================================
# _to_timestamp — UTC fix for naive datetimes (issue #777)
# ===================================================================


class TestToTimestamp:
    def test_none_returns_none(self):
        assert _to_timestamp(None) is None

    def test_float_passthrough(self):
        ts = time.time()
        assert _to_timestamp(ts) == ts

    def test_int_passthrough(self):
        assert _to_timestamp(1234567890) == 1234567890.0

    def test_aware_datetime_returns_correct_timestamp(self):
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert _to_timestamp(aware) == aware.timestamp()

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime (as returned by Popoto SortedField) must be
        treated as UTC, not local time.  On a UTC+7 machine, the old code
        would inflate the timestamp by 25200 seconds; after the fix both
        forms must agree within 1 second of each other."""
        naive = datetime.utcnow()
        aware = datetime.now(tz=UTC)
        assert abs(_to_timestamp(naive) - _to_timestamp(aware)) < 1.0

    def test_naive_matches_aware_explicit_value(self):
        """Verify with a fixed timestamp to rule out timing jitter."""
        naive = datetime(2026, 4, 7, 10, 0, 0)
        aware = datetime(2026, 4, 7, 10, 0, 0, tzinfo=UTC)
        assert _to_timestamp(naive) == _to_timestamp(aware)

    def test_unrecognized_type_returns_none(self):
        assert _to_timestamp("not-a-datetime") is None


# ===================================================================
# Liveness tick counter (issue #2716)
# ===================================================================


def _make_tick_session(
    session_id="tg_user_-100_42",
    chat_id="-100",
    telegram_message_id=42,
    agent_session_id="as-001",
    age_seconds=0.0,
):
    """SimpleNamespace mimicking an AgentSession with a Telegram origin."""
    now = time.time()
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=agent_session_id,
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
        status="running",
        started_at=now - age_seconds,
        created_at=now - age_seconds,
        updated_at=now - age_seconds,
        project_key="testproj",
    )


class _FakeRedis:
    """In-memory stub for the get/set-NX-EX/rpush/expire/delete surface used
    by the tick publisher and bridge/liveness_ticks.py."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def expire(self, key, ttl):
        self.expires[key] = ttl
        return True

    def delete(self, key):
        self.store.pop(key, None)
        self.lists.pop(key, None)
        self.expires.pop(key, None)
        return 1


@pytest.fixture
def fake_redis(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", fr)
    return fr


def _run_publisher(sessions_by_status):
    from monitoring import session_watchdog

    def filter_fn(**kwargs):
        return sessions_by_status.get(kwargs.get("status", ""), [])

    with patch(
        "monitoring.session_watchdog.AgentSession.query",
        SimpleNamespace(filter=filter_fn),
    ):
        return session_watchdog._publish_liveness_ticks()


class TestLivenessTickPublisher:
    def test_fresh_session_publishes_nothing(self, fake_redis):
        """Slot 0 is the 👀 ingestion already placed; the counter starts at tick 1."""
        session = _make_tick_session(age_seconds=0)
        assert _run_publisher({"running": [session]}) == 0
        assert fake_redis.lists == {}

    def test_first_interval_publishes_tick_one(self, fake_redis):
        from bridge.response import HEARTBEAT_TICK_INTERVAL_SECONDS, heartbeat_reaction

        session = _make_tick_session(age_seconds=HEARTBEAT_TICK_INTERVAL_SECONDS + 5)
        assert _run_publisher({"running": [session]}) == 1

        payload = json.loads(fake_redis.lists["telegram:outbox:tg_user_-100_42"][0])
        assert payload["type"] == "reaction"
        assert payload["chat_id"] == "-100"
        assert payload["reply_to"] == 42
        assert payload["emoji"] == heartbeat_reaction(1).emoji
        assert payload["heartbeat_tick"] == 1
        assert fake_redis.expires["telegram:outbox:tg_user_-100_42"] == 3600

    def test_tick_is_derived_from_wall_clock_not_incremented(self, fake_redis):
        """WATCHDOG_INTERVAL (300s) is half a tick — a rescan must recompute, not add."""
        from bridge.response import HEARTBEAT_TICK_INTERVAL_SECONDS

        session = _make_tick_session(age_seconds=HEARTBEAT_TICK_INTERVAL_SECONDS * 3 + 5)
        assert _run_publisher({"running": [session]}) == 1
        payload = json.loads(fake_redis.lists["telegram:outbox:tg_user_-100_42"][0])
        assert payload["heartbeat_tick"] == 3

    def test_second_scan_within_the_same_interval_is_a_noop(self, fake_redis):
        from bridge.response import HEARTBEAT_TICK_INTERVAL_SECONDS

        session = _make_tick_session(age_seconds=HEARTBEAT_TICK_INTERVAL_SECONDS + 5)
        _run_publisher({"running": [session]})
        assert _run_publisher({"running": [session]}) == 0
        assert len(fake_redis.lists["telegram:outbox:tg_user_-100_42"]) == 1

    def test_tick_priority_is_explicit_and_lowest(self, fake_redis):
        from agent.reaction_priority import PRIORITY_HEARTBEAT, priority_for_glyph
        from bridge.response import HEARTBEAT_TICK_INTERVAL_SECONDS

        session = _make_tick_session(age_seconds=HEARTBEAT_TICK_INTERVAL_SECONDS + 5)
        _run_publisher({"running": [session]})
        payload = json.loads(fake_redis.lists["telegram:outbox:tg_user_-100_42"][0])
        assert payload["priority"] == PRIORITY_HEARTBEAT
        # And not the glyph derivation, which would misrank the slot-0 arc entry.
        assert payload["priority"] != priority_for_glyph("👀")

    def test_running_status_is_swept(self):
        """The load-bearing case: check_all_sessions queries active only, and
        worker-executed Telegram sessions live at running."""
        from monitoring.session_watchdog import HEARTBEAT_STATUSES

        assert "running" in HEARTBEAT_STATUSES

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"chat_id": None},
            {"telegram_message_id": None},
            {"telegram_message_id": 0},
            {"session_id": "", "agent_session_id": None},
        ],
    )
    def test_non_telegram_sessions_are_skipped_silently(self, fake_redis, kwargs):
        from bridge.response import HEARTBEAT_TICK_INTERVAL_SECONDS

        session = _make_tick_session(age_seconds=HEARTBEAT_TICK_INTERVAL_SECONDS + 5, **kwargs)
        assert _run_publisher({"running": [session]}) == 0
        assert fake_redis.store == {}
        assert fake_redis.lists == {}

    def test_redis_failure_is_fail_quiet(self, monkeypatch):
        class _BoomRedis:
            def __getattr__(self, _name):
                def _boom(*a, **kw):
                    raise RuntimeError("redis down")

                return _boom

        monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", _BoomRedis())
        from bridge.response import HEARTBEAT_TICK_INTERVAL_SECONDS

        session = _make_tick_session(age_seconds=HEARTBEAT_TICK_INTERVAL_SECONDS + 5)
        assert _run_publisher({"running": [session]}) == 0


class TestLivenessCeiling:
    def _aged_past_ceiling(self):
        from bridge.response import HEARTBEAT_MAX_TICKS, HEARTBEAT_TICK_INTERVAL_SECONDS

        return _make_tick_session(
            age_seconds=(HEARTBEAT_MAX_TICKS + 1) * HEARTBEAT_TICK_INTERVAL_SECONDS + 5
        )

    def test_ceiling_steers_instead_of_ticking(self, fake_redis):
        session = self._aged_past_ceiling()
        with patch("agent.steering.push_steering_message") as push:
            assert _run_publisher({"running": [session]}) == 0
        push.assert_called_once()
        # No reaction was queued past the ceiling.
        assert fake_redis.lists == {}

    def test_ceiling_steer_uses_the_session_scoped_leg(self, fake_redis):
        """room_id=None: 'publish your progress' is a diagnostic about THIS
        session; on the Room leg a sibling could consume it (#2642)."""
        session = self._aged_past_ceiling()
        with patch("agent.steering.push_steering_message") as push:
            _run_publisher({"running": [session]})
        assert push.call_args.kwargs["room_id"] is None
        assert push.call_args.kwargs["sender"] == "watchdog"

    def test_ceiling_fires_once_across_two_scans(self, fake_redis):
        session = self._aged_past_ceiling()
        with patch("agent.steering.push_steering_message") as push:
            _run_publisher({"running": [session]})
            _run_publisher({"running": [session]})
        assert push.call_count == 1

    def test_unhonored_steer_leaves_the_counter_frozen(self, fake_redis):
        """A wedged session never drains the steer, so no progress message
        appears and no re-anchor happens. The counter stays frozen at the
        ceiling — the intended terminal display. Do not 'fix' this with a
        retry or timeout escalation."""
        session = self._aged_past_ceiling()
        with patch("agent.steering.push_steering_message"):
            for _ in range(5):
                _run_publisher({"running": [session]})
        assert fake_redis.lists == {}
        from bridge.liveness_ticks import ceiling_key

        assert ceiling_key(session.session_id) in fake_redis.store


class TestReAnchoring:
    def test_delivered_message_restarts_the_counter(self, fake_redis):
        from bridge.liveness_ticks import read_anchor, read_last_tick
        from bridge.response import HEARTBEAT_TICK_INTERVAL_SECONDS
        from bridge.telegram_relay import _reanchor_liveness_counter

        session = _make_tick_session(age_seconds=HEARTBEAT_TICK_INTERVAL_SECONDS * 2 + 5)
        _run_publisher({"running": [session]})
        assert read_last_tick(session.session_id) == 2

        _reanchor_liveness_counter({"session_id": session.session_id}, 4242)

        assert read_last_tick(session.session_id) == 0
        anchor_ts, anchor_msg = read_anchor(session.session_id)
        assert anchor_msg == 4242
        assert time.time() - anchor_ts < 5

    def test_delivered_no_id_clears_the_marker_without_reanchoring(self, fake_redis):
        """The DELIVERED_NO_ID hole: the human was answered but there is no
        message to anchor to, so the counter stops rather than latching."""
        from bridge.liveness_ticks import ceiling_key, read_anchor
        from bridge.response import HEARTBEAT_MAX_TICKS, HEARTBEAT_TICK_INTERVAL_SECONDS
        from bridge.telegram_relay import _reanchor_liveness_counter

        session = _make_tick_session(
            age_seconds=(HEARTBEAT_MAX_TICKS + 1) * HEARTBEAT_TICK_INTERVAL_SECONDS + 5
        )
        with patch("agent.steering.push_steering_message"):
            _run_publisher({"running": [session]})
        assert ceiling_key(session.session_id) in fake_redis.store

        _reanchor_liveness_counter({"session_id": session.session_id}, None)

        assert ceiling_key(session.session_id) not in fake_redis.store
        assert read_anchor(session.session_id) is None


class TestReactionSlotPrecedence:
    """Drain-side guard (bridge/telegram_relay.py). Terminal-wins and
    no-flicker are the highest-value assertions in this change."""

    def _tick_payload(self):
        from agent.reaction_priority import PRIORITY_HEARTBEAT

        return {
            "type": "reaction",
            "chat_id": "-100",
            "reply_to": 42,
            "emoji": "🥱",
            "session_id": "tg_user_-100_42",
            "timestamp": time.time(),
            "priority": PRIORITY_HEARTBEAT,
            "heartbeat_tick": 3,
        }

    def test_tick_dropped_when_terminal_owns_the_slot(self, fake_redis):
        from agent.reaction_priority import PRIORITY_TERMINAL
        from bridge.liveness_ticks import record_slot_owner
        from bridge.telegram_relay import _reaction_yields_slot

        record_slot_owner("-100", 42, PRIORITY_TERMINAL)
        assert _reaction_yields_slot(self._tick_payload()) is True

    def test_tick_dropped_when_any_higher_rank_owns_the_slot(self, fake_redis):
        from agent.reaction_priority import PRIORITY_SUPPRESS
        from bridge.liveness_ticks import record_slot_owner
        from bridge.telegram_relay import _reaction_yields_slot

        record_slot_owner("-100", 42, PRIORITY_SUPPRESS)
        assert _reaction_yields_slot(self._tick_payload()) is True

    def test_tick_delivered_on_an_unowned_slot(self, fake_redis):
        from bridge.telegram_relay import _reaction_yields_slot

        assert _reaction_yields_slot(self._tick_payload()) is False

    def test_tick_dropped_for_a_terminal_session(self, fake_redis):
        from bridge.telegram_relay import _reaction_yields_slot

        with patch("bridge.telegram_relay._session_reached_terminal_status", return_value=True):
            assert _reaction_yields_slot(self._tick_payload()) is True

    def test_budget_reaction_dropped_after_terminal(self, fake_redis):
        """Latent bug predating the counter: nothing stopped 🤯 landing after a
        session's terminal reaction."""
        from agent.reaction_priority import PRIORITY_INGESTION, PRIORITY_TERMINAL
        from bridge.liveness_ticks import record_slot_owner
        from bridge.telegram_relay import _reaction_yields_slot

        record_slot_owner("-100", 42, PRIORITY_TERMINAL)
        payload = {
            "chat_id": "-100",
            "reply_to": 42,
            "emoji": "🤯",
            "priority": PRIORITY_INGESTION,
        }
        assert _reaction_yields_slot(payload) is True

    def test_pickup_still_overwrites_worker_down(self, fake_redis):
        """⚠ (2) → ✍ (3) must keep working: the guard is not a blanket
        monotonic-rank rule."""
        from agent.reaction_priority import PRIORITY_INGESTION, PRIORITY_PICKUP
        from bridge.liveness_ticks import record_slot_owner
        from bridge.telegram_relay import _reaction_yields_slot

        record_slot_owner("-100", 42, PRIORITY_INGESTION)
        payload = {
            "chat_id": "-100",
            "reply_to": 42,
            "emoji": "✍",
            "priority": PRIORITY_PICKUP,
        }
        assert _reaction_yields_slot(payload) is False

    def test_killed_session_drops_the_tick(self, fake_redis):
        """`killed` is a terminal status (models/session_lifecycle.py) and must
        drop a queued tick.

        Regression guard for a hand-rolled status set that omitted it: a session
        killed while a tick was already queued would have had the tick land on
        top of its terminal reaction, since the two outbox queues drain in
        undefined order and the slot-owner layer only helps when the terminal
        reaction happened to go first.
        """
        from models.session_lifecycle import TERMINAL_STATUSES

        assert "killed" in TERMINAL_STATUSES

        from bridge.telegram_relay import _session_reached_terminal_status

        session = MagicMock()
        session.status = "killed"
        with patch("models.agent_session.AgentSession") as mock_model:
            mock_model.query.filter.return_value = [session]
            assert _session_reached_terminal_status("tg_user_-100_42") is True

    def test_unranked_reaction_delivers_without_claiming_the_slot(self, fake_redis):
        """An arbitrary agent glyph must not lock the slot at terminal rank.

        `priority_for_glyph` answers DEFAULT_PRIORITY (terminal) for anything it
        does not recognize, so react_with_emoji is never dropped. Recording that
        fallback as the slot owner would suppress every lower-ranked writer --
        including the counter -- for the owner key's whole TTL.
        """
        from agent.reaction_priority import PRIORITY_HEARTBEAT, priority_for_glyph
        from bridge.liveness_ticks import read_slot_owner
        from bridge.telegram_relay import _reaction_yields_slot, _record_reaction_slot_owner

        unranked = {
            "chat_id": "-100",
            "reply_to": 42,
            "emoji": "🦄",
            "priority": priority_for_glyph("🦄"),
            "priority_ranked": False,
        }
        # Delivered, not dropped.
        assert _reaction_yields_slot(unranked) is False
        _record_reaction_slot_owner(unranked)
        # ...but it took no ownership, so the counter is not suppressed.
        assert read_slot_owner("-100", 42) is None

        tick = self._tick_payload()
        tick["priority"] = PRIORITY_HEARTBEAT
        assert _reaction_yields_slot(tick) is False

    def test_ranked_reaction_still_claims_the_slot(self, fake_redis):
        """Control for the test above: a known glyph must still take ownership."""
        from agent.reaction_priority import PRIORITY_TERMINAL
        from bridge.liveness_ticks import read_slot_owner
        from bridge.telegram_relay import _record_reaction_slot_owner

        _record_reaction_slot_owner(
            {
                "chat_id": "-100",
                "reply_to": 42,
                "emoji": "✅",
                "priority": PRIORITY_TERMINAL,
                "priority_ranked": True,
            }
        )
        assert read_slot_owner("-100", 42) == PRIORITY_TERMINAL

    def test_terminal_reaction_always_wins(self, fake_redis):
        from agent.reaction_priority import PRIORITY_HEARTBEAT, PRIORITY_TERMINAL
        from bridge.liveness_ticks import record_slot_owner
        from bridge.telegram_relay import _reaction_yields_slot

        record_slot_owner("-100", 42, PRIORITY_HEARTBEAT)
        payload = {
            "chat_id": "-100",
            "reply_to": 42,
            "emoji": "👏",
            "priority": PRIORITY_TERMINAL,
        }
        assert _reaction_yields_slot(payload) is False


class TestReactionPayloadSchemaParity:
    """Every hand-mirrored reaction payload must match the canonical builder.

    Replaces the deleted ``test_payload_matches_build_reaction_payload``, which
    pinned the ⏳ design. There are five mirrors of a schema whose only source
    of truth is a static method they deliberately do not import; without this
    test they drift silently the first time the schema moves.
    """

    # Keys a mirror may add on top of the canonical schema.
    EXTRA_KEYS = {"custom_emoji_document_id", "heartbeat_tick", "ack_sent_id"}

    def _assert_parity(self, payload, *, chat_id, reply_to, emoji, session_id, expect_ranked):
        from agent.output_handler import TelegramRelayOutputHandler

        expected = TelegramRelayOutputHandler._build_reaction_payload(
            chat_id,
            reply_to,
            emoji,
            session_id,
            timestamp=payload["timestamp"],
            priority=payload["priority"],
        )
        assert set(payload) - set(expected) <= self.EXTRA_KEYS

        # `priority_ranked` cannot be compared against the builder's own output:
        # `priority` is passed through above, and any explicit `priority` forces
        # the derived value True -- which would make this helper structurally
        # unable to express the one writer that must be False
        # (`tools/react_with_emoji.py`). So the caller states the expected value
        # instead. Asserting only presence/type would leave a real hole: a mirror
        # could flip its literal and silently reinstate the #2716 review blocker
        # where an unranked glyph claims the slot at terminal rank.
        assert "priority_ranked" in payload, "priority_ranked missing from mirror"
        assert payload["priority_ranked"] is expect_ranked, (
            f"priority_ranked drifted: expected {expect_ranked}"
        )

        for field, value in expected.items():
            if field == "priority_ranked":
                continue
            assert payload[field] == value, f"{field} drifted"

    def test_canonical_builder_emits_priority(self):
        from agent.output_handler import TelegramRelayOutputHandler

        payload = TelegramRelayOutputHandler._build_reaction_payload("-100", 42, "👀", "s1")
        assert payload["priority"] is not None

    def test_watchdog_liveness_tick_mirror(self, fake_redis):
        from bridge.response import HEARTBEAT_TICK_INTERVAL_SECONDS

        session = _make_tick_session(age_seconds=HEARTBEAT_TICK_INTERVAL_SECONDS + 5)
        _run_publisher({"running": [session]})
        payload = json.loads(fake_redis.lists["telegram:outbox:tg_user_-100_42"][0])
        self._assert_parity(
            payload,
            chat_id="-100",
            reply_to=42,
            emoji=payload["emoji"],
            session_id="tg_user_-100_42",
            expect_ranked=True,
        )

    def test_tool_budget_mirror(self, fake_redis):
        from agent.tool_budget import BUDGET_REACTION_EMOJI, _queue_budget_reaction

        session = _make_tick_session()
        _queue_budget_reaction(session)
        payload = json.loads(fake_redis.lists["telegram:outbox:tg_user_-100_42"][0])
        self._assert_parity(
            payload,
            chat_id="-100",
            reply_to=42,
            emoji=BUDGET_REACTION_EMOJI,
            session_id="tg_user_-100_42",
            expect_ranked=True,
        )

    def test_session_completion_suppress_mirror(self, fake_redis, monkeypatch):
        from agent import session_completion

        captured = {}

        class _FakeRedisModule:
            @staticmethod
            def from_url(*a, **kw):
                return fake_redis

        monkeypatch.setattr(session_completion, "_OUTBOX_TTL", 3600, raising=False)
        with patch.dict("sys.modules", {"redis": SimpleNamespace(Redis=_FakeRedisModule)}):
            parent = SimpleNamespace(session_id="tg_user_-100_42")
            assert session_completion._queue_completion_suppress_reaction(parent, "-100", 42)
        captured["payload"] = json.loads(fake_redis.lists["telegram:outbox:tg_user_-100_42"][0])
        self._assert_parity(
            captured["payload"],
            chat_id="-100",
            reply_to=42,
            emoji="👀",
            session_id="tg_user_-100_42",
            expect_ranked=True,
        )

    def test_react_with_emoji_mirror(self, monkeypatch):
        import tools.react_with_emoji as rwe
        from tools.emoji_embedding import EmojiResult

        fr = _FakeRedis()
        monkeypatch.setattr(rwe, "_get_redis", lambda: fr)
        monkeypatch.setattr(rwe, "_resolve_transport", lambda: "telegram")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "42")
        monkeypatch.setenv("VALOR_SESSION_ID", "tg_user_-100_42")
        with patch("tools.emoji_embedding.find_best_emoji", return_value=EmojiResult(emoji="🔥")):
            rwe.react("excited")
        payload = json.loads(fr.lists["telegram:outbox:tg_user_-100_42"][0])
        self._assert_parity(
            payload,
            chat_id="-100",
            reply_to=42,
            emoji="🔥",
            session_id="tg_user_-100_42",
            expect_ranked=False,
        )

    def test_worker_down_reactions_uses_the_builder_directly(self):
        """The fifth mirror imports _build_reaction_payload rather than
        re-inlining it, so parity is structural — assert that stays true."""
        import inspect

        from agent import worker_down_reactions

        src = inspect.getsource(worker_down_reactions)
        assert "_build_reaction_payload(" in src
        assert '"type": "reaction"' not in src
