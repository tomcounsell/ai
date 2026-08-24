"""Tests for agent/output_handler.py: delivery outcomes.

Covers deferred self-draft persistence and release, the DeliveryOutcome
returned by send, and deliver_system_notice.
Split out of the former ``tests/unit/test_output_handler.py`` monolith (#2879). The
``output_handler`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from agent.output_handler import (
    DeliveryOutcome,
    TelegramRelayOutputHandler,
    deliver_system_notice,
)


class TestDeferredSelfDraftPersistence:
    """Tests that deferred_self_draft_pending + deferred_self_draft_text are
    persisted to AgentSession.extra_context when steering_deferred=True.

    The persisted flag is the cross-process detection signal the health checker
    reads to decide whether to deliver a fallback.  The steering queue CANNOT be
    used because the agent drains it at turn start, leaving it empty by
    finalization time.
    """

    def _make_handler(self):
        from unittest.mock import MagicMock

        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler()
        h._redis = MagicMock()
        return h

    def _make_session(self, *, session_id="sess-persist", extra_context=None):
        from unittest.mock import MagicMock

        session = MagicMock()
        session.session_id = session_id
        session.extra_context = extra_context or {}
        return session

    def test_persists_pending_flag_and_text_when_steering_deferred(self):
        """When steering_deferred=True, extra_context gains deferred_self_draft_pending=True
        and deferred_self_draft_text=<the original text> before the early return."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = self._make_session()

        drafted = MessageDraft(text="", full_output_file=None, needs_self_draft=True, artifacts={})

        auth_session = MagicMock()
        auth_session.extra_context = {}
        saved_contexts: list[dict] = []

        def _capture_save(update_fields=None, **_kw):
            saved_contexts.append(dict(auth_session.extra_context))

        auth_session.save = _capture_save

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message", return_value=True),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
            patch(
                "models.session_lifecycle.get_authoritative_session",
                return_value=auth_session,
            ),
        ):
            asyncio.run(handler.send("123", "This is the deferred text", 0, session=session))

        # A save with the deferred keys must have occurred.
        assert saved_contexts, "save must have been called with extra_context update"
        last_ctx = saved_contexts[-1]
        assert last_ctx.get("deferred_self_draft_pending") is True
        assert last_ctx.get("deferred_self_draft_text") == "This is the deferred text"

    def test_persist_failure_is_logged_not_swallowed(self):
        """If the extra_context persist fails, a WARNING is logged (not swallowed).
        The file dual-write and early return still complete normally."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        file_handler = MagicMock()
        file_handler.send = AsyncMock()
        handler._file_handler = file_handler

        session = self._make_session()

        drafted = MessageDraft(text="", full_output_file=None, needs_self_draft=True, artifacts={})

        def _raise(*_a, **_kw):
            raise RuntimeError("Redis unavailable")

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message", return_value=True),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
            patch("models.session_lifecycle.get_authoritative_session", side_effect=_raise),
            patch("agent.output_handler.logger") as mock_logger,
        ):
            asyncio.run(handler.send("123", "text", 0, session=session))

        # Must log at WARNING level (not silently swallow).
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("sess-persist" in w or "deferred" in w.lower() for w in warning_calls), (
            f"Expected a WARNING about persist failure; got calls: {warning_calls}"
        )

    def test_persist_uses_authoritative_re_read_not_stale_session(self):
        """The RMW re-reads the authoritative session before merging, avoiding a
        last-writer-wins clobber of concurrent extra_context writes."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = self._make_session()
        # Pre-load a key that a concurrent writer might have set.
        session.extra_context = {"transport": "telegram"}

        drafted = MessageDraft(text="", full_output_file=None, needs_self_draft=True, artifacts={})

        auth_session = MagicMock()
        # Auth session has a different (newer) extra_context.
        auth_session.extra_context = {"transport": "telegram", "other_key": "other_val"}
        auth_session.save = MagicMock()

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch("agent.steering.peek_steering_sender", return_value=None),
            patch("agent.steering.push_steering_message", return_value=True),
            patch("agent.steering.bump_self_draft_attempts", return_value=1),
            patch(
                "models.session_lifecycle.get_authoritative_session",
                return_value=auth_session,
            ),
        ):
            asyncio.run(handler.send("123", "text", 0, session=session))

        # The save target must be the auth_session (re-read), not the stale local session.
        auth_session.save.assert_called()
        # The re-read's pre-existing key must survive (not clobbered).
        assert auth_session.extra_context.get("other_key") == "other_val", (
            "concurrent extra_context key must not be clobbered by the RMW"
        )
        # The deferred keys must be present.
        assert auth_session.extra_context.get("deferred_self_draft_pending") is True


class TestDeferredSelfDraftRelease:
    def _make_handler(self):
        from unittest.mock import MagicMock

        from agent.output_handler import TelegramRelayOutputHandler

        h = TelegramRelayOutputHandler()
        h._redis = MagicMock()
        return h

    def _bypass_drafter(self, _input, *, session=None, medium="telegram"):
        from bridge.message_drafter import MessageDraft

        return MessageDraft(text=_input, artifacts={})

    def test_clean_send_clears_pending_flag_and_text(self):
        """A successful (non-deferred) send clears a previously-set
        deferred_self_draft_pending flag and its held text via the
        authoritative RMW, so the terminal flush never re-delivers it."""
        from unittest.mock import AsyncMock, MagicMock, patch

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-release"
        session.extra_context = {
            "transport": "telegram",
            "deferred_self_draft_pending": True,
            "deferred_self_draft_text": "the originally rejected text",
        }

        auth_session = MagicMock()
        auth_session.extra_context = dict(session.extra_context)
        auth_session.save = MagicMock()

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch("agent.steering.reset_self_draft_attempts"),
            patch(
                "models.session_lifecycle.get_authoritative_session",
                return_value=auth_session,
            ),
        ):
            outcome = asyncio.run(handler.send("123", "the successful rewrite", 0, session=session))

        assert outcome == DeliveryOutcome.sent
        auth_session.save.assert_called()
        assert "deferred_self_draft_pending" not in auth_session.extra_context
        assert "deferred_self_draft_text" not in auth_session.extra_context
        # Concurrent keys must survive the merge.
        assert auth_session.extra_context.get("transport") == "telegram"

    def test_clean_send_without_pending_flag_does_not_touch_extra_context(self):
        """When the flag was never set, the clean path must not perform the
        authoritative RMW at all (cheap-check gate)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "sess-no-release-needed"
        session.extra_context = {"transport": "telegram"}

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch("agent.steering.reset_self_draft_attempts"),
            patch("models.session_lifecycle.get_authoritative_session") as mock_auth,
        ):
            outcome = asyncio.run(handler.send("123", "hello there", 0, session=session))

        assert outcome == DeliveryOutcome.sent
        mock_auth.assert_not_called()


class TestSendReturnsDeliveryOutcome:
    """Every exit path of send() returns the correct DeliveryOutcome."""

    def _make_handler(self):
        h = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        h._redis = MagicMock()
        return h

    def _bypass_drafter(self, _input, *, session=None, medium="telegram"):
        from bridge.message_drafter import MessageDraft

        return MessageDraft(text=_input, artifacts={})

    def test_returns_sent_on_successful_outbox_write(self):
        """A clean telegram send returns DeliveryOutcome.sent."""
        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-sent"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = False

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=self._bypass_drafter),
        ):
            outcome = asyncio.run(handler.send("123", "hello there", 0, session=session))

        assert outcome == DeliveryOutcome.sent
        handler._redis.rpush.assert_called_once()

    def test_returns_dropped_empty_on_empty_text(self):
        """Empty text short-circuits with DeliveryOutcome.dropped_empty."""
        handler = self._make_handler()

        outcome = asyncio.run(handler.send("123", "", 0))

        assert outcome == DeliveryOutcome.dropped_empty
        handler._redis.rpush.assert_not_called()

    def test_returns_sent_even_when_drafter_raises(self):
        """A drafter exception falls through to raw text and returns sent
        (failure-mode: drafter is a guard, never a blocker)."""
        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-drafter-boom"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = False

        with patch(
            "bridge.message_drafter.draft_message",
            AsyncMock(side_effect=RuntimeError("drafter broken")),
        ):
            outcome = asyncio.run(
                handler.send("123", "Raw text survives? yes.", 0, session=session)
            )

        assert outcome == DeliveryOutcome.sent
        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["text"] == "Raw text survives? yes."

    def test_returns_deferred_self_draft_when_steering_injected(self):
        """When self-draft steering is injected, send() defers and returns
        DeliveryOutcome.deferred_self_draft without an outbox write."""
        from bridge.message_drafter import MessageDraft

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-deferred"
        session.extra_context = {"transport": "telegram"}

        drafted = MessageDraft(text="", full_output_file=None, needs_self_draft=True, artifacts={})

        with (
            patch("bridge.message_drafter.draft_message", AsyncMock(return_value=drafted)),
            patch.object(handler, "_inject_self_draft_steering", MagicMock(return_value=True)),
        ):
            outcome = asyncio.run(
                handler.send("123", "needs a self draft? yes", 0, session=session)
            )

        assert outcome == DeliveryOutcome.deferred_self_draft
        handler._redis.rpush.assert_not_called()

    def test_returns_suppressed_redundant(self):
        """A redundancy-filter suppression (SDLC session, reply anchor present)
        returns DeliveryOutcome.suppressed_redundant."""
        import time

        from bridge.redundancy_filter import SuppressionVerdict

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-redund"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = True
        session.status = "active"
        session.recent_sent_drafts = [{"ts": time.time(), "text": "status", "artifacts": {}}]
        session.session_events = None

        verdict = SuppressionVerdict(
            action="suppress", reason="jaccard=0.9", jaccard=0.9, matched_index=0
        )

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch("bridge.redundancy_filter.should_suppress", return_value=verdict),
        ):
            outcome = asyncio.run(handler.send("-100123", "status", 42, session=session))

        assert outcome == DeliveryOutcome.suppressed_redundant

    def test_returns_suppressed_rtr(self):
        """A read-the-room suppression (reply anchor present) returns
        DeliveryOutcome.suppressed_rtr."""
        from bridge.read_the_room import RoomVerdict

        handler = self._make_handler()
        session = MagicMock()
        session.session_id = "ret-rtr"
        session.extra_context = {"transport": "telegram"}
        session.is_sdlc = False
        session.session_events = None

        with (
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=self._bypass_drafter),
            ),
            patch(
                "bridge.read_the_room.read_the_room",
                AsyncMock(return_value=RoomVerdict(action="suppress", reason="redundant")),
            ),
        ):
            outcome = asyncio.run(handler.send("-100123", "x" * 250, 42, session=session))

        assert outcome == DeliveryOutcome.suppressed_rtr


class TestDeliverSystemNotice:
    """deliver_system_notice registered-handler / file-fallback / never-raises."""

    def _notice_entry(self, *, session_id="notice-sess", transport="telegram"):
        entry = MagicMock()
        entry.session_id = session_id
        entry.agent_session_id = session_id
        entry.chat_id = "55555"
        entry.telegram_message_id = 7
        entry.project_key = "test-notice-proj"
        entry.extra_context = {"transport": transport}
        return entry

    def test_registered_handler_receives_notice_and_writes_outbox(self):
        """With a registered send callback, the notice traverses the real
        handler and lands on telegram:outbox:{session_id}."""
        entry = self._notice_entry(session_id="notice-registered")

        # Real handler with a mocked Redis client — the notice must reach it.
        handler = TelegramRelayOutputHandler(redis_url="redis://localhost:6379/0")
        handler._redis = MagicMock()

        def _bypass_drafter(_input, *, session=None, medium="telegram"):
            from bridge.message_drafter import MessageDraft

            return MessageDraft(text=_input, artifacts={})

        with (
            patch(
                "agent.agent_session_queue._resolve_callbacks",
                return_value=(handler.send, None),
            ),
            patch(
                "bridge.message_drafter.draft_message",
                AsyncMock(side_effect=_bypass_drafter),
            ),
        ):
            result = asyncio.run(deliver_system_notice(entry, "System notice: service degraded."))

        assert result is True
        handler._redis.rpush.assert_called_once()
        key = handler._redis.rpush.call_args[0][0]
        assert key == "telegram:outbox:notice-registered"
        payload = json.loads(handler._redis.rpush.call_args[0][1])
        assert payload["text"] == "System notice: service degraded."
        assert payload["chat_id"] == "55555"
        assert payload["reply_to"] == 7
        assert payload["session_id"] == "notice-registered"

    def test_no_registration_falls_back_to_file_output_handler(self, tmp_path, monkeypatch):
        """With NO registered callback, the notice is written via
        FileOutputHandler (dev / non-bridge fallback)."""
        import agent.output_handler as oh

        entry = self._notice_entry(session_id="notice-filefallback")

        # Redirect FileOutputHandler's default dir to a temp path so we don't
        # pollute the repo's logs/worker/ tree.
        monkeypatch.setattr(oh, "WORKER_LOGS_DIR", tmp_path)

        with patch(
            "agent.agent_session_queue._resolve_callbacks",
            return_value=(None, None),
        ):
            result = asyncio.run(deliver_system_notice(entry, "Fallback notice text."))

        assert result is True
        log_file = tmp_path / "notice-filefallback.log"
        assert log_file.exists()
        assert "Fallback notice text." in log_file.read_text()

    def test_callback_exception_is_logged_and_swallowed(self):
        """A raising send callback → WARNING logged, no exception propagates,
        returns False (never-raises contract)."""

        async def _boom(*_a, **_kw):
            raise RuntimeError("send callback exploded")

        entry = self._notice_entry(session_id="notice-boom")

        with (
            patch(
                "agent.agent_session_queue._resolve_callbacks",
                return_value=(_boom, None),
            ),
            patch("agent.output_handler.logger") as mock_logger,
        ):
            # Must NOT raise.
            result = asyncio.run(deliver_system_notice(entry, "will fail"))

        assert result is False
        mock_logger.warning.assert_called()
        assert any(
            "notice-boom" in str(c) or "delivery failed" in str(c).lower()
            for c in mock_logger.warning.call_args_list
        )

    def test_empty_message_is_noop_with_debug_log(self):
        """An empty message is a no-op: no callback resolution, debug log,
        returns False."""
        entry = self._notice_entry(session_id="notice-empty")

        with (
            patch(
                "agent.agent_session_queue._resolve_callbacks",
            ) as mock_resolve,
            patch("agent.output_handler.logger") as mock_logger,
        ):
            result = asyncio.run(deliver_system_notice(entry, ""))

        assert result is False
        # Callback resolution must never be reached for an empty message.
        mock_resolve.assert_not_called()
        mock_logger.debug.assert_called()

    def test_telemetry_key_increments_only_on_success(self):
        """When telemetry_key is supplied and the send succeeds, the counter
        is INCR'd exactly once."""
        entry = self._notice_entry(session_id="notice-telemetry")

        async def _ok(*_a, **_kw):
            return None

        fake_redis = MagicMock()
        with (
            patch(
                "agent.agent_session_queue._resolve_callbacks",
                return_value=(_ok, None),
            ),
            patch("popoto.redis_db.POPOTO_REDIS_DB", fake_redis),
        ):
            result = asyncio.run(
                deliver_system_notice(entry, "notice", telemetry_key="proj:counter")
            )

        assert result is True
        fake_redis.incr.assert_called_once_with("proj:counter")

    def test_file_paths_forwarded_to_send_callback(self):
        """The optional attachment channel (issue #2303): a supplied file_paths
        list is forwarded verbatim to the resolved send callback."""
        entry = self._notice_entry(session_id="notice-attach")
        captured = {}

        async def _capture(chat_id, text, reply_to, session=None, file_paths=None):
            captured["file_paths"] = file_paths
            return None

        with patch(
            "agent.agent_session_queue._resolve_callbacks",
            return_value=(_capture, None),
        ):
            result = asyncio.run(
                deliver_system_notice(entry, "notice", file_paths=["/tmp/report.pdf"])  # noqa: S108
            )

        assert result is True
        assert captured["file_paths"] == ["/tmp/report.pdf"]  # noqa: S108

    def test_file_paths_defaults_to_none_when_omitted(self):
        """Back-compat: callers that pass no file_paths still forward None so the
        send callback omits the attachment key (unchanged behavior)."""
        entry = self._notice_entry(session_id="notice-noattach")
        captured = {}

        async def _capture(chat_id, text, reply_to, session=None, file_paths="sentinel"):
            captured["file_paths"] = file_paths
            return None

        with patch(
            "agent.agent_session_queue._resolve_callbacks",
            return_value=(_capture, None),
        ):
            result = asyncio.run(deliver_system_notice(entry, "notice"))

        assert result is True
        assert captured["file_paths"] is None
