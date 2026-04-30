"""Tests for ``bridge.telegram_bridge._ack_steering_routed`` helper.

This helper bundles the terminal sequence shared by all six steering
routing branches in the bridge. It replaces six ad-hoc duplicated
sequences (inline imports + is_abort detection + push + ack +
log + record) with a single helper call.

Tests cover both the steer and abort branches, the dual-push path
(when ``agent_session`` is provided), the defensive try/except
around ``set_reaction``, and the media-enrichment + auto-ingest
branch added for issue #1215.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.telegram_bridge import _ack_steering_routed


def _make_event_message(
    chat_id: int = 12345,
    msg_id: int = 67890,
    media: object | None = None,
):
    event = MagicMock()
    event.chat_id = chat_id
    message = MagicMock()
    message.id = msg_id
    # `media` defaults to None — text-only path. Tests that exercise the
    # media branch pass a truthy sentinel object.
    message.media = media
    return event, message


class TestSteerBranch:
    """Default steer path: non-abort text, no agent_session."""

    @pytest.mark.asyncio
    async def test_pushes_with_is_abort_false(self):
        client = MagicMock()
        event, message = _make_event_message()
        with (
            patch("bridge.telegram_bridge.push_steering_message") as push,
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock) as react,
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ) as rec,
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="please update the readme",
                log_context="[test] steer log",
            )

        push.assert_called_once_with("sess-1", "please update the readme", "Alice", is_abort=False)
        react.assert_awaited_once()
        # Steer reaction is the standard "received" eyes
        args, _ = react.await_args
        assert args[3] == "\U0001f440"  # 👀
        rec.assert_awaited_once_with(12345, 67890)

    @pytest.mark.asyncio
    async def test_does_not_call_agent_session_push(self):
        client = MagicMock()
        event, message = _make_event_message()
        with (
            patch("bridge.telegram_bridge.push_steering_message"),
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="hello",
                log_context="[test]",
                # agent_session omitted
            )
        # No assertion needed — the call simply must not raise.


class TestAbortBranch:
    """Abort path: text matches ABORT_KEYWORDS."""

    @pytest.mark.asyncio
    async def test_abort_detected_and_salute_reaction(self):
        client = MagicMock()
        event, message = _make_event_message()
        with (
            patch("bridge.telegram_bridge.push_steering_message") as push,
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock) as react,
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ) as rec,
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="stop",
                log_context="[test] abort log",
            )

        push.assert_called_once_with("sess-1", "stop", "Alice", is_abort=True)
        react.assert_awaited_once()
        args, _ = react.await_args
        assert args[3] == "\U0001fae1"  # 🫡
        rec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_abort_keyword_case_insensitive(self):
        client = MagicMock()
        event, message = _make_event_message()
        with (
            patch("bridge.telegram_bridge.push_steering_message") as push,
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="  STOP  ",
                log_context="[test]",
            )
        # is_abort should still be detected after strip + lower
        push.assert_called_once_with("sess-1", "  STOP  ", "Alice", is_abort=True)


class TestDualPush:
    """When agent_session is provided, the durable PM-visible push runs first."""

    @pytest.mark.asyncio
    async def test_agent_session_push_called_before_redis(self):
        client = MagicMock()
        event, message = _make_event_message()
        agent_session = MagicMock()
        agent_session.push_steering_message = MagicMock()
        call_order = []

        agent_session.push_steering_message.side_effect = lambda *a, **kw: call_order.append(
            "popoto"
        )

        with (
            patch(
                "bridge.telegram_bridge.push_steering_message",
                side_effect=lambda *a, **kw: call_order.append("redis"),
            ),
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="please update X",
                log_context="[test]",
                agent_session=agent_session,
            )

        # Durable Popoto write happens before the Redis push
        assert call_order == ["popoto", "redis"]
        agent_session.push_steering_message.assert_called_once_with("please update X")


class TestDefensiveReaction:
    """The try/except around set_reaction must absorb failures silently."""

    @pytest.mark.asyncio
    async def test_set_reaction_failure_does_not_raise(self):
        client = MagicMock()
        event, message = _make_event_message()
        with (
            patch("bridge.telegram_bridge.push_steering_message"),
            patch(
                "bridge.telegram_bridge.set_reaction",
                new_callable=AsyncMock,
                side_effect=RuntimeError("non-Premium account"),
            ),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ) as rec,
        ):
            # Must not raise
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="hello",
                log_context="[test]",
            )
        # record_telegram_message_handled must still be awaited even if
        # set_reaction blew up — the steering write succeeded and we still
        # need to mark the message handled.
        rec.assert_awaited_once()


class TestMediaEnrichment:
    """Issue #1215: media-bearing steering messages get enriched in-place.

    When ``message.media`` is truthy, ``_ack_steering_routed`` must:
    - Fire ``set_reaction`` BEFORE ``process_incoming_media`` (immediate ack).
    - Replace the sentinel with the description (or compose description +
      caption when a real caption is present).
    - Schedule an ``_ingest_attachments`` task and append it to
      ``_background_tasks`` so the GC cannot collect it mid-flight.
    - Survive ``process_incoming_media`` failures by leaving the original
      ``text`` intact and still pushing.
    - Survive ingest task scheduling failures and still push the steering.

    Text-only path (``message.media is None``) keeps the existing
    reaction-after-push order byte-identical.
    """

    @pytest.mark.asyncio
    async def test_media_only_replaces_sentinel_with_description(self):
        """Media + sentinel caption → push uses description outright."""
        client = MagicMock()
        event, message = _make_event_message(media=object())
        with (
            patch(
                "bridge.telegram_bridge.process_incoming_media",
                new_callable=AsyncMock,
                return_value=("[Document content: hello world]", []),
            ),
            patch("bridge.telegram_bridge.push_steering_message") as push,
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="--file attachment only--",
                log_context="[test]",
            )

        push.assert_called_once_with(
            "sess-1", "[Document content: hello world]", "Alice", is_abort=False
        )

    @pytest.mark.asyncio
    async def test_media_with_caption_composes_description_and_caption(self):
        """Media + non-sentinel caption → push uses description + caption."""
        client = MagicMock()
        event, message = _make_event_message(media=object())
        with (
            patch(
                "bridge.telegram_bridge.process_incoming_media",
                new_callable=AsyncMock,
                return_value=("[User sent an image]\nImage description: cat", []),
            ),
            patch("bridge.telegram_bridge.push_steering_message") as push,
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="check this out",
                log_context="[test]",
            )

        expected = "[User sent an image]\nImage description: cat\n\ncheck this out"
        push.assert_called_once_with("sess-1", expected, "Alice", is_abort=False)

    @pytest.mark.asyncio
    async def test_text_only_path_skips_process_incoming_media(self):
        """Text-only path must not call process_incoming_media."""
        client = MagicMock()
        event, message = _make_event_message()  # media=None default
        with (
            patch(
                "bridge.telegram_bridge.process_incoming_media",
                new_callable=AsyncMock,
            ) as proc,
            patch("bridge.telegram_bridge.push_steering_message") as push,
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="hello",
                log_context="[test]",
            )

        proc.assert_not_awaited()
        push.assert_called_once_with("sess-1", "hello", "Alice", is_abort=False)

    @pytest.mark.asyncio
    async def test_process_incoming_media_failure_leaves_sentinel_intact(self):
        """When process_incoming_media raises, original text still pushes."""
        client = MagicMock()
        event, message = _make_event_message(media=object())
        with (
            patch(
                "bridge.telegram_bridge.process_incoming_media",
                new_callable=AsyncMock,
                side_effect=RuntimeError("download exploded"),
            ),
            patch("bridge.telegram_bridge.push_steering_message") as push,
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ) as rec,
        ):
            # Must not raise
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="--file attachment only--",
                log_context="[test]",
            )

        # Push still happens with the sentinel (defensive fallback).
        push.assert_called_once_with("sess-1", "--file attachment only--", "Alice", is_abort=False)
        rec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ingest_task_exception_does_not_block_push(self):
        """A crash inside the ingest task must not block the steering push.

        The push happens BEFORE the ingest task runs; a synchronous
        failure during task scheduling (e.g. asyncio.create_task raising)
        is also caught so the steering write still completes.
        """
        client = MagicMock()
        event, message = _make_event_message(media=object())
        downloaded = Path("/tmp/some-attachment.txt")
        with (
            patch(
                "bridge.telegram_bridge.process_incoming_media",
                new_callable=AsyncMock,
                return_value=("[Document content: hi]", [downloaded]),
            ),
            patch(
                "bridge.telegram_bridge.asyncio.create_task",
                side_effect=RuntimeError("loop closed"),
            ),
            patch("bridge.telegram_bridge.push_steering_message") as push,
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ) as rec,
        ):
            # Must not raise even though create_task blew up.
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="--file attachment only--",
                log_context="[test]",
            )

        push.assert_called_once_with("sess-1", "[Document content: hi]", "Alice", is_abort=False)
        rec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ingest_task_registered_in_background_tasks(self):
        """Confirms the new ingest task is appended to _background_tasks.

        Without this, ``asyncio.create_task`` returns a task whose only
        live reference is local — the GC may collect it before it
        completes (the well-known "fire-and-vanish" footgun).
        """
        from bridge import telegram_bridge

        client = MagicMock()
        event, message = _make_event_message(media=object())
        downloaded = Path("/tmp/some-attachment.txt")
        baseline_len = len(telegram_bridge._background_tasks)

        # Use a real coroutine factory so the task object is genuine and
        # the append actually holds a reference to a Task instance.
        with (
            patch(
                "bridge.telegram_bridge.process_incoming_media",
                new_callable=AsyncMock,
                return_value=("[doc]", [downloaded]),
            ),
            patch("bridge.telegram_bridge.push_steering_message"),
            patch("bridge.telegram_bridge.set_reaction", new_callable=AsyncMock),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
            patch(
                "bridge.telegram_bridge._ingest_attachments",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="--file attachment only--",
                log_context="[test]",
            )

        try:
            assert len(telegram_bridge._background_tasks) == baseline_len + 1
            new_task = telegram_bridge._background_tasks[-1]
            # Wait for the task to complete so we don't leak warnings.
            await new_task
        finally:
            # Clean up the test's contribution to the module-level list.
            telegram_bridge._background_tasks[:] = telegram_bridge._background_tasks[:baseline_len]

    @pytest.mark.asyncio
    async def test_reaction_ordering_media_path(self):
        """Media path: set_reaction → process_incoming_media → push_steering."""
        client = MagicMock()
        event, message = _make_event_message(media=object())
        call_order: list[str] = []

        async def _spy_react(*a, **kw):
            call_order.append("react")

        async def _spy_process(*a, **kw):
            call_order.append("process")
            return ("[doc]", [])

        def _spy_push(*a, **kw):
            call_order.append("push")

        with (
            patch("bridge.telegram_bridge.set_reaction", new=_spy_react),
            patch("bridge.telegram_bridge.process_incoming_media", new=_spy_process),
            patch("bridge.telegram_bridge.push_steering_message", new=_spy_push),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="--file attachment only--",
                log_context="[test]",
            )

        assert call_order == ["react", "process", "push"]

    @pytest.mark.asyncio
    async def test_reaction_ordering_text_only_preserved(self):
        """Text-only path: push_steering → set_reaction (existing order)."""
        client = MagicMock()
        event, message = _make_event_message()  # media=None
        call_order: list[str] = []

        async def _spy_react(*a, **kw):
            call_order.append("react")

        def _spy_push(*a, **kw):
            call_order.append("push")

        with (
            patch("bridge.telegram_bridge.set_reaction", new=_spy_react),
            patch("bridge.telegram_bridge.push_steering_message", new=_spy_push),
            patch(
                "bridge.telegram_bridge.record_telegram_message_handled",
                new_callable=AsyncMock,
            ),
        ):
            await _ack_steering_routed(
                client,
                event,
                message,
                session_id="sess-1",
                sender_name="Alice",
                text="hello",
                log_context="[test]",
            )

        assert call_order == ["push", "react"]
