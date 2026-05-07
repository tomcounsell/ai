"""Unit tests for stall detection in monitoring/session_watchdog.py.

Tests check_stalled_sessions (detection) and fix_unhealthy_session (abandon).
The old stall retry mechanisms (_recover_stalled_pending, _kill_stalled_worker,
_enqueue_stall_retry) were deleted in the bridge-resilience refactor.
Recovery is now handled by the unified _agent_session_health_check in agent/agent_session_queue.py.
"""

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
# _apply_stall_reaction (issue #1313)
# ===================================================================


def _make_stall_session(
    session_id="tg_user_-100_42",
    chat_id="-100",
    telegram_message_id=42,
    agent_session_id="as-001",
):
    """Build a SimpleNamespace mimicking AgentSession for stall-reaction tests."""
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=agent_session_id,
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
    )


class _FakeRedis:
    """Minimal in-memory Redis stub supporting set NX EX, rpush, expire, delete."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

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


class TestStallReaction:
    def test_reaction_queued_on_first_stall(self, fake_redis, monkeypatch):
        from monitoring.session_watchdog import _apply_stall_reaction

        monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)
        session = _make_stall_session()
        result = _apply_stall_reaction(session)
        assert result is True
        # Dedup key claimed
        assert "watchdog:stall_reaction_applied:tg_user_-100_42" in fake_redis.store
        # Outbox payload written
        queue_key = "telegram:outbox:tg_user_-100_42"
        assert queue_key in fake_redis.lists
        import json as _json

        payload = _json.loads(fake_redis.lists[queue_key][0])
        assert payload["type"] == "reaction"
        assert payload["chat_id"] == "-100"
        assert payload["reply_to"] == 42
        assert payload["emoji"] == "⏳"
        assert payload["session_id"] == "tg_user_-100_42"
        assert "timestamp" in payload
        # TTL set on the queue
        assert fake_redis.expires[queue_key] == 3600

    def test_reaction_deduped_on_second_call(self, fake_redis, monkeypatch):
        from monitoring.session_watchdog import _apply_stall_reaction

        monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)
        session = _make_stall_session()
        first = _apply_stall_reaction(session)
        second = _apply_stall_reaction(session)
        assert first is True
        assert second is False
        # Only one payload was queued
        queue_key = "telegram:outbox:tg_user_-100_42"
        assert len(fake_redis.lists[queue_key]) == 1

    def test_skip_when_no_telegram_message_id(self, fake_redis, monkeypatch):
        from monitoring.session_watchdog import _apply_stall_reaction

        monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)
        session = _make_stall_session(telegram_message_id=None)
        assert _apply_stall_reaction(session) is False
        assert fake_redis.store == {}
        assert fake_redis.lists == {}

    def test_skip_when_no_chat_id(self, fake_redis, monkeypatch):
        from monitoring.session_watchdog import _apply_stall_reaction

        monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)
        session = _make_stall_session(chat_id=None)
        assert _apply_stall_reaction(session) is False
        assert fake_redis.store == {}

    def test_skip_when_telegram_message_id_zero(self, fake_redis, monkeypatch):
        from monitoring.session_watchdog import _apply_stall_reaction

        monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)
        # 0 is treated as falsy -- no real Telegram message id is 0.
        session = _make_stall_session(telegram_message_id=0)
        assert _apply_stall_reaction(session) is False
        assert fake_redis.store == {}

    def test_skip_when_session_id_empty(self, fake_redis, monkeypatch):
        from monitoring.session_watchdog import _apply_stall_reaction

        monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)
        session = _make_stall_session(session_id="", agent_session_id=None)
        assert _apply_stall_reaction(session) is False

    def test_skip_when_flag_disabled(self, fake_redis, monkeypatch):
        from monitoring.session_watchdog import _apply_stall_reaction

        monkeypatch.setenv("WATCHDOG_STALL_REACTION_ENABLED", "0")
        session = _make_stall_session()
        assert _apply_stall_reaction(session) is False
        # No Redis writes when disabled
        assert fake_redis.store == {}
        assert fake_redis.lists == {}

    def test_redis_exception_is_fail_quiet(self, monkeypatch):
        from monitoring.session_watchdog import _apply_stall_reaction

        class _BoomRedis:
            def set(self, *a, **kw):
                raise RuntimeError("redis down")

            def rpush(self, *a, **kw):
                raise RuntimeError("redis down")

            def expire(self, *a, **kw):
                raise RuntimeError("redis down")

            def delete(self, *a, **kw):
                raise RuntimeError("redis down")

        monkeypatch.setattr("popoto.redis_db.POPOTO_REDIS_DB", _BoomRedis())
        monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)
        session = _make_stall_session()
        # Must not raise
        assert _apply_stall_reaction(session) is False

    def test_payload_matches_build_reaction_payload(self, fake_redis, monkeypatch):
        """Schema parity test: the watchdog's inlined payload literal must match
        agent.output_handler.OutputHandler._build_reaction_payload byte-for-byte.

        This is the ONLY mechanical defense against schema drift between the
        two outbox writers. If this test fails, either reconcile the watchdog's
        literal or update _build_reaction_payload (and probably the bridge relay).
        """
        from agent.output_handler import TelegramRelayOutputHandler
        from monitoring.session_watchdog import _apply_stall_reaction

        monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)
        session = _make_stall_session()
        assert _apply_stall_reaction(session) is True
        import json as _json

        queue_key = "telegram:outbox:tg_user_-100_42"
        actual = _json.loads(fake_redis.lists[queue_key][0])

        expected = TelegramRelayOutputHandler._build_reaction_payload(
            chat_id=str(session.chat_id),
            reply_to_msg_id=session.telegram_message_id,
            emoji="⏳",
            session_id=session.session_id,
            timestamp=actual["timestamp"],
        )
        assert actual == expected

    def test_clear_dedup_removes_key(self, fake_redis, monkeypatch):
        from monitoring.session_watchdog import (
            _apply_stall_reaction,
            _clear_stall_reaction_dedup,
        )

        monkeypatch.delenv("WATCHDOG_STALL_REACTION_ENABLED", raising=False)
        session = _make_stall_session()
        # Apply once, then clear, then apply again -- should succeed twice.
        assert _apply_stall_reaction(session) is True
        _clear_stall_reaction_dedup(session.session_id)
        assert "watchdog:stall_reaction_applied:tg_user_-100_42" not in fake_redis.store
        # Second apply succeeds because the dedup key was cleared.
        assert _apply_stall_reaction(session) is True
        queue_key = "telegram:outbox:tg_user_-100_42"
        assert len(fake_redis.lists[queue_key]) == 2

    def test_clear_dedup_empty_session_id_is_noop(self, fake_redis):
        from monitoring.session_watchdog import _clear_stall_reaction_dedup

        # Should not raise, no Redis call needed.
        _clear_stall_reaction_dedup("")
        _clear_stall_reaction_dedup(None)  # type: ignore[arg-type]
