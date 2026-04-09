"""Tests for the Sentry before_send hibernation filter.

The filter drops auth-related Sentry events when the bridge is hibernating
(is_hibernating() == True). Non-auth events pass through even when hibernating.
When not hibernating, all events pass through unchanged.
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
    "exception": {
        "values": [{"type": "SystemExit", "value": "Bridge hibernating: auth required"}]
    },
}
_GENERIC_ERROR_EVENT = {"level": "error", "message": "something unrelated"}
_MINIMAL_EVENT = {}
_NO_EXCEPTION_OR_LOGENTRY = {"level": "warning", "tags": {"module": "bridge"}}


class TestSentryBeforeSend:
    """Tests for _sentry_before_send callback."""

    def test_drops_logentry_auth_event_when_hibernating(self):
        """VALOR-1 logentry auth error is dropped when hibernating."""
        with patch("bridge.telegram_bridge.is_hibernating", return_value=True):
            assert _sentry_before_send(_LOGENTRY_AUTH_EVENT, {}) is None

    def test_drops_exception_auth_event_when_hibernating(self):
        """VALOR-Y exception auth error is dropped when hibernating."""
        with patch("bridge.telegram_bridge.is_hibernating", return_value=True):
            assert _sentry_before_send(_EXCEPTION_AUTH_EVENT, {}) is None

    def test_passes_non_auth_event_when_hibernating(self):
        """Non-auth errors pass through even when hibernating."""
        with patch("bridge.telegram_bridge.is_hibernating", return_value=True):
            assert _sentry_before_send(_GENERIC_ERROR_EVENT, {}) is _GENERIC_ERROR_EVENT

    def test_passes_empty_event_when_hibernating(self):
        """Empty/minimal events pass through when hibernating (no auth match)."""
        with patch("bridge.telegram_bridge.is_hibernating", return_value=True):
            assert _sentry_before_send(_MINIMAL_EVENT, {}) is _MINIMAL_EVENT

    def test_passes_event_without_exception_or_logentry_when_hibernating(self):
        """Events with no exception or logentry keys pass through when hibernating."""
        with patch("bridge.telegram_bridge.is_hibernating", return_value=True):
            result = _sentry_before_send(_NO_EXCEPTION_OR_LOGENTRY, {})
            assert result is _NO_EXCEPTION_OR_LOGENTRY

    @pytest.mark.parametrize(
        "event, hint",
        [
            (_LOGENTRY_AUTH_EVENT, {}),
            (_EXCEPTION_AUTH_EVENT, {}),
            (_GENERIC_ERROR_EVENT, None),
            (_MINIMAL_EVENT, {}),
        ],
        ids=["logentry_auth", "exception_auth", "none_hint", "empty_event"],
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

    def test_handles_empty_exception_values_list(self):
        """Event with empty exception.values list doesn't crash the filter."""
        event = {"level": "error", "exception": {"values": []}}
        with patch("bridge.telegram_bridge.is_hibernating", return_value=True):
            assert _sentry_before_send(event, {}) is event
