"""Tests for ``bridge.telegram_bridge._ack_steering_routed`` helper.

This helper bundles the terminal sequence shared by all six steering
routing branches in the bridge. It replaces six ad-hoc duplicated
sequences (inline imports + is_abort detection + push + ack +
log + record) with a single helper call.

Tests cover both the steer and abort branches, the dual-push path
(when ``agent_session`` is provided), and the defensive try/except
around ``set_reaction``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.telegram_bridge import _ack_steering_routed


def _make_event_message(chat_id: int = 12345, msg_id: int = 67890):
    event = MagicMock()
    event.chat_id = chat_id
    message = MagicMock()
    message.id = msg_id
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
