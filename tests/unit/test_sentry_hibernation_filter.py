"""Tests for the Sentry before_send hibernation filter.

The filter drops all Sentry events when the bridge is hibernating
(is_hibernating() == True). When not hibernating, all events pass through.
"""

from unittest.mock import patch

import pytest

from bridge.telegram_bridge import _sentry_before_send

# Representative Sentry event structures
_LOGENTRY_AUTH_EVENT = {
    "level": "error",
    "logentry": {"formatted": "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set"},
}
_EXCEPTION_AUTH_EVENT = {
    "level": "error",
    "exception": {"values": [{"type": "SystemExit", "value": "Bridge hibernating: auth required"}]},
}
_GENERIC_ERROR_EVENT = {"level": "error", "message": "something unrelated"}
_MINIMAL_EVENT = {}


class TestSentryBeforeSend:
    """Tests for _sentry_before_send callback."""

    @pytest.mark.parametrize(
        "event, desc",
        [
            (_LOGENTRY_AUTH_EVENT, "VALOR-1 logentry auth error"),
            (_EXCEPTION_AUTH_EVENT, "VALOR-Y exception auth error"),
            (_GENERIC_ERROR_EVENT, "non-auth error"),
            (_MINIMAL_EVENT, "empty event"),
        ],
        ids=["logentry_auth", "exception_auth", "generic", "empty"],
    )
    def test_drops_all_events_when_hibernating(self, event, desc):
        """All events are dropped when bridge is hibernating."""
        with patch("bridge.telegram_bridge.is_hibernating", return_value=True):
            assert _sentry_before_send(event, {}) is None

    @pytest.mark.parametrize(
        "event, hint",
        [
            (_LOGENTRY_AUTH_EVENT, {}),
            (_GENERIC_ERROR_EVENT, None),
            (_MINIMAL_EVENT, {}),
        ],
        ids=["auth_event", "none_hint", "empty_event"],
    )
    def test_passes_all_events_when_not_hibernating(self, event, hint):
        """All events pass through unchanged when not hibernating."""
        with patch("bridge.telegram_bridge.is_hibernating", return_value=False):
            assert _sentry_before_send(event, hint) is event

    def test_passes_event_when_is_hibernating_raises(self):
        """If is_hibernating() crashes, events pass through (safety net)."""
        event = {"level": "error", "message": "important error"}
        with patch(
            "bridge.telegram_bridge.is_hibernating",
            side_effect=OSError("disk error"),
        ):
            assert _sentry_before_send(event, {}) is event
