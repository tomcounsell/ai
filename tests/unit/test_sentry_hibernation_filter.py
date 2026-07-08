"""Tests for the Sentry before_send hibernation + orphan-noise filters.

The hibernation filter drops all Sentry events when the bridge is hibernating
(is_hibernating() == True). When not hibernating, all events pass through.

The orphan-noise filter drops events whose message contains the Popoto
orphan-keys noise substring. It is applied after the hibernation check so
orphan noise is suppressed even when the bridge is awake.
"""

from unittest.mock import patch

import pytest

from bridge.telegram_bridge import _sentry_before_send
from monitoring.sentry_config import drop_orphan_noise

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

# The Popoto orphan-keys noise message (from popoto/models/query.py).
_ORPHAN_NOISE_MSG = "one or more redis keys points to missing objects"


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
        """Non-orphan events pass through unchanged when not hibernating."""
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

    @pytest.mark.parametrize(
        "event",
        [
            {"level": "error", "logentry": {"formatted": _ORPHAN_NOISE_MSG}},
            {"level": "error", "logentry": {"message": _ORPHAN_NOISE_MSG}},
            {"level": "error", "message": _ORPHAN_NOISE_MSG},
        ],
        ids=["orphan_in_logentry_formatted", "orphan_in_logentry_message", "orphan_in_message"],
    )
    def test_drops_orphan_noise_when_not_hibernating(self, event):
        """Orphan-noise events are dropped even when the bridge is awake."""
        with patch("bridge.telegram_bridge.is_hibernating", return_value=False):
            assert _sentry_before_send(event, {}) is None

    def test_non_orphan_event_passes_through_when_not_hibernating(self):
        """A real (non-orphan) error passes through unchanged."""
        event = {
            "level": "error",
            "logentry": {"formatted": "KeyError: 'session_id'"},
        }
        with patch("bridge.telegram_bridge.is_hibernating", return_value=False):
            assert _sentry_before_send(event, {}) is event


class TestDropOrphanNoise:
    """Tests for the standalone drop_orphan_noise filter."""

    @pytest.mark.parametrize(
        "event",
        [
            {"level": "error", "logentry": {"formatted": _ORPHAN_NOISE_MSG}},
            {"level": "error", "logentry": {"message": _ORPHAN_NOISE_MSG}},
            {"level": "error", "message": _ORPHAN_NOISE_MSG},
            {
                "level": "error",
                "logentry": {"formatted": f"context: {_ORPHAN_NOISE_MSG} (extra detail)"},
            },
        ],
        ids=["logentry_formatted", "logentry_message", "message", "substring_in_context"],
    )
    def test_drops_orphan_events(self, event):
        """Events containing the orphan-noise substring are dropped (return None)."""
        assert drop_orphan_noise(event, {}) is None

    @pytest.mark.parametrize(
        "event",
        [
            {"level": "error", "logentry": {"formatted": "KeyError: 'session_id'"}},
            {"level": "error", "message": "something unrelated"},
            {"level": "error", "logentry": {"message": "RuntimeError: boom"}},
            {},
            {"level": "error"},
        ],
        ids=[
            "real_logentry",
            "real_message",
            "real_logentry_message",
            "empty",
            "no_logentry_or_message",
        ],
    )
    def test_passes_non_orphan_events(self, event):
        """Events without the orphan-noise substring pass through unchanged."""
        assert drop_orphan_noise(event, {}) is event

    def test_handles_hint_none(self):
        """hint=None does not crash the filter."""
        event = {"level": "error", "message": _ORPHAN_NOISE_MSG}
        assert drop_orphan_noise(event, None) is None

    def test_handles_no_logentry_and_no_message(self):
        """An event with neither logentry nor message falls through safely."""
        event = {"level": "error", "exception": {"values": [{"type": "ValueError"}]}}
        assert drop_orphan_noise(event, {}) is event

    def test_handles_empty_strings(self):
        """Empty-string message fields do not match and do not crash."""
        event = {"level": "error", "logentry": {"formatted": "", "message": ""}, "message": ""}
        assert drop_orphan_noise(event, {}) is event

    def test_never_raises_on_filter_crash(self):
        """If the matching logic raises, the event passes through (safety net)."""

        # An object that raises on .get to simulate a pathological event shape.
        class ExplodingDict(dict):
            def get(self, key, default=None):
                if key == "logentry":
                    raise RuntimeError("boom")
                return super().get(key, default)

        event = ExplodingDict({"level": "error"})
        assert drop_orphan_noise(event, {}) is event
