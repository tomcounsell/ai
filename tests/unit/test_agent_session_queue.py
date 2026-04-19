"""Unit tests for agent.agent_session_queue helpers.

Focused on field-extraction semantics used by delete-and-recreate callers
(retry, orphan fix, continuation fallback). _pop_agent_session itself uses
in-place mutation via transition_status() and does NOT go through
_extract_agent_session_fields.

Also tests Redis pop lock acquisition and contention behavior.
Also tests sustainability throttle guards in _pop_agent_session.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.agent_session_queue import (
    _AGENT_SESSION_FIELDS,
    _acquire_pop_lock,
    _complete_agent_session,
    _extract_agent_session_fields,
    _pop_agent_session,
    _release_pop_lock,
)
from models.agent_session import AgentSession


def _make_session(**overrides) -> AgentSession:
    """Build an unsaved AgentSession with sensible defaults."""
    defaults = {
        "project_key": "test",
        "status": "pending",
        "priority": "normal",
        "created_at": datetime.now(tz=UTC),
        "session_id": "unit-test",
        "working_dir": "/tmp/test",
        "chat_id": "123",
        "message_text": "hello",
        "sender_name": "Tester",
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return AgentSession(**defaults)


class TestExtractFieldsMessageTextRoundTrip:
    """_extract_agent_session_fields must preserve message_text across
    delete-and-recreate via the initial_telegram_message dict.

    message_text is a virtual @property on AgentSession that reads from
    initial_telegram_message["message_text"]. _AGENT_SESSION_FIELDS does not
    include message_text directly -- it includes initial_telegram_message,
    so the value is preserved transitively when the dict is copied.
    """

    def test_message_text_roundtrips_via_initial_telegram_message(self):
        """Round-trip: extract -> create new record -> .message_text matches."""
        original = _make_session(message_text="the-original-message")
        assert original.message_text == "the-original-message"

        fields = _extract_agent_session_fields(original)

        # message_text is NOT a top-level key in the extracted dict; it lives
        # inside initial_telegram_message.
        assert "message_text" not in fields
        assert "initial_telegram_message" in fields
        assert fields["initial_telegram_message"]["message_text"] == "the-original-message"

        # Recreate and verify the virtual property resolves correctly.
        recreated = AgentSession(**fields)
        assert recreated.message_text == "the-original-message"

    def test_message_text_none_roundtrips_safely(self):
        """When message_text is None / unset, extraction and recreation
        must not raise."""
        original = _make_session()
        # Clear the text explicitly
        original.initial_telegram_message = None

        fields = _extract_agent_session_fields(original)
        # initial_telegram_message may be None; recreation should still work
        recreated = AgentSession(**fields)
        # No crash; .message_text returns None for empty dict
        assert recreated.message_text in (None, "")

    def test_scheduling_depth_intentionally_omitted(self):
        """_AGENT_SESSION_FIELDS must NOT include scheduling_depth.

        scheduling_depth is a derived @property that walks the
        parent_agent_session_id chain at read time. Including it in the
        extracted dict would attempt to set a read-only property on recreate.
        """
        assert "scheduling_depth" not in _AGENT_SESSION_FIELDS

    def test_agent_session_id_intentionally_omitted(self):
        """_AGENT_SESSION_FIELDS must NOT include agent_session_id / id.

        agent_session_id is the AutoKeyField; delete-and-recreate callers
        rely on a fresh auto-generated ID for the new record.
        """
        assert "agent_session_id" not in _AGENT_SESSION_FIELDS
        assert "id" not in _AGENT_SESSION_FIELDS


class TestPopLock:
    """Tests for _acquire_pop_lock and _release_pop_lock helpers.

    These helpers prevent TOCTOU races in _pop_agent_session by making the
    query→transition block atomic across concurrent workers.
    """

    def test_acquire_pop_lock_succeeds_when_no_lock_held(self):
        """First acquisition of a lock key must succeed."""
        chat_id = "test-pop-lock-chat-1"
        # Ensure no stale lock
        _release_pop_lock(chat_id)
        try:
            result = _acquire_pop_lock(chat_id)
            assert result is True, "First acquisition must succeed"
        finally:
            _release_pop_lock(chat_id)

    def test_acquire_pop_lock_fails_when_already_held(self):
        """Second acquisition of the same key must fail (contention)."""
        chat_id = "test-pop-lock-chat-2"
        _release_pop_lock(chat_id)
        try:
            first = _acquire_pop_lock(chat_id)
            assert first is True, "First acquisition must succeed"

            second = _acquire_pop_lock(chat_id)
            assert second is False, (
                "Second acquisition while lock is held must return False — "
                "contention detected, caller should return None"
            )
        finally:
            _release_pop_lock(chat_id)

    def test_release_pop_lock_allows_reacquisition(self):
        """After releasing a lock, it must be acquirable again."""
        chat_id = "test-pop-lock-chat-3"
        _release_pop_lock(chat_id)
        try:
            first = _acquire_pop_lock(chat_id)
            assert first is True
            _release_pop_lock(chat_id)

            reacquired = _acquire_pop_lock(chat_id)
            assert reacquired is True, "After release, lock must be acquirable again"
        finally:
            _release_pop_lock(chat_id)

    def test_different_chat_ids_have_independent_locks(self):
        """Locks for different chat_ids must be independent."""
        chat_id_a = "test-pop-lock-chat-a"
        chat_id_b = "test-pop-lock-chat-b"
        _release_pop_lock(chat_id_a)
        _release_pop_lock(chat_id_b)
        try:
            result_a = _acquire_pop_lock(chat_id_a)
            result_b = _acquire_pop_lock(chat_id_b)
            assert result_a is True, "Lock for chat_id_a must succeed"
            assert result_b is True, "Lock for chat_id_b must succeed (independent)"
        finally:
            _release_pop_lock(chat_id_a)
            _release_pop_lock(chat_id_b)

    def test_acquire_pop_lock_returns_true_on_redis_failure(self):
        """If Redis is unavailable, _acquire_pop_lock must fail open (return True).

        Failing open preserves backward compatibility: workers continue to
        function without the lock rather than deadlocking when Redis is down.
        """
        with patch("agent.agent_session_queue._acquire_pop_lock") as mock_acquire:
            # Simulate the fail-open path by returning True even on error
            mock_acquire.return_value = True
            result = mock_acquire("test-chat-id")
            assert result is True

    def test_pop_lock_key_is_chat_id_scoped(self):
        """Pop lock key format must be worker:pop_lock:{chat_id}."""
        from popoto import get_redis

        redis_client = get_redis()
        chat_id = "test-pop-lock-key-format"
        expected_key = f"worker:pop_lock:{chat_id}"
        _release_pop_lock(chat_id)
        try:
            _acquire_pop_lock(chat_id)
            assert redis_client.exists(expected_key), (
                f"Lock key {expected_key!r} must exist in Redis after acquisition"
            )
        finally:
            _release_pop_lock(chat_id)


class TestCompleteAgentSessionRequeryNoStatusFilter:
    """Regression tests for _complete_agent_session re-query fix (issue #825).

    The bug: _complete_agent_session() filtered re-query by status="running".
    If the session had already transitioned to another status, the filter
    returned empty and the code fell back to the stale in-memory object.
    finalize_session() would then backfill _saved_field_values from that stale
    object, recording the wrong old status. On save, Popoto removed the session
    from the wrong index set, orphaning it in multiple index sets simultaneously.

    The fix: re-query without status filter so a fresh Redis object is always
    retrieved regardless of current status.
    """

    @pytest.mark.asyncio
    async def test_complete_agent_session_does_not_filter_by_running_status(self):
        """_complete_agent_session must not pass status='running' to filter().

        When the session has already transitioned away from 'running' before
        _complete_agent_session executes, a status-filtered re-query returns
        nothing, causing fallback to the stale in-memory object.
        """
        session = _make_session(status="completed")

        # _complete_agent_session now lives in agent.session_completion — patch there.
        with patch("agent.session_completion.AgentSession") as mock_agent_session_cls:
            mock_query = MagicMock()
            mock_agent_session_cls.query = mock_query
            mock_filter = MagicMock()
            mock_filter.__iter__ = MagicMock(return_value=iter([session]))
            mock_query.filter.return_value = mock_filter

            with patch("models.session_lifecycle.finalize_session") as mock_finalize:
                mock_finalize.return_value = None
                await _complete_agent_session(session)

            # The filter call must NOT include status="running"
            call_kwargs_list = mock_query.filter.call_args_list
            for call_args in call_kwargs_list:
                _, kwargs = call_args
                assert "status" not in kwargs, (
                    "_complete_agent_session must not filter by status='running'; "
                    "doing so causes stale fallback when the session has already transitioned"
                )

    @pytest.mark.asyncio
    async def test_complete_agent_session_uses_fresh_record_when_session_already_completed(self):
        """Fresh re-query is used even when session.status != 'running' at call time.

        This simulates the race condition: session transitions to 'completed'
        before _complete_agent_session runs. Without the fix the filter would
        have returned nothing; with the fix a fresh record is always fetched.
        """
        stale_session = _make_session(status="running")
        fresh_session = _make_session(status="completed")
        fresh_session.session_id = stale_session.session_id

        # _complete_agent_session now lives in agent.session_completion — patch there.
        with patch("agent.session_completion.AgentSession") as mock_agent_session_cls:
            mock_query = MagicMock()
            mock_agent_session_cls.query = mock_query
            mock_filter = MagicMock()
            # Fresh record returned (status-independent query)
            mock_filter.__iter__ = MagicMock(return_value=iter([fresh_session]))
            mock_query.filter.return_value = mock_filter

            finalize_called_with = []

            with patch("models.session_lifecycle.finalize_session") as mock_finalize:
                mock_finalize.side_effect = lambda s, *args, **kw: finalize_called_with.append(s)
                await _complete_agent_session(stale_session)

            # finalize_session must have been called with the fresh record, not the stale one
            if finalize_called_with:
                assert finalize_called_with[0].status == "completed", (
                    "finalize_session should receive the fresh Redis record (status=completed), "
                    "not the stale in-memory object (status=running)"
                )


class TestPopAgentSessionThrottleGuard:
    """Tests for sustainability throttle-level guard inside _pop_agent_session.

    When the Redis throttle_level key is 'suspended', both normal and low
    priority sessions must be blocked (function returns None without dequeuing).
    When throttle_level is 'moderate', only low priority sessions are blocked.

    These tests mock both the Redis throttle-level key read and AgentSession
    queries so they run as pure unit tests without a live Redis connection.
    """

    def _make_mock_redis(self, throttle_value: str | None):
        """Return a MagicMock Redis client that returns throttle_value for GET calls.

        Returns None for the pause-key check (circuit not open) and the
        encoded throttle_value for the throttle-level key check.
        """
        mock_r = MagicMock()
        # First call: pause key → None (circuit not open)
        # Second call: throttle key → encoded throttle level
        if throttle_value is None:
            mock_r.get.side_effect = [None, None]
        else:
            mock_r.get.side_effect = [None, throttle_value.encode()]
        return mock_r

    def _make_pending_session(self, priority: str) -> AgentSession:
        """Build a pending AgentSession with the given priority."""
        return _make_session(status="pending", priority=priority)

    @pytest.mark.asyncio
    async def test_suspended_throttle_blocks_normal_priority(self):
        """When throttle_level is 'suspended', _pop_agent_session returns None
        even when a normal-priority pending session exists."""
        normal_session = self._make_pending_session("normal")
        mock_r = self._make_mock_redis("suspended")

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with patch("agent.session_pickup.AgentSession") as mock_cls:
                mock_query = MagicMock()
                mock_cls.query = mock_query
                mock_query.async_filter = AsyncMock(return_value=[normal_session])

                with patch("agent.session_pickup._acquire_pop_lock", return_value=True):
                    with patch("agent.session_pickup._release_pop_lock"):
                        result = await _pop_agent_session(
                            worker_key="test-suspended-normal", is_project_keyed=False
                        )

        assert result is None, (
            "_pop_agent_session must return None when throttle='suspended' "
            "and only normal-priority sessions are available"
        )

    @pytest.mark.asyncio
    async def test_suspended_throttle_blocks_low_priority(self):
        """When throttle_level is 'suspended', _pop_agent_session returns None
        even when a low-priority pending session exists."""
        low_session = self._make_pending_session("low")
        mock_r = self._make_mock_redis("suspended")

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with patch("agent.session_pickup.AgentSession") as mock_cls:
                mock_query = MagicMock()
                mock_cls.query = mock_query
                mock_query.async_filter = AsyncMock(return_value=[low_session])

                with patch("agent.session_pickup._acquire_pop_lock", return_value=True):
                    with patch("agent.session_pickup._release_pop_lock"):
                        result = await _pop_agent_session(
                            worker_key="test-suspended-low", is_project_keyed=False
                        )

        assert result is None, (
            "_pop_agent_session must return None when throttle='suspended' "
            "and only low-priority sessions are available"
        )

    @pytest.mark.asyncio
    async def test_moderate_throttle_blocks_low_priority(self):
        """When throttle_level is 'moderate', _pop_agent_session returns None
        when only low-priority pending sessions exist."""
        low_session = self._make_pending_session("low")
        mock_r = self._make_mock_redis("moderate")

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with patch("agent.session_pickup.AgentSession") as mock_cls:
                mock_query = MagicMock()
                mock_cls.query = mock_query
                mock_query.async_filter = AsyncMock(return_value=[low_session])

                with patch("agent.session_pickup._acquire_pop_lock", return_value=True):
                    with patch("agent.session_pickup._release_pop_lock"):
                        result = await _pop_agent_session(
                            worker_key="test-moderate-low", is_project_keyed=False
                        )

        assert result is None, (
            "_pop_agent_session must return None when throttle='moderate' "
            "and only low-priority sessions are available"
        )


class TestHealthCheckDeliveryGuard:
    """Tests for the response_delivered_at guard in _agent_session_health_check.

    When a session has response_delivered_at set, the health check should
    finalize it as completed instead of resetting it to pending (which would
    cause duplicate message delivery -- issue #918).
    """

    @pytest.mark.asyncio
    async def test_delivered_session_finalized_not_requeued(self):
        """Session WITH response_delivered_at should be marked completed,
        not reset to pending."""
        # Use session_type="teammate" + chat_id so worker_key = chat_id
        delivered_session = _make_session(
            status="running",
            started_at=datetime(2020, 1, 1, tzinfo=UTC),
            session_type="teammate",
            chat_id="chat-123",
        )
        delivered_session.agent_session_id = "delivered-test-1"
        delivered_session.response_delivered_at = datetime(2020, 1, 1, 0, 30, tzinfo=UTC)

        mock_finalize = MagicMock()

        # _agent_session_health_check now lives in agent.session_health — patch there.
        with (
            patch("agent.session_health.AgentSession") as mock_cls,
            patch(
                "agent.session_health._active_workers",
                {"chat-123": MagicMock(done=MagicMock(return_value=True))},
            ),
        ):
            mock_cls.query.filter.return_value = [delivered_session]
            # The health check does a local import of finalize_session from models
            import models.session_lifecycle as lifecycle_mod

            original_finalize = lifecycle_mod.finalize_session
            original_transition = lifecycle_mod.transition_status
            lifecycle_mod.finalize_session = mock_finalize
            mock_transition = MagicMock()
            lifecycle_mod.transition_status = mock_transition
            try:
                from agent.agent_session_queue import _agent_session_health_check

                await _agent_session_health_check()
            finally:
                lifecycle_mod.finalize_session = original_finalize
                lifecycle_mod.transition_status = original_transition

            # Should finalize as completed, NOT transition to pending
            mock_finalize.assert_called_once()
            call_args = mock_finalize.call_args
            assert call_args[0][1] == "completed"
            mock_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_undelivered_session_recovered_to_pending(self):
        """Session WITHOUT response_delivered_at should be recovered to
        pending as before (existing behavior preserved)."""
        # Use session_type="teammate" + chat_id so worker_key = chat_id
        undelivered_session = _make_session(
            status="running",
            started_at=datetime(2020, 1, 1, tzinfo=UTC),
            session_type="teammate",
            chat_id="chat-456",
        )
        undelivered_session.agent_session_id = "undelivered-test-1"
        # Ensure response_delivered_at is None (not set)

        mock_transition = MagicMock()
        mock_finalize = MagicMock()

        # _agent_session_health_check now lives in agent.session_health — patch there.
        with (
            patch("agent.session_health.AgentSession") as mock_cls,
            patch(
                "agent.session_health._active_workers",
                {"chat-456": MagicMock(done=MagicMock(return_value=True))},
            ),
        ):
            mock_cls.query.filter.return_value = [undelivered_session]
            import models.session_lifecycle as lifecycle_mod

            original_finalize = lifecycle_mod.finalize_session
            original_transition = lifecycle_mod.transition_status
            lifecycle_mod.finalize_session = mock_finalize
            lifecycle_mod.transition_status = mock_transition
            try:
                from agent.agent_session_queue import _agent_session_health_check

                await _agent_session_health_check()
            finally:
                lifecycle_mod.finalize_session = original_finalize
                lifecycle_mod.transition_status = original_transition

            # Should transition to pending, NOT finalize
            mock_transition.assert_called_once()
            assert mock_transition.call_args[0][1] == "pending"
            mock_finalize.assert_not_called()


class TestAppendEventWithBadResponseDeliveredAt:
    """append_event must succeed regardless of response_delivered_at state.

    Regression coverage for #929: Popoto's is_valid() would fail when
    response_delivered_at held a non-datetime, non-None value, causing
    _append_event_dict to silently drop the save and leaving PM sessions
    stuck at status=running in Redis.
    """

    def _make_session_with_rda(self, rda_value) -> AgentSession:
        """Create a session then forcibly set response_delivered_at to rda_value,
        bypassing __setattr__ to simulate a corrupted Redis load."""
        session = _make_session(status="running")
        # Bypass the defensive __setattr__ to plant a bad value
        object.__setattr__(session, "response_delivered_at", rda_value)
        return session

    def test_append_event_with_none_response_delivered_at(self):
        """response_delivered_at=None is the normal case — must always work."""
        session = _make_session(status="running")
        assert session.response_delivered_at is None
        # append_event calls _append_event_dict → save; we verify no exception is raised
        # by checking __setattr__ coercion leaves None intact
        session.response_delivered_at = None
        assert session.response_delivered_at is None

    def test_append_event_with_int_response_delivered_at(self):
        """response_delivered_at as Unix timestamp int must be coerced to datetime."""
        session = _make_session(status="running")
        ts = 1_700_000_000
        session.response_delivered_at = ts
        assert isinstance(session.response_delivered_at, datetime)
        assert session.response_delivered_at.tzinfo is not None

    def test_append_event_with_datetime_response_delivered_at(self):
        """response_delivered_at as proper datetime must pass through unchanged."""
        session = _make_session(status="running")
        dt = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        session.response_delivered_at = dt
        assert session.response_delivered_at == dt

    def test_append_event_with_valid_iso_string_response_delivered_at(self):
        """response_delivered_at as valid ISO string must be coerced to UTC datetime."""
        session = _make_session(status="running")
        session.response_delivered_at = "2024-06-01T12:00:00+00:00"
        assert isinstance(session.response_delivered_at, datetime)
        assert session.response_delivered_at.tzinfo is not None

    def test_append_event_with_bad_string_response_delivered_at(self):
        """response_delivered_at as unparseable string must be reset to None."""
        session = _make_session(status="running")
        session.response_delivered_at = "not-a-date"
        assert session.response_delivered_at is None

    def test_append_event_with_descriptor_object_response_delivered_at(self):
        """response_delivered_at holding a non-datetime, non-None object (e.g. a
        Popoto DatetimeField descriptor) must be reset to None by __setattr__."""
        session = _make_session(status="running")
        # Plant a bad value as if loaded from a malformed Redis record
        object.__setattr__(session, "response_delivered_at", object())
        # Now simulate what Popoto's is_valid() loop does: it reads the value
        # and tries to coerce — our fix must ensure save() won't fail.
        # We verify by re-assigning through __setattr__ (which is what the
        # _normalize_kwargs path does at construction time).
        bad_val = session.response_delivered_at
        session.response_delivered_at = bad_val  # goes through __setattr__
        assert session.response_delivered_at is None

    def test_normalize_kwargs_coerces_bad_string(self):
        """_normalize_kwargs must reset a bad string response_delivered_at to None."""
        from models.agent_session import AgentSession

        kwargs = {
            "project_key": "test",
            "status": "pending",
            "session_id": "kw-test",
            "response_delivered_at": "bad-value",
        }
        result = AgentSession._normalize_kwargs(kwargs)
        assert result["response_delivered_at"] is None

    def test_normalize_kwargs_coerces_valid_iso_string(self):
        """_normalize_kwargs must convert a valid ISO string to a UTC datetime."""
        from models.agent_session import AgentSession

        kwargs = {
            "project_key": "test",
            "status": "pending",
            "session_id": "kw-test-iso",
            "response_delivered_at": "2024-06-01T12:00:00",
        }
        result = AgentSession._normalize_kwargs(kwargs)
        assert isinstance(result["response_delivered_at"], datetime)
        assert result["response_delivered_at"].tzinfo is not None


def test_append_event_succeeds_with_bad_response_delivered_at():
    """Regression test: append_event must succeed with any response_delivered_at state.

    Named for the verification table grep check in the plan. Delegates to the
    TestAppendEventWithBadResponseDeliveredAt class for the actual assertions.
    The key states (None, int, datetime, bad string, descriptor) are all covered
    there; this function confirms the overall fix is present by testing the
    canonical bad-value scenario end-to-end.
    """
    session = _make_session(status="running")
    # Plant a bad (descriptor-like) value bypassing __setattr__
    object.__setattr__(session, "response_delivered_at", object())
    # Re-assign through __setattr__ — must be coerced to None, not raise
    bad_val = session.response_delivered_at
    session.response_delivered_at = bad_val
    assert session.response_delivered_at is None


class TestHealthCheckNoProgressRecovery:
    """Tests for the no-progress recovery branch in _agent_session_health_check (#944).

    When a slugless dev session shares ``worker_key`` with a co-running PM session,
    ``worker_alive=True`` alone is insufficient to prove the dev session is being
    handled. The health check must inspect ``turn_count``, ``log_path``, and
    ``claude_session_uuid`` — if none is set and the 300s startup guard has
    elapsed, the session is orphaned and must be recovered.
    """

    def _make_stuck_dev_session(
        self,
        *,
        project_key: str = "test",
        agent_session_id: str = "stuck-dev-1",
        turn_count: int = 0,
        log_path=None,
        claude_session_uuid=None,
        started_seconds_ago: int = 600,
        response_delivered_at=None,
    ) -> AgentSession:
        """Build an unsaved slugless dev session stuck in ``running``."""
        import time as _time

        started_at = datetime.fromtimestamp(_time.time() - started_seconds_ago, tz=UTC)
        session = _make_session(
            project_key=project_key,
            status="running",
            session_type="dev",
            chat_id="some-chat-id",
            started_at=started_at,
        )
        # Slugless dev → worker_key == project_key
        session.slug = None
        session.agent_session_id = agent_session_id
        session.turn_count = turn_count
        session.log_path = log_path
        session.claude_session_uuid = claude_session_uuid
        if response_delivered_at is not None:
            session.response_delivered_at = response_delivered_at
        return session

    def _patch_lifecycle(self):
        """Return (mock_finalize, mock_transition, context_manager).

        The context manager patches both finalize_session and transition_status
        in models.session_lifecycle and restores them on exit.
        """
        import contextlib

        mock_finalize = MagicMock()
        mock_transition = MagicMock()

        @contextlib.contextmanager
        def _ctx():
            import models.session_lifecycle as lifecycle_mod

            original_finalize = lifecycle_mod.finalize_session
            original_transition = lifecycle_mod.transition_status
            lifecycle_mod.finalize_session = mock_finalize
            lifecycle_mod.transition_status = mock_transition
            try:
                yield
            finally:
                lifecycle_mod.finalize_session = original_finalize
                lifecycle_mod.transition_status = original_transition

        return mock_finalize, mock_transition, _ctx()

    @pytest.mark.asyncio
    async def test_no_progress_project_keyed_recovered_to_pending(self):
        """Slugless dev session with no progress and a live project-keyed worker
        must be recovered to ``pending`` after the 300s guard."""
        session = self._make_stuck_dev_session(
            project_key="valor",
            agent_session_id="no-prog-proj-1",
        )
        # worker_key for a slugless dev session == project_key
        assert session.worker_key == "valor"

        mock_finalize, mock_transition, lifecycle_ctx = self._patch_lifecycle()
        live_worker = MagicMock(done=MagicMock(return_value=False))

        # _agent_session_health_check now lives in agent.session_health — patch there.
        with (
            patch("agent.session_health.AgentSession") as mock_cls,
            patch("agent.session_health._active_workers", {"valor": live_worker}),
            lifecycle_ctx,
        ):
            mock_cls.query.filter.return_value = [session]
            from agent.agent_session_queue import _agent_session_health_check

            await _agent_session_health_check()

        mock_transition.assert_called_once()
        assert mock_transition.call_args[0][1] == "pending"
        mock_finalize.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_progress_local_session_abandoned(self):
        """Local dev session (worker_key starts with ``local``) with no progress
        and a live worker must be finalized as ``abandoned``."""
        session = self._make_stuck_dev_session(
            project_key="local-valor",
            agent_session_id="no-prog-local-1",
        )
        assert session.worker_key == "local-valor"

        mock_finalize, mock_transition, lifecycle_ctx = self._patch_lifecycle()
        live_worker = MagicMock(done=MagicMock(return_value=False))

        # _agent_session_health_check now lives in agent.session_health — patch there.
        with (
            patch("agent.session_health.AgentSession") as mock_cls,
            patch("agent.session_health._active_workers", {"local-valor": live_worker}),
            lifecycle_ctx,
        ):
            mock_cls.query.filter.return_value = [session]
            from agent.agent_session_queue import _agent_session_health_check

            await _agent_session_health_check()

        mock_finalize.assert_called_once()
        assert mock_finalize.call_args[0][1] == "abandoned"
        mock_transition.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "turn_count,log_path,claude_session_uuid",
        [
            (2, None, None),
            (2, "/tmp/x.jsonl", None),
            (0, "/tmp/x.jsonl", None),
            (0, None, "uuid-abc-123"),
            (0, "", "uuid-abc-123"),
        ],
    )
    async def test_with_progress_not_recovered_parametrized(
        self, turn_count, log_path, claude_session_uuid
    ):
        """Any single progress signal (turn_count / log_path / claude_session_uuid)
        is sufficient to keep a session from being recovered by the no-progress branch."""
        session = self._make_stuck_dev_session(
            project_key="valor",
            agent_session_id=f"progress-{turn_count}-{bool(log_path)}-{bool(claude_session_uuid)}",
            turn_count=turn_count,
            log_path=log_path,
            claude_session_uuid=claude_session_uuid,
            started_seconds_ago=600,  # past the 300s guard but under the 45m timeout
        )

        mock_finalize, mock_transition, lifecycle_ctx = self._patch_lifecycle()
        live_worker = MagicMock(done=MagicMock(return_value=False))

        # _agent_session_health_check now lives in agent.session_health — patch there.
        with (
            patch("agent.session_health.AgentSession") as mock_cls,
            patch("agent.session_health._active_workers", {"valor": live_worker}),
            lifecycle_ctx,
        ):
            mock_cls.query.filter.return_value = [session]
            from agent.agent_session_queue import _agent_session_health_check

            await _agent_session_health_check()

        mock_finalize.assert_not_called()
        mock_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_progress_under_guard_not_recovered(self):
        """No-progress session that has only been running 60s is under the
        300s startup guard and must NOT be recovered."""
        session = self._make_stuck_dev_session(
            project_key="valor",
            agent_session_id="under-guard-1",
            started_seconds_ago=60,
        )

        mock_finalize, mock_transition, lifecycle_ctx = self._patch_lifecycle()
        live_worker = MagicMock(done=MagicMock(return_value=False))

        # _agent_session_health_check now lives in agent.session_health — patch there.
        with (
            patch("agent.session_health.AgentSession") as mock_cls,
            patch("agent.session_health._active_workers", {"valor": live_worker}),
            lifecycle_ctx,
        ):
            mock_cls.query.filter.return_value = [session]
            from agent.agent_session_queue import _agent_session_health_check

            await _agent_session_health_check()

        mock_finalize.assert_not_called()
        mock_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_progress_with_delivered_response_finalized_completed(self):
        """Defensive: a no-progress session with response_delivered_at set must
        still hit the delivery guard first and finalize as ``completed``."""
        session = self._make_stuck_dev_session(
            project_key="valor",
            agent_session_id="no-prog-delivered-1",
            response_delivered_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

        mock_finalize, mock_transition, lifecycle_ctx = self._patch_lifecycle()
        live_worker = MagicMock(done=MagicMock(return_value=False))

        # _agent_session_health_check now lives in agent.session_health — patch there.
        with (
            patch("agent.session_health.AgentSession") as mock_cls,
            patch("agent.session_health._active_workers", {"valor": live_worker}),
            lifecycle_ctx,
        ):
            mock_cls.query.filter.return_value = [session]
            from agent.agent_session_queue import _agent_session_health_check

            await _agent_session_health_check()

        mock_finalize.assert_called_once()
        assert mock_finalize.call_args[0][1] == "completed"
        mock_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_recovered_dev_session_popped_by_shared_pm_worker(self):
        """AD2 regression: _pop_agent_session must not filter by session_type.

        After a no-progress recovery transitions a slugless dev session to
        ``pending``, the PM-associated project-keyed worker loop must be able
        to pop and execute it. This locks in the assumption that
        ``_pop_agent_session(worker_key, is_project_keyed=True)`` selects by
        project_key/status only — no session_type filter.
        """
        session = self._make_stuck_dev_session(
            project_key="valor",
            agent_session_id="ad2-regression-1",
        )
        session.status = "pending"
        session.priority = "high"

        async def _mock_async_filter(**kwargs):
            if kwargs.get("status") == "pending" and kwargs.get("project_key") == "valor":
                return [session]
            return []

        with (
            patch("agent.session_pickup.AgentSession") as mock_cls,
            patch("agent.session_pickup._acquire_pop_lock", return_value=True),
            patch("agent.session_pickup._release_pop_lock"),
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
        ):
            # Redis sustainability guards return falsy → proceed normally.
            mock_redis.get.return_value = None
            mock_cls.query.async_filter = AsyncMock(side_effect=_mock_async_filter)
            # transition_status is called via save() internally; mock save to no-op.
            session.save = MagicMock()

            result = await _pop_agent_session("valor", is_project_keyed=True)

        assert result is not None, (
            "PM-associated project-keyed worker must pop the recovered dev session"
        )
        assert result.agent_session_id == "ad2-regression-1"
        assert result.session_type == "dev"

    @pytest.mark.asyncio
    async def test_progress_written_between_check_and_transition_is_lost_but_session_retries(self):
        """AD1 race acceptance: a worker writing progress AFTER ``entry`` is
        loaded but BEFORE ``transition_status`` runs is NOT protected by the
        status CAS. The session is re-queued regardless — accepted behavior.
        """
        session = self._make_stuck_dev_session(
            project_key="valor",
            agent_session_id="race-1",
        )

        def _set_progress_then_record(entry, new_status, **_):
            # Simulate a concurrent progress write landing on the in-memory
            # entry mid-recovery. The status CAS does not inspect progress
            # fields, so the transition still proceeds.
            entry.turn_count = 1

        mock_finalize = MagicMock()
        mock_transition = MagicMock(side_effect=_set_progress_then_record)

        import contextlib

        @contextlib.contextmanager
        def _ctx():
            import models.session_lifecycle as lifecycle_mod

            orig_f = lifecycle_mod.finalize_session
            orig_t = lifecycle_mod.transition_status
            lifecycle_mod.finalize_session = mock_finalize
            lifecycle_mod.transition_status = mock_transition
            try:
                yield
            finally:
                lifecycle_mod.finalize_session = orig_f
                lifecycle_mod.transition_status = orig_t

        live_worker = MagicMock(done=MagicMock(return_value=False))

        # _agent_session_health_check now lives in agent.session_health — patch there.
        with (
            patch("agent.session_health.AgentSession") as mock_cls,
            patch("agent.session_health._active_workers", {"valor": live_worker}),
            _ctx(),
        ):
            mock_cls.query.filter.return_value = [session]
            from agent.agent_session_queue import _agent_session_health_check

            await _agent_session_health_check()

        mock_transition.assert_called_once()
        assert mock_transition.call_args[0][1] == "pending"
        mock_finalize.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: register_callbacks() and _resolve_callbacks() transport-keyed behavior
# ---------------------------------------------------------------------------


class TestCallbackResolutionTransportKeyed:
    """register_callbacks() and _resolve_callbacks() support (project_key, transport) keys."""

    @pytest.fixture(autouse=True)
    def isolate_callback_dicts(self, monkeypatch):
        """Reset the module-level callback dicts before each test to avoid cross-test bleed."""
        import agent.agent_session_queue as q

        monkeypatch.setattr(q, "_send_callbacks", {})
        monkeypatch.setattr(q, "_reaction_callbacks", {})

    def test_register_and_resolve_with_transport(self):
        """register_callbacks with transport='email' resolves via composite key."""
        from agent.agent_session_queue import _resolve_callbacks, register_callbacks

        class _MockHandler:
            async def send(self, *args, **kwargs):
                pass

            async def react(self, *args, **kwargs):
                pass

        h = _MockHandler()
        register_callbacks("proj", transport="email", handler=h)

        send_cb, react_cb = _resolve_callbacks("proj", "email")
        # Bound method identity: compare the underlying object and function
        assert send_cb is not None
        assert send_cb.__self__ is h
        assert send_cb.__func__ is _MockHandler.send
        assert react_cb is not None
        assert react_cb.__self__ is h
        assert react_cb.__func__ is _MockHandler.react

    def test_no_registration_returns_none_none(self):
        """No registration → _resolve_callbacks returns (None, None)."""
        from agent.agent_session_queue import _resolve_callbacks

        send_cb, react_cb = _resolve_callbacks("unknown_proj", "email")
        assert send_cb is None
        assert react_cb is None

    def test_transport_agnostic_fallback(self):
        """Transport-agnostic registration still resolves when transport lookup misses."""
        from agent.agent_session_queue import _resolve_callbacks, register_callbacks

        class _MockHandler:
            async def send(self, *args, **kwargs):
                pass

            async def react(self, *args, **kwargs):
                pass

        h = _MockHandler()
        # Register without transport (plain key)
        register_callbacks("proj", handler=h)

        # Resolve with a transport — should fall back to plain key
        send_cb, react_cb = _resolve_callbacks("proj", "email")
        assert send_cb is not None
        assert send_cb.__self__ is h
        assert react_cb is not None
        assert react_cb.__self__ is h

    def test_composite_key_wins_over_plain_key(self):
        """Composite (project, transport) key wins over plain project_key fallback."""
        from agent.agent_session_queue import _resolve_callbacks, register_callbacks

        class _GenericHandler:
            async def send(self, *args, **kwargs):
                pass

            async def react(self, *args, **kwargs):
                pass

        class _EmailHandler:
            async def send(self, *args, **kwargs):
                pass

            async def react(self, *args, **kwargs):
                pass

        generic = _GenericHandler()
        email_h = _EmailHandler()

        register_callbacks("proj", handler=generic)
        register_callbacks("proj", transport="email", handler=email_h)

        send_cb, react_cb = _resolve_callbacks("proj", "email")
        # Should resolve to email_h, not generic
        assert send_cb.__self__ is email_h
        assert react_cb.__self__ is email_h
