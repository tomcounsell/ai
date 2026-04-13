"""Unit tests for transport-keyed callback registration and resolution.

Tests the multi-transport callback system in agent.agent_session_queue where
callbacks can be stored under plain project_key strings (backward compat) or
under (project_key, transport) composite keys for per-transport routing.
"""

from unittest.mock import AsyncMock

import pytest

import agent.agent_session_queue as queue_module
from agent.agent_session_queue import _resolve_callbacks, register_callbacks


def _make_handler():
    """Create a minimal OutputHandler-like mock with async send and react."""
    handler = AsyncMock()
    handler.send = AsyncMock()
    handler.react = AsyncMock()
    return handler


@pytest.fixture(autouse=True)
def clear_callback_dicts():
    """Reset the module-level callback dicts before each test."""
    queue_module._send_callbacks.clear()
    queue_module._reaction_callbacks.clear()
    queue_module._response_callbacks.clear()
    yield
    queue_module._send_callbacks.clear()
    queue_module._reaction_callbacks.clear()
    queue_module._response_callbacks.clear()


class TestPlainKeyRegistration:
    """register_callbacks with no transport stores under plain string key."""

    def test_plain_key_stored_and_resolved_without_transport(self):
        """Plain key handler is resolved when no transport is given."""
        handler = _make_handler()
        register_callbacks("myproject", handler=handler)

        send_cb, react_cb = _resolve_callbacks("myproject", None)
        assert send_cb is handler.send
        assert react_cb is handler.react

    def test_plain_key_resolved_when_transport_given_but_no_composite_key(self):
        """Plain key falls back when transport is given but no composite key exists."""
        handler = _make_handler()
        register_callbacks("myproject", handler=handler)

        # Transport "telegram" requested but only plain key registered — should fall back
        send_cb, react_cb = _resolve_callbacks("myproject", "telegram")
        assert send_cb is handler.send
        assert react_cb is handler.react

    def test_plain_key_stored_under_string_not_tuple(self):
        """Verify the internal dict key is a plain string, not a tuple."""
        handler = _make_handler()
        register_callbacks("myproject", handler=handler)

        assert "myproject" in queue_module._send_callbacks
        assert ("myproject", None) not in queue_module._send_callbacks


class TestCompositeKeyRegistration:
    """register_callbacks with transport stores under (project_key, transport) key."""

    def test_email_transport_stored_under_composite_key(self):
        """Email handler stored under ('project', 'email') composite key."""
        handler = _make_handler()
        register_callbacks("myproject", transport="email", handler=handler)

        assert ("myproject", "email") in queue_module._send_callbacks
        assert "myproject" not in queue_module._send_callbacks

    def test_email_transport_resolved_correctly(self):
        """Email-keyed handler is returned when email transport is requested."""
        handler = _make_handler()
        register_callbacks("myproject", transport="email", handler=handler)

        send_cb, react_cb = _resolve_callbacks("myproject", "email")
        assert send_cb is handler.send
        assert react_cb is handler.react


class TestDualTransportResolution:
    """Both Telegram and email handlers registered for same project."""

    def test_telegram_transport_gets_telegram_handler(self):
        """_resolve_callbacks returns Telegram handler when transport='telegram'."""
        tg_handler = _make_handler()
        email_handler = _make_handler()

        register_callbacks("proj", transport="telegram", handler=tg_handler)
        register_callbacks("proj", transport="email", handler=email_handler)

        send_cb, react_cb = _resolve_callbacks("proj", "telegram")
        assert send_cb is tg_handler.send
        assert react_cb is tg_handler.react

    def test_email_transport_gets_email_handler(self):
        """_resolve_callbacks returns email handler when transport='email'."""
        tg_handler = _make_handler()
        email_handler = _make_handler()

        register_callbacks("proj", transport="telegram", handler=tg_handler)
        register_callbacks("proj", transport="email", handler=email_handler)

        send_cb, react_cb = _resolve_callbacks("proj", "email")
        assert send_cb is email_handler.send
        assert react_cb is email_handler.react

    def test_both_keys_stored_independently(self):
        """Registering two transports creates two independent dict entries."""
        tg_handler = _make_handler()
        email_handler = _make_handler()

        register_callbacks("proj", transport="telegram", handler=tg_handler)
        register_callbacks("proj", transport="email", handler=email_handler)

        assert ("proj", "telegram") in queue_module._send_callbacks
        assert ("proj", "email") in queue_module._send_callbacks
        assert "proj" not in queue_module._send_callbacks


class TestUnknownProjectFallback:
    """Unregistered projects return (None, None) from _resolve_callbacks."""

    def test_unknown_project_no_transport_returns_none(self):
        """Completely unknown project returns (None, None)."""
        send_cb, react_cb = _resolve_callbacks("nonexistent", None)
        assert send_cb is None
        assert react_cb is None

    def test_unknown_project_with_transport_returns_none(self):
        """Unknown project with transport also returns (None, None)."""
        send_cb, react_cb = _resolve_callbacks("nonexistent", "email")
        assert send_cb is None
        assert react_cb is None


class TestCompositeKeyFallbackToPlain:
    """Composite key missing → falls back to plain key handler."""

    def test_plain_key_fallback_when_composite_key_not_registered(self):
        """Project has plain key only; requesting 'email' transport falls back to plain."""
        handler = _make_handler()
        register_callbacks("proj", handler=handler)

        # No email composite key — should fall back to plain "proj" handler
        send_cb, react_cb = _resolve_callbacks("proj", "email")
        assert send_cb is handler.send
        assert react_cb is handler.react

    def test_plain_key_fallback_does_not_affect_registered_composite_keys(self):
        """Email composite key overrides plain when both registered."""
        plain_handler = _make_handler()
        email_handler = _make_handler()

        register_callbacks("proj", handler=plain_handler)
        register_callbacks("proj", transport="email", handler=email_handler)

        # email transport → composite key wins
        send_cb, react_cb = _resolve_callbacks("proj", "email")
        assert send_cb is email_handler.send

        # no transport → plain key
        send_cb_plain, react_cb_plain = _resolve_callbacks("proj", None)
        assert send_cb_plain is plain_handler.send


class TestRawCallbackRegistration:
    """register_callbacks also accepts raw callables without a handler."""

    def test_raw_send_and_react_callbacks_stored(self):
        """Passing send_callback and reaction_callback directly works."""
        send_fn = AsyncMock()
        react_fn = AsyncMock()

        register_callbacks("proj", send_callback=send_fn, reaction_callback=react_fn)

        send_cb, react_cb = _resolve_callbacks("proj", None)
        assert send_cb is send_fn
        assert react_cb is react_fn

    def test_raw_callbacks_with_transport(self):
        """Raw callbacks with transport stored under composite key."""
        send_fn = AsyncMock()
        react_fn = AsyncMock()

        register_callbacks(
            "proj",
            send_callback=send_fn,
            reaction_callback=react_fn,
            transport="telegram",
        )

        send_cb, react_cb = _resolve_callbacks("proj", "telegram")
        assert send_cb is send_fn
        assert react_cb is react_fn

    def test_missing_send_callback_without_handler_raises(self):
        """register_callbacks without handler and without send_callback raises ValueError."""
        with pytest.raises(ValueError, match="send_callback"):
            register_callbacks(
                "proj",
                reaction_callback=AsyncMock(),
            )
