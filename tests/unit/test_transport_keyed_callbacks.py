"""Unit tests for transport-keyed callback registration and lookup.

Tests that register_callbacks() supports both plain project_key and
(project_key, transport) composite keys, and that the lookup helpers
correctly fall back from transport-keyed to plain-key to None.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.agent_session_queue import (
    _reaction_callbacks,
    _resolve_reaction_callback,
    _resolve_send_callback,
    _response_callbacks,
    _send_callbacks,
    register_callbacks,
)


def _make_handler():
    """Create a minimal mock OutputHandler."""
    handler = MagicMock()
    handler.send = AsyncMock()
    handler.react = AsyncMock()
    return handler


@pytest.fixture(autouse=True)
def clear_callbacks():
    """Clear global callback dicts before and after each test."""
    _send_callbacks.clear()
    _reaction_callbacks.clear()
    _response_callbacks.clear()
    yield
    _send_callbacks.clear()
    _reaction_callbacks.clear()
    _response_callbacks.clear()


class TestBackwardCompatibility:
    """Existing callers with no transport= arg must be unaffected."""

    def test_register_no_transport_uses_string_key(self):
        handler = _make_handler()
        register_callbacks("my-project", handler=handler)
        assert "my-project" in _send_callbacks
        assert "my-project" in _reaction_callbacks

    def test_resolve_no_transport_returns_plain_key_handler(self):
        handler = _make_handler()
        register_callbacks("my-project", handler=handler)
        cb = _resolve_send_callback("my-project", None)
        assert cb is handler.send

    def test_resolve_with_transport_falls_back_to_plain_key(self):
        """If no transport-keyed handler exists, fall back to plain key."""
        handler = _make_handler()
        register_callbacks("my-project", handler=handler)
        cb = _resolve_send_callback("my-project", "email")
        # No email-specific handler — should fall back to the plain-key Telegram handler
        assert cb is handler.send

    def test_resolve_unknown_project_returns_none(self):
        cb = _resolve_send_callback("nonexistent", None)
        assert cb is None

    def test_resolve_unknown_transport_returns_none(self):
        cb = _resolve_send_callback("nonexistent", "email")
        assert cb is None


class TestTransportKeyedRegistration:
    """Transport-specific handlers are stored and resolved correctly."""

    def test_register_with_transport_uses_tuple_key(self):
        handler = _make_handler()
        register_callbacks("my-project", handler=handler, transport="email")
        assert ("my-project", "email") in _send_callbacks
        assert ("my-project", "email") in _reaction_callbacks
        # Plain key must NOT be set
        assert "my-project" not in _send_callbacks

    def test_resolve_transport_keyed_handler(self):
        email_handler = _make_handler()
        register_callbacks("my-project", handler=email_handler, transport="email")
        cb = _resolve_send_callback("my-project", "email")
        assert cb is email_handler.send

    def test_transport_specific_overrides_plain_key(self):
        """When both plain and transport-keyed handlers exist, transport wins."""
        tg_handler = _make_handler()
        email_handler = _make_handler()
        register_callbacks("my-project", handler=tg_handler)
        register_callbacks("my-project", handler=email_handler, transport="email")

        email_cb = _resolve_send_callback("my-project", "email")
        tg_cb = _resolve_send_callback("my-project", "telegram")

        assert email_cb is email_handler.send
        # Telegram fallback: no (project, telegram) key → falls back to plain key
        assert tg_cb is tg_handler.send

    def test_multiple_transports_for_same_project(self):
        """A project can have both Telegram and email handlers simultaneously."""
        tg_handler = _make_handler()
        email_handler = _make_handler()
        register_callbacks("my-project", handler=tg_handler)
        register_callbacks("my-project", handler=email_handler, transport="email")

        assert _resolve_send_callback("my-project", None) is tg_handler.send
        assert _resolve_send_callback("my-project", "email") is email_handler.send

    def test_multiple_projects_independent(self):
        h1 = _make_handler()
        h2 = _make_handler()
        register_callbacks("project-a", handler=h1, transport="email")
        register_callbacks("project-b", handler=h2, transport="email")

        assert _resolve_send_callback("project-a", "email") is h1.send
        assert _resolve_send_callback("project-b", "email") is h2.send


class TestReactionCallbackResolution:
    """_resolve_reaction_callback mirrors send callback resolution."""

    def test_resolve_reaction_plain_key(self):
        handler = _make_handler()
        register_callbacks("my-project", handler=handler)
        cb = _resolve_reaction_callback("my-project", None)
        assert cb is handler.react

    def test_resolve_reaction_transport_keyed(self):
        email_handler = _make_handler()
        register_callbacks("my-project", handler=email_handler, transport="email")
        cb = _resolve_reaction_callback("my-project", "email")
        assert cb is email_handler.react

    def test_resolve_reaction_fallback_to_plain(self):
        tg_handler = _make_handler()
        register_callbacks("my-project", handler=tg_handler)
        cb = _resolve_reaction_callback("my-project", "email")
        assert cb is tg_handler.react


class TestResponseCallback:
    """response_callback follows the same keying pattern."""

    def test_response_callback_plain_key(self):
        handler = _make_handler()
        resp_cb = MagicMock()
        register_callbacks("my-project", handler=handler, response_callback=resp_cb)
        assert _response_callbacks.get("my-project") is resp_cb

    def test_response_callback_transport_key(self):
        handler = _make_handler()
        resp_cb = MagicMock()
        register_callbacks(
            "my-project", handler=handler, transport="email", response_callback=resp_cb
        )
        assert _response_callbacks.get(("my-project", "email")) is resp_cb


class TestRawCallbackRegistration:
    """register_callbacks with raw callable args (not handler=) still works."""

    def test_raw_callbacks_plain_key(self):
        send_cb = AsyncMock()
        react_cb = AsyncMock()
        register_callbacks("raw-project", send_callback=send_cb, reaction_callback=react_cb)
        assert _send_callbacks.get("raw-project") is send_cb
        assert _reaction_callbacks.get("raw-project") is react_cb

    def test_raw_callbacks_transport_key(self):
        send_cb = AsyncMock()
        react_cb = AsyncMock()
        register_callbacks(
            "raw-project",
            send_callback=send_cb,
            reaction_callback=react_cb,
            transport="email",
        )
        assert _send_callbacks.get(("raw-project", "email")) is send_cb
        assert _reaction_callbacks.get(("raw-project", "email")) is react_cb

    def test_missing_send_callback_raises(self):
        react_cb = AsyncMock()
        with pytest.raises(ValueError, match="send_callback or handler"):
            register_callbacks("p", reaction_callback=react_cb)

    def test_missing_reaction_callback_raises(self):
        send_cb = AsyncMock()
        with pytest.raises(ValueError, match="reaction_callback or handler"):
            register_callbacks("p", send_callback=send_cb)
