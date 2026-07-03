"""Tests for asyncio.to_thread wrapping in agent_session_queue.py.

Covers get_active_session_for_chat and related async helpers.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: F401

import pytest

from agent.agent_session_queue import get_active_session_for_chat


@pytest.fixture
def mock_agent_session():
    """Patch AgentSession.query.filter for unit testing."""
    with patch("agent.agent_session_queue.AgentSession") as mock_cls:
        yield mock_cls


class TestGetActiveSessionForChat:
    """Tests for the get_active_session_for_chat helper."""

    def test_returns_none_when_no_sessions(self, mock_agent_session):
        """Should return None if no running sessions exist for chat_id."""
        mock_agent_session.query.filter.return_value = []
        result = asyncio.run(get_active_session_for_chat("12345"))
        assert result is None
        mock_agent_session.query.filter.assert_called_once_with(chat_id="12345", status="running")

    def test_returns_most_recent_session(self, mock_agent_session):
        """Should return the most recent running session by created_at."""
        older = MagicMock()
        older.created_at = 1000.0
        newer = MagicMock()
        newer.created_at = 2000.0

        mock_agent_session.query.filter.return_value = [older, newer]
        result = asyncio.run(get_active_session_for_chat("12345"))
        assert result is newer

    def test_returns_single_session(self, mock_agent_session):
        """Should return the only session when exactly one exists."""
        session = MagicMock()
        session.created_at = 1500.0

        mock_agent_session.query.filter.return_value = [session]
        result = asyncio.run(get_active_session_for_chat("67890"))
        assert result is session

    def test_handles_none_created_at(self, mock_agent_session):
        """Should handle sessions with None created_at (sorted as 0)."""
        no_ts = MagicMock()
        no_ts.created_at = None
        with_ts = MagicMock()
        with_ts.created_at = 500.0

        mock_agent_session.query.filter.return_value = [no_ts, with_ts]
        result = asyncio.run(get_active_session_for_chat("12345"))
        # with_ts has higher created_at, should be returned
        assert result is with_ts

    def test_uses_to_thread(self, mock_agent_session):
        """Verify the sync filter call is wrapped in asyncio.to_thread."""
        mock_agent_session.query.filter.return_value = []
        with patch(
            "agent.agent_session_queue.asyncio.to_thread", wraps=asyncio.to_thread
        ) as mock_to_thread:
            asyncio.run(get_active_session_for_chat("12345"))
            # to_thread should have been called (at least once for this function)
            assert mock_to_thread.called


class TestPushJobAsyncWrapping:
    """Verify _push_agent_session wraps sync Popoto calls in asyncio.to_thread."""

    def test_push_agent_session_superseding_uses_to_thread(self, mock_agent_session):
        """Superseding logic in _push_agent_session uses to_thread for sync filter+save."""
        from agent.agent_session_queue import _push_agent_session

        # Set up mocks
        old_session = MagicMock()
        old_session.status = "completed"
        old_session.agent_session_id = "old-session"
        mock_agent_session.query.filter.return_value = [old_session]
        mock_agent_session.async_create = AsyncMock(return_value=MagicMock())
        mock_agent_session.query.async_count = AsyncMock(return_value=1)

        with patch(
            "agent.agent_session_queue.asyncio.to_thread", wraps=asyncio.to_thread
        ) as mock_to_thread:
            asyncio.run(
                _push_agent_session(
                    project_key="test",
                    session_id="sess-1",
                    working_dir="/tmp",
                    message_text="hello",
                    sender_name="Test",
                    chat_id="123",
                    telegram_message_id=1,
                )
            )
            # to_thread should be called for superseding and lifecycle logging
            assert mock_to_thread.call_count >= 1


class TestPushAgentSessionPublish:
    """Verify _push_agent_session publishes to valor:sessions:new after enqueue."""

    def test_publish_called_after_enqueue(self, mock_agent_session):
        """Publish to valor:sessions:new should be called after session is written."""
        from agent.agent_session_queue import _push_agent_session

        mock_agent_session.query.filter.return_value = []
        mock_agent_session.async_create = AsyncMock(return_value=MagicMock())
        mock_agent_session.query.async_count = AsyncMock(return_value=1)

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.publish = MagicMock(return_value=1)
            asyncio.run(
                _push_agent_session(
                    project_key="test",
                    session_id="sess-pub",
                    working_dir="/tmp",
                    message_text="hello",
                    sender_name="Test",
                    chat_id="chat-1",
                    telegram_message_id=1,
                )
            )
            mock_redis.publish.assert_called_once()
            args = mock_redis.publish.call_args
            assert args[0][0] == "valor:sessions:new"
            import json

            payload = json.loads(args[0][1])
            assert payload["chat_id"] == "chat-1"
            assert payload["session_id"] == "sess-pub"

    def test_session_written_even_if_publish_fails(self, mock_agent_session):
        """Publish failure must not prevent session creation."""
        from agent.agent_session_queue import _push_agent_session

        mock_agent_session.query.filter.return_value = []
        mock_agent_session.async_create = AsyncMock(return_value=MagicMock())
        mock_agent_session.query.async_count = AsyncMock(return_value=1)

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.publish = MagicMock(side_effect=Exception("Redis down"))
            # Should not raise
            result = asyncio.run(
                _push_agent_session(
                    project_key="test",
                    session_id="sess-fail",
                    working_dir="/tmp",
                    message_text="hello",
                    sender_name="Test",
                    chat_id="chat-2",
                    telegram_message_id=2,
                )
            )
            # Session was still created
            mock_agent_session.async_create.assert_called_once()
            # Result is a count (not an exception)
            assert isinstance(result, int)


class TestEnqueueContinuationAsyncWrapping:
    """Verify _enqueue_nudge wraps sync filter in asyncio.to_thread."""

    def test_continuation_filter_uses_to_thread(self, mock_agent_session):
        """The session lookup in _enqueue_nudge should use to_thread."""
        from agent.agent_session_queue import _enqueue_nudge

        # Create a mock agent session
        mock_rj = MagicMock()
        mock_rj.session_id = "sess-1"
        mock_rj.project_key = "test"
        session = mock_rj

        # Return a session from filter -- used by get_authoritative_session
        existing_session = MagicMock()
        existing_session.status = "running"
        existing_session.session_id = "sess-1"
        existing_session.project_key = "test"
        existing_session.save = MagicMock()
        existing_session.log_lifecycle_transition = MagicMock()

        # Patch get_authoritative_session and transition_status at the source module
        # (_enqueue_nudge does a local import from models.session_lifecycle)
        with patch(
            "models.session_lifecycle.get_authoritative_session",
            return_value=existing_session,
        ):
            with patch(
                "models.session_lifecycle.transition_status",
            ) as mock_transition:
                with patch(
                    "agent.agent_session_queue.asyncio.to_thread",
                    wraps=asyncio.to_thread,
                ) as mock_to_thread:
                    with patch("agent.agent_session_queue._ensure_worker"):
                        asyncio.run(
                            _enqueue_nudge(
                                session=session,
                                branch_name="test-branch",
                                task_list_id="tl-1",
                                auto_continue_count=1,
                                output_msg="test output",
                                nudge_feedback="continue",
                            )
                        )
                        # to_thread should be called for the re-read
                        assert mock_to_thread.called
                        # transition_status should be called directly
                        # (no update_session wrapper — saves a Redis re-read)
                        mock_transition.assert_called_once()


class TestEnqueueSessionTypeOmissionWarning:
    """enqueue_agent_session warns when both session_type and project_config are omitted.

    Finding 2 safety net: a scanner that dropped persona resolution silently
    defaults a teammate-configured chat to an eng PM<->Dev loop. The greppable
    warning surfaces that call shape without changing the eng default.
    """

    def _run_enqueue(self, **overrides):
        from agent.agent_session_queue import enqueue_agent_session

        kwargs = dict(
            project_key="testproj",
            session_id="s1",
            working_dir="/tmp/test",
            message_text="hello",
            sender_name="Alice",
            chat_id="100",
            telegram_message_id=1,
        )
        kwargs.update(overrides)

        with (
            patch(
                "agent.agent_session_queue._push_agent_session",
                new_callable=AsyncMock,
                return_value=1,
            ) as push,
            patch("agent.agent_session_queue._ensure_worker"),
        ):
            asyncio.run(enqueue_agent_session(**kwargs))
        return push

    def test_warns_when_both_omitted(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            push = self._run_enqueue()
        assert any(
            "[enqueue] session_type omitted AND project_config omitted" in r.getMessage()
            for r in caplog.records
        )
        # Effective default is still eng.
        from config.enums import SessionType

        assert push.call_args[1]["session_type"] == SessionType.ENG

    def test_no_warning_when_project_config_passed(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            self._run_enqueue(project_config={"_key": "testproj"})
        assert not any(
            "[enqueue] session_type omitted AND project_config omitted" in r.getMessage()
            for r in caplog.records
        )

    def test_no_warning_when_session_type_passed(self, caplog):
        import logging

        from config.enums import SessionType

        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            push = self._run_enqueue(session_type=SessionType.ENG)
        assert not any(
            "[enqueue] session_type omitted AND project_config omitted" in r.getMessage()
            for r in caplog.records
        )
        assert push.call_args[1]["session_type"] == SessionType.ENG


class TestNotifyListenerNusubSelfCheck:
    """Unit tests for the NUMSUB subscribe-time self-check added in #1804.

    These tests exercise the _listen_in_thread inner function via the
    _session_notify_listener coroutine.  Redis and time.sleep are mocked so
    the tests run in <100 ms.
    """

    def _make_mocks(self, numsub_return=None, numsub_raise=None):
        """Build mock redis conn+pubsub and a mock POPOTO pool.

        Uses bytes-keyed list-of-tuples — the shape redis-py returns when
        ``decode_responses=False`` (POPOTO pool default, #1811).  This ensures
        ``test_numsub_ok_proceeds_to_listen`` would have caught the regression.
        """
        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])  # empty; thread exits cleanly

        mock_conn = MagicMock()
        mock_conn.pubsub.return_value = mock_pubsub
        if numsub_raise is not None:
            mock_conn.pubsub_numsub.side_effect = numsub_raise
        else:
            count = numsub_return if numsub_return is not None else 1
            # Use bytes-keyed list-of-tuples to match production decode_responses=False
            mock_conn.pubsub_numsub.return_value = [(b"valor:sessions:new", count)]

        mock_popoto = MagicMock()
        mock_popoto.connection_pool.connection_kwargs = {
            "host": "localhost",
            "port": 6379,
            "db": 0,
        }
        return mock_conn, mock_pubsub, mock_popoto

    def _run_listener_briefly(self, mock_conn, mock_popoto):
        """Run _session_notify_listener for a short time then cancel it."""
        import json

        import redis as _redis_module

        from agent.agent_session_queue import _session_notify_listener

        async def run():
            with (
                patch("popoto.redis_db.POPOTO_REDIS_DB", mock_popoto),
                patch("agent.agent_session_queue.json", wraps=json),
                patch.object(_redis_module, "Redis", return_value=mock_conn),
                patch("time.sleep"),  # no real delays in NUMSUB retry loop
            ):
                task = asyncio.create_task(_session_notify_listener())
                await asyncio.sleep(0.2)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())

    def test_numsub_ok_proceeds_to_listen(self):
        """When NUMSUB >= 1, _listen_in_thread calls pubsub.listen()."""
        mock_conn, mock_pubsub, mock_popoto = self._make_mocks(numsub_return=1)
        self._run_listener_briefly(mock_conn, mock_popoto)
        assert mock_conn.pubsub_numsub.called, (
            "pubsub_numsub() was never called — NUMSUB self-check not applied"
        )
        assert mock_pubsub.listen.called, (
            "pubsub.listen() was not called — listener should proceed when NUMSUB >= 1"
        )

    def test_numsub_zero_skips_listen_and_logs_warning(self, caplog):
        """When NUMSUB == 0 after all retries, _listen_in_thread returns early.

        pubsub.listen() must not be called; the outer while-True loop will
        re-subscribe after the 5 s backoff.  A WARNING must be logged.
        """
        import logging

        mock_conn, mock_pubsub, mock_popoto = self._make_mocks(numsub_return=0)

        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            self._run_listener_briefly(mock_conn, mock_popoto)

        assert not mock_pubsub.listen.called, (
            "pubsub.listen() must NOT be called when NUMSUB == 0; "
            "listener should return early and let the outer loop re-subscribe"
        )
        assert any("NUMSUB check reports 0" in r.getMessage() for r in caplog.records), (
            "Expected WARNING mentioning 'NUMSUB check reports 0'"
        )
        # Teardown (finally) must have run — unsubscribe() is called
        mock_conn.pubsub.return_value.unsubscribe.assert_called()

    def test_numsub_raises_no_crash_and_logs_warning(self, caplog):
        """When pubsub_numsub() raises, the listener does not crash.

        A WARNING must be logged, pubsub.listen() must not be called, and
        teardown must still run (observable by the test completing without error).
        """
        import logging

        mock_conn, mock_pubsub, mock_popoto = self._make_mocks(
            numsub_raise=RuntimeError("redis gone")
        )

        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            self._run_listener_briefly(mock_conn, mock_popoto)

        # Test completing without exception proves no crash.
        assert not mock_pubsub.listen.called, (
            "pubsub.listen() must NOT be called when NUMSUB raises"
        )
        assert any("NUMSUB check raised" in r.getMessage() for r in caplog.records), (
            "Expected WARNING about the NUMSUB exception"
        )
        # Teardown (finally) must have run — unsubscribe() is called
        mock_conn.pubsub.return_value.unsubscribe.assert_called()


class TestNumsubCount:
    """Direct unit tests for the _numsub_count helper (#1811).

    Covers both reply shapes (list-of-tuples and dict) and both key encodings
    (bytes and str), plus edge cases, without touching the thread/asyncio machinery.
    """

    def setup_method(self):
        from agent.agent_session_queue import _numsub_count

        self.fn = _numsub_count
        self.ch = "valor:sessions:new"

    # --- list-of-tuples shapes ---

    def test_bytes_key_list_correct_count(self):
        """Production shape: bytes-keyed list-of-tuples from decode_responses=False."""
        assert self.fn([(b"valor:sessions:new", 1)], self.ch) == 1

    def test_bytes_key_list_higher_count(self):
        assert self.fn([(b"valor:sessions:new", 3)], self.ch) == 3

    def test_str_key_list_correct_count(self):
        """str-keyed list (decode_responses=True or mocked)."""
        assert self.fn([("valor:sessions:new", 2)], self.ch) == 2

    def test_wrong_channel_list_returns_zero(self):
        assert self.fn([(b"other:channel", 5)], self.ch) == 0

    def test_empty_list_returns_zero(self):
        assert self.fn([], self.ch) == 0

    # --- dict shapes ---

    def test_bytes_key_dict_correct_count(self):
        """Some redis-py versions return a bytes-keyed dict."""
        assert self.fn({b"valor:sessions:new": 1}, self.ch) == 1

    def test_str_key_dict_correct_count(self):
        assert self.fn({"valor:sessions:new": 1}, self.ch) == 1

    def test_wrong_channel_dict_returns_zero(self):
        assert self.fn({b"other:channel": 7}, self.ch) == 0

    def test_empty_dict_returns_zero(self):
        assert self.fn({}, self.ch) == 0


class TestNotifyHealthcheckWatchdog:
    """Unit tests for the D4 periodic off-path pubsub liveness watchdog (#1817).

    `_notify_healthcheck_watchdog` runs alongside `_session_notify_listener`'s
    blocking pubsub thread and probes NUMSUB on a SEPARATE short-lived Redis
    connection every NOTIFY_HEALTHCHECK_INTERVAL seconds. These tests drive
    the watchdog directly (not through the full listener) so timing is fast
    and deterministic; the listener's own subscribe/resubscribe machinery is
    covered by TestNotifyListenerNusubSelfCheck above.
    """

    def _run_watchdog_ticks(self, handle, numsub_side_effect, interval=0.05, ticks=1):
        """Run _notify_healthcheck_watchdog for `ticks` probe cycles then cancel."""
        import redis as _redis_module

        from agent.agent_session_queue import _notify_healthcheck_watchdog

        mock_probe_conn = MagicMock()
        mock_probe_conn.pubsub_numsub.side_effect = numsub_side_effect

        mock_popoto = MagicMock()
        mock_popoto.connection_pool.connection_kwargs = {
            "host": "localhost",
            "port": 6379,
            "db": 0,
        }

        async def run():
            with (
                patch("agent.agent_session_queue.NOTIFY_HEALTHCHECK_INTERVAL", interval),
                patch("popoto.redis_db.POPOTO_REDIS_DB", mock_popoto),
                patch.object(_redis_module, "Redis", return_value=mock_probe_conn),
            ):
                task = asyncio.create_task(_notify_healthcheck_watchdog(handle))
                await asyncio.sleep(interval * (ticks + 2))
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())
        return mock_probe_conn

    def test_confirmed_drop_closes_handle_pubsub_and_warns(self, caplog):
        """A CONFIRMED NUMSUB==0 forces a resubscribe: closes the handle's
        pubsub and logs a WARNING — within NOTIFY_HEALTHCHECK_INTERVAL."""
        import logging

        from agent.agent_session_queue import _ListenerPubsubHandle

        handle = _ListenerPubsubHandle()
        mock_pubsub = MagicMock()
        handle.pubsub = mock_pubsub

        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            self._run_watchdog_ticks(
                handle, numsub_side_effect=lambda *a, **k: [(b"valor:sessions:new", 0)]
            )

        mock_pubsub.close.assert_called()
        assert any("notify subscription dropped" in r.getMessage() for r in caplog.records), (
            "Expected a WARNING announcing the forced resubscribe"
        )

    def test_healthy_subscription_does_not_close_pubsub(self):
        """NUMSUB >= 1 (healthy) must never close the listener's pubsub."""
        from agent.agent_session_queue import _ListenerPubsubHandle

        handle = _ListenerPubsubHandle()
        mock_pubsub = MagicMock()
        handle.pubsub = mock_pubsub

        self._run_watchdog_ticks(
            handle, numsub_side_effect=lambda *a, **k: [(b"valor:sessions:new", 1)]
        )

        mock_pubsub.close.assert_not_called()

    def test_transient_probe_error_does_not_tear_down_listener(self, caplog):
        """A probe-side exception (Redis transiently unreachable) must be
        logged and skipped — it must NOT close the listener's pubsub."""
        import logging

        from agent.agent_session_queue import _ListenerPubsubHandle

        handle = _ListenerPubsubHandle()
        mock_pubsub = MagicMock()
        handle.pubsub = mock_pubsub

        with caplog.at_level(logging.WARNING, logger="agent.agent_session_queue"):
            self._run_watchdog_ticks(handle, numsub_side_effect=RuntimeError("redis unreachable"))

        mock_pubsub.close.assert_not_called()
        assert any("NUMSUB probe raised" in r.getMessage() for r in caplog.records)

    def test_handle_with_no_pubsub_yet_does_not_crash_on_drop(self):
        """A confirmed drop before the listener has published its pubsub
        (handle.pubsub is still None, e.g. mid-establishment) must not raise."""
        from agent.agent_session_queue import _ListenerPubsubHandle

        handle = _ListenerPubsubHandle()
        assert handle.pubsub is None

        # Must not raise.
        self._run_watchdog_ticks(
            handle, numsub_side_effect=lambda *a, **k: [(b"valor:sessions:new", 0)]
        )


class TestNotifyListenerPreservesNoneSocketTimeout:
    """D4 (issue #1817): the listen() connection's socket_timeout=None must
    stay unconditional — a finite timeout there was already tried and
    reverted (spurious "Timeout reading from socket" + a dropped-notification
    window). This guards against a future regression reintroducing one."""

    def test_listener_connection_uses_socket_timeout_none(self):
        import json

        import redis as _redis_module

        from agent.agent_session_queue import _session_notify_listener

        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])

        mock_conn = MagicMock()
        mock_conn.pubsub.return_value = mock_pubsub
        mock_conn.pubsub_numsub.return_value = [(b"valor:sessions:new", 1)]

        mock_popoto = MagicMock()
        mock_popoto.connection_pool.connection_kwargs = {
            "host": "localhost",
            "port": 6379,
            "db": 0,
        }

        redis_calls = []

        def _redis_ctor(*args, **kwargs):
            redis_calls.append(kwargs)
            return mock_conn

        async def run():
            with (
                patch("popoto.redis_db.POPOTO_REDIS_DB", mock_popoto),
                patch("agent.agent_session_queue.json", wraps=json),
                patch.object(_redis_module, "Redis", side_effect=_redis_ctor),
                patch("time.sleep"),
            ):
                task = asyncio.create_task(_session_notify_listener())
                await asyncio.sleep(0.2)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())

        assert redis_calls, "Expected at least one Redis connection constructed"
        listener_calls = [c for c in redis_calls if c.get("socket_timeout") is None]
        assert listener_calls, (
            "The listen() connection must be constructed with socket_timeout=None "
            "— this was reverted once already after causing spurious timeouts "
            "and a dropped-notification window; do not reintroduce a finite "
            "timeout here."
        )
