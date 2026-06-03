"""Unit tests for bridge/silent_stream.py (issue #1408).

Covers the false-positive suppression rules of the silent-stream watcher:
skip-when-no-prior-activity, warn-after-15-min, suppress-within-30-min-window,
only-respond_to_unaddressed-chats, cold-start suppression, and survive-Redis-failure.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.silent_stream import (
    SILENCE_THRESHOLD_SECONDS,
    WARN_SUPPRESSION_SECONDS,
    check_silent_streams,
)

NOW = 1_800_000_000.0  # fixed "now" for deterministic tests


def _make_dialog(chat_title, chat_id):
    dialog = MagicMock()
    dialog.entity = MagicMock()
    dialog.entity.title = chat_title
    dialog.id = chat_id
    return dialog


def _project(respond_to_unaddressed=True):
    return {"telegram": {"respond_to_unaddressed": respond_to_unaddressed}}


def _client(dialogs):
    client = AsyncMock()
    client.get_dialogs = AsyncMock(return_value=dialogs)
    return client


# Bridge "started" long ago so cold-start suppression never interferes
EARLY_START = NOW - 10 * 3600


class TestCheckSilentStreams:
    @pytest.mark.asyncio
    async def test_warns_after_silence_threshold(self):
        """A chat silent past the threshold emits exactly one warning."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        client = _client([dialog])
        warned: dict = {}

        last_event = NOW - (SILENCE_THRESHOLD_SECONDS + 60)
        with patch(
            "bridge.silent_stream.get_last_event_ts",
            new_callable=AsyncMock,
            return_value=last_event,
        ):
            emitted = await check_silent_streams(
                client=client,
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                bridge_start_ts=EARLY_START,
                warned_chats=warned,
                now=NOW,
            )

        assert emitted == 1
        assert -1001 in warned

    @pytest.mark.asyncio
    async def test_no_warn_when_recently_active(self):
        """A chat active within the threshold does not warn."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        client = _client([dialog])

        last_event = NOW - 60  # 1 minute ago
        with patch(
            "bridge.silent_stream.get_last_event_ts",
            new_callable=AsyncMock,
            return_value=last_event,
        ):
            emitted = await check_silent_streams(
                client=client,
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                bridge_start_ts=EARLY_START,
                warned_chats={},
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_skip_when_no_prior_activity(self):
        """A chat with no last_event record is skipped (no baseline → no signal)."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        client = _client([dialog])

        with patch(
            "bridge.silent_stream.get_last_event_ts",
            new_callable=AsyncMock,
            return_value=None,
        ):
            emitted = await check_silent_streams(
                client=client,
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                bridge_start_ts=EARLY_START,
                warned_chats={},
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_only_respond_to_unaddressed_chats(self):
        """Mention-gated chats (respond_to_unaddressed=False) are not watched."""
        dialog = _make_dialog("Mention Only", -1002)
        client = _client([dialog])

        last_event = NOW - (SILENCE_THRESHOLD_SECONDS + 60)
        with patch(
            "bridge.silent_stream.get_last_event_ts",
            new_callable=AsyncMock,
            return_value=last_event,
        ):
            emitted = await check_silent_streams(
                client=client,
                monitored_groups=["mention only"],
                find_project_fn=MagicMock(return_value=_project(respond_to_unaddressed=False)),
                bridge_start_ts=EARLY_START,
                warned_chats={},
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_suppress_within_window(self):
        """A second scan within the suppression window does not re-warn."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        client = _client([dialog])
        # Already warned 5 minutes ago, inside the 30-min suppression window.
        warned = {-1001: NOW - 5 * 60}

        last_event = NOW - (SILENCE_THRESHOLD_SECONDS + 60)
        with patch(
            "bridge.silent_stream.get_last_event_ts",
            new_callable=AsyncMock,
            return_value=last_event,
        ):
            emitted = await check_silent_streams(
                client=client,
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                bridge_start_ts=EARLY_START,
                warned_chats=warned,
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_rewarns_after_suppression_window(self):
        """After the suppression window elapses, a still-silent chat warns again."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        client = _client([dialog])
        warned = {-1001: NOW - (WARN_SUPPRESSION_SECONDS + 60)}

        last_event = NOW - (SILENCE_THRESHOLD_SECONDS + 60)
        with patch(
            "bridge.silent_stream.get_last_event_ts",
            new_callable=AsyncMock,
            return_value=last_event,
        ):
            emitted = await check_silent_streams(
                client=client,
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                bridge_start_ts=EARLY_START,
                warned_chats=warned,
                now=NOW,
            )

        assert emitted == 1

    @pytest.mark.asyncio
    async def test_cold_start_suppression(self):
        """No warning fires within the silence threshold after bridge startup."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        client = _client([dialog])

        # Bridge started 5 minutes ago — below the 15-min threshold.
        with patch(
            "bridge.silent_stream.get_last_event_ts",
            new_callable=AsyncMock,
            return_value=NOW - (SILENCE_THRESHOLD_SECONDS + 60),
        ):
            emitted = await check_silent_streams(
                client=client,
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                bridge_start_ts=NOW - 5 * 60,
                warned_chats={},
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_empty_monitored_groups(self):
        """No monitored groups → no scan, no warnings."""
        client = _client([])
        emitted = await check_silent_streams(
            client=client,
            monitored_groups=[],
            find_project_fn=MagicMock(),
            bridge_start_ts=EARLY_START,
            warned_chats={},
            now=NOW,
        )
        assert emitted == 0
        client.get_dialogs.assert_not_called()

    @pytest.mark.asyncio
    async def test_survives_redis_failure(self):
        """A get_last_event_ts that returns None on failure does not crash the scan."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        client = _client([dialog])

        # get_last_event_ts swallows failures and returns None — the scan must
        # treat it as "no baseline" and continue without raising.
        with patch(
            "bridge.silent_stream.get_last_event_ts",
            new_callable=AsyncMock,
            return_value=None,
        ):
            emitted = await check_silent_streams(
                client=client,
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                bridge_start_ts=EARLY_START,
                warned_chats={},
                now=NOW,
            )

        assert emitted == 0
