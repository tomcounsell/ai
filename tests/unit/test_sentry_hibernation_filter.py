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
from monitoring.sentry_config import (
    drop_asyncio_gc_noise,
    drop_orphan_noise,
    drop_transient_infra_noise,
    filter_sentry_noise,
    normalize_noisy_fingerprints,
)

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


# The exact Redis persistence-failure message (interpolated per model/loop).
_MISCONF_MSG = (
    "MISCONF Redis is configured to save RDB snapshots, but it's currently unable "
    "to persist to disk. Commands that may modify the data set are disabled."
)
_POPOTO_CLEANUP_MSG = f"[popoto-cleanup] Error processing TelegramMessage: {_MISCONF_MSG}"
_WATCHDOG_MSG = f"[watchdog] Failed to query active sessions for stall check: {_MISCONF_MSG}"


class TestDropTransientInfraNoise:
    """Tests for the drop_transient_infra_noise filter (Redis MISCONF fanout)."""

    @pytest.mark.parametrize(
        "event",
        [
            {"level": "error", "logentry": {"formatted": _POPOTO_CLEANUP_MSG}},
            {"level": "error", "logentry": {"message": _WATCHDOG_MSG}},
            {"level": "error", "message": _MISCONF_MSG},
        ],
        ids=["popoto_cleanup", "watchdog", "bare_misconf"],
    )
    def test_drops_misconf_events(self, event):
        """Events containing the MISCONF marker are dropped (return None)."""
        assert drop_transient_infra_noise(event, {}) is None

    @pytest.mark.parametrize(
        "event",
        [
            {"level": "error", "logentry": {"formatted": "KeyError: 'session_id'"}},
            {"level": "error", "message": "something unrelated"},
            {},
        ],
        ids=["real_logentry", "real_message", "empty"],
    )
    def test_passes_non_misconf_events(self, event):
        """Events without the MISCONF marker pass through unchanged."""
        assert drop_transient_infra_noise(event, {}) is event

    @pytest.mark.parametrize(
        "message",
        [
            "REGISTERED BOT MISCONFIGURATION (#1574): bot_id 12345 is not a bot",
            "Detected MISCONFIGURED routing table entry",
        ],
        ids=["registered_bot_misconfiguration", "misconfigured"],
    )
    def test_does_not_drop_misconfiguration_errors(self, message):
        """Real errors whose text merely *contains* ``MISCONF`` must survive.

        Regression guard for 7ed56b8bd, which matched the bare ``MISCONF`` token
        as a substring. ``MISCONFIGURATION`` / ``MISCONFIGURED`` contain that
        token, so the live ``logger.error("REGISTERED BOT MISCONFIGURATION
        (#1574): %s", ...)`` in ``bridge/telegram_bridge.py`` was silently
        dropped from Sentry. The filter must anchor on the full Redis phrase.
        """
        event = {"level": "error", "logentry": {"formatted": message}}
        assert drop_transient_infra_noise(event, {}) is event

    def test_never_raises_on_filter_crash(self):
        """If the matching logic raises, the event passes through (safety net)."""

        class ExplodingDict(dict):
            def get(self, key, default=None):
                if key == "logentry":
                    raise RuntimeError("boom")
                return super().get(key, default)

        event = ExplodingDict({"level": "error"})
        assert drop_transient_infra_noise(event, {}) is event


class TestNormalizeNoisyFingerprints:
    """Tests for normalize_noisy_fingerprints (per-model/per-loop fanout guard)."""

    def test_popoto_cleanup_gets_stable_fingerprint_across_models(self):
        """Every model collapses into one Sentry issue via a shared fingerprint."""
        ev_a = {"level": "error", "logentry": {"formatted": _POPOTO_CLEANUP_MSG}}
        ev_b = {
            "level": "error",
            "logentry": {"formatted": "[popoto-cleanup] Error processing AgentSession: boom"},
        }
        normalize_noisy_fingerprints(ev_a, {})
        normalize_noisy_fingerprints(ev_b, {})
        assert ev_a["fingerprint"] == ev_b["fingerprint"]
        assert ev_a["fingerprint"] == ["popoto-cleanup", "error-processing-model"]

    def test_watchdog_gets_stable_fingerprint(self):
        event = {"level": "error", "message": _WATCHDOG_MSG}
        normalize_noisy_fingerprints(event, {})
        assert event["fingerprint"] == ["watchdog", "failed-to-query-sessions-for-stall-check"]

    def test_watchdog_all_status_variants_collapse(self):
        """pending/running/active stall-query failures share one fingerprint (#2371).

        The watchdog interpolates the session status into the message; before the
        #2371 fix only the ``active`` variant matched, so ``pending``/``running``
        fanned out into their own Sentry issues.
        """
        events = [
            {
                "level": "error",
                "message": (
                    f"[watchdog] Failed to query {status} sessions for stall check: "
                    "Timeout reading from socket"
                ),
            }
            for status in ("pending", "running", "active")
        ]
        for ev in events:
            normalize_noisy_fingerprints(ev, {})
        fingerprints = [ev["fingerprint"] for ev in events]
        assert all(
            fp == ["watchdog", "failed-to-query-sessions-for-stall-check"] for fp in fingerprints
        )

    def test_unknown_cluster_left_unfingerprinted(self):
        event = {"level": "error", "message": "KeyError: 'session_id'"}
        normalize_noisy_fingerprints(event, {})
        assert "fingerprint" not in event

    def test_never_raises_on_filter_crash(self):
        class ExplodingDict(dict):
            def get(self, key, default=None):
                if key == "logentry":
                    raise RuntimeError("boom")
                return super().get(key, default)

        event = ExplodingDict({"level": "error"})
        assert normalize_noisy_fingerprints(event, {}) is event


_ASYNCIO_GC_MSG = (
    "Task was destroyed but it is pending!\n"
    "task: <Task pending name='Task-465' coro=<MTProtoSender._recv_loop() "
    "done, defined at telethon/network/mtprotosender.py:503> wait_for=<Future cancelled>>"
)


class TestDropAsyncioGcNoise:
    """Tests for the drop_asyncio_gc_noise filter (Telethon shutdown GC, #2368)."""

    @pytest.mark.parametrize(
        "event",
        [
            {"level": "error", "logentry": {"formatted": _ASYNCIO_GC_MSG}},
            {"level": "error", "logentry": {"message": _ASYNCIO_GC_MSG}},
            {"level": "error", "message": _ASYNCIO_GC_MSG},
        ],
        ids=["formatted", "message_template", "top_level_message"],
    )
    def test_drops_asyncio_gc_events(self, event):
        """Events containing the asyncio pending-task marker are dropped."""
        assert drop_asyncio_gc_noise(event, {}) is None

    @pytest.mark.parametrize(
        "event",
        [
            {"level": "error", "logentry": {"formatted": "KeyError: 'session_id'"}},
            {"level": "error", "message": "Task completed successfully"},
            {},
        ],
        ids=["real_logentry", "real_message", "empty"],
    )
    def test_passes_non_asyncio_events(self, event):
        """Events without the asyncio marker pass through unchanged."""
        assert drop_asyncio_gc_noise(event, {}) is event

    def test_never_raises_on_filter_crash(self):
        class ExplodingDict(dict):
            def get(self, key, default=None):
                if key == "logentry":
                    raise RuntimeError("boom")
                return super().get(key, default)

        event = ExplodingDict({"level": "error"})
        assert drop_asyncio_gc_noise(event, {}) is event


class TestFilterSentryNoise:
    """Tests for the composite filter_sentry_noise chain."""

    def test_drops_orphan_noise(self):
        assert filter_sentry_noise({"message": _ORPHAN_NOISE_MSG}, {}) is None

    def test_drops_misconf_noise(self):
        assert filter_sentry_noise({"logentry": {"formatted": _POPOTO_CLEANUP_MSG}}, {}) is None
        assert filter_sentry_noise({"message": _WATCHDOG_MSG}, {}) is None

    def test_drops_asyncio_gc_noise(self):
        assert filter_sentry_noise({"message": _ASYNCIO_GC_MSG}, {}) is None

    def test_passes_real_error_unchanged(self):
        event = {"level": "error", "logentry": {"formatted": "KeyError: 'session_id'"}}
        assert filter_sentry_noise(event, {}) is event
        assert "fingerprint" not in event
