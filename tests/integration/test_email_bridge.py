"""
Integration tests for the email bridge inbound path.

Tests _process_inbound_email() with a real enqueue_agent_session() call
against the test Redis instance (provided by the autouse redis_test_db
fixture in tests/conftest.py).

Design:
- enqueue_agent_session() is mocked to avoid Popoto persistence complexity
  in tests — the unit under test is the routing/dispatch logic in
  _process_inbound_email(), not Popoto internals.
- Thread-continuation Redis lookups are exercised against the real test Redis
  db (via a patched _get_redis() that points at db=1).
- Unknown sender, active-project guard, and extra_context propagation are
  all verified by inspecting mock call args.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
import redis

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parsed_email(
    from_addr: str = "alice@example.com",
    subject: str = "Help with my account",
    body: str = "Hello, I need some assistance.",
    message_id: str = "<msg-001@example.com>",
    in_reply_to: str | None = None,
) -> dict:
    """Return a minimal parsed email dict matching parse_email_message() output."""
    return {
        "from_addr": from_addr,
        "subject": subject,
        "body": body,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
    }


def _project_config(key: str = "test-project") -> dict:
    """Return a minimal project config dict with email.contacts section."""
    return {
        "_key": key,
        "name": key,
        "working_directory": "/tmp/test-project",
        "email": {
            "contacts": {
                "alice@example.com": {"name": "Alice"},
            }
        },
    }


def _projects_json(project_key: str = "test-project") -> dict:
    """Return a minimal projects.json config dict."""
    return {
        "projects": {
            project_key: _project_config(project_key),
        }
    }


def _test_redis() -> redis.Redis:
    """Return a Redis connection to the test db (db=1, matching conftest fixture)."""
    return redis.Redis(db=1, decode_responses=True)


# ---------------------------------------------------------------------------
# Tests: inbound email → session enqueued
# ---------------------------------------------------------------------------


class TestProcessInboundEmail:
    """Integration tests for _process_inbound_email() → enqueue_agent_session()."""

    @pytest.mark.asyncio
    async def test_new_inbound_email_enqueues_session(self):
        """A new inbound email calls enqueue_agent_session with correct args."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "test-project"
        project = _project_config(project_key)
        config = _projects_json(project_key)

        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT["alice@example.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                # Patch _get_redis so it uses the test db (not db=0)
                test_r = _test_redis()
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(_parsed_email(), config)
                test_r.close()

        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_called_once()
        kwargs = mock_enqueue.call_args.kwargs

        assert kwargs["project_key"] == project_key
        assert kwargs["message_text"] == "Hello, I need some assistance."
        assert kwargs["sender_name"] == "alice@example.com"
        assert kwargs["chat_id"] == "alice@example.com"
        assert kwargs["telegram_message_id"] == 0  # sentinel for email sessions
        assert kwargs["working_dir"] == "/tmp/test-project"

    @pytest.mark.asyncio
    async def test_inbound_email_sets_email_extra_context(self):
        """Extra context passed to enqueue_agent_session contains transport and email metadata."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "test-project"
        project = _project_config(project_key)
        config = _projects_json(project_key)

        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT["alice@example.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                test_r = _test_redis()
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(
                        _parsed_email(
                            message_id="<msg-42@example.com>",
                            subject="Billing question",
                        ),
                        config,
                    )
                test_r.close()

        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_called_once()
        extra = mock_enqueue.call_args.kwargs.get("extra_context_overrides", {})

        assert extra.get("transport") == "email"
        assert extra.get("email_message_id") == "<msg-42@example.com>"
        assert extra.get("email_from") == "alice@example.com"
        assert extra.get("email_subject") == "Billing question"

    @pytest.mark.asyncio
    async def test_unknown_sender_discards_email(self):
        """Email from an unknown sender is discarded — enqueue_agent_session not called."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "test-project"
        config = _projects_json(project_key)

        # Do NOT add unknown@stranger.com to EMAIL_TO_PROJECT
        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                test_r = _test_redis()
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(
                        _parsed_email(from_addr="unknown@stranger.com"),
                        config,
                    )
                test_r.close()

        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_thread_continuation_reuses_session_id(self):
        """When In-Reply-To matches a stored Message-ID, the original session_id is reused."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "test-project"
        project = _project_config(project_key)
        config = _projects_json(project_key)

        # Pre-seed the thread-continuation mapping in test Redis (db=1)
        original_session_id = f"email_{project_key}_alice_at_example_com_{int(time.time()) - 100}"
        test_r = _test_redis()
        test_r.set(
            "email:msgid:<outbound-msg-001@example.com>",
            original_session_id,
            ex=172800,
        )

        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT["alice@example.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                # Patch _get_redis to return our pre-seeded test db connection
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(
                        _parsed_email(
                            message_id="<reply-001@example.com>",
                            in_reply_to="<outbound-msg-001@example.com>",
                            body="Thanks for your help!",
                        ),
                        config,
                    )

        finally:
            test_r.close()
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_called_once()
        called_session_id = mock_enqueue.call_args.kwargs.get("session_id")
        assert called_session_id == original_session_id, (
            f"Expected session_id={original_session_id!r} from thread continuation, "
            f"got {called_session_id!r}"
        )


# ---------------------------------------------------------------------------
# Tests: health timestamp in _email_inbox_loop
# ---------------------------------------------------------------------------


class _BreakLoopError(Exception):
    """Sentinel exception to break out of the infinite polling loop after one iteration."""


class TestHealthTimestamp:
    """_email_inbox_loop() writes email:last_poll_ts to Redis on each poll."""

    @pytest.mark.asyncio
    async def test_health_timestamp_written_after_poll(self):
        """After one successful poll iteration, email:last_poll_ts is set in Redis."""
        from bridge.email_bridge import REDIS_LAST_POLL_KEY, _email_inbox_loop

        test_r = _test_redis()
        # Ensure the key does not exist before the test
        test_r.delete(REDIS_LAST_POLL_KEY)

        imap_config = {
            "host": "imap.example.com",
            "port": 993,
            "user": "test@example.com",
            "password": "secret",
            "ssl": True,
        }

        call_count = 0

        async def _break_after_first(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise _BreakLoopError("break after first iteration")

        try:
            with patch(
                "bridge.email_bridge._poll_imap",
                new_callable=AsyncMock,
                return_value=[],
            ):
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    with patch(
                        "bridge.email_bridge.asyncio.sleep",
                        side_effect=_break_after_first,
                    ):
                        await _email_inbox_loop(imap_config, config={})
        except _BreakLoopError:
            pass

        # Verify health timestamp was written
        ts_value = test_r.get(REDIS_LAST_POLL_KEY)
        assert ts_value is not None, (
            f"Expected {REDIS_LAST_POLL_KEY} to be set in Redis after one poll"
        )
        # Verify it's a valid float timestamp
        ts_float = float(ts_value)
        assert ts_float > 0
        assert ts_float <= time.time() + 1  # not in the future

        # Cleanup
        test_r.delete(REDIS_LAST_POLL_KEY)
        test_r.close()
