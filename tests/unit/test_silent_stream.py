"""Unit tests for bridge/silent_stream.py (issue #1408).

Covers the false-positive suppression rules of the silent-gap check:
skip-when-no-prior-activity, warn-after-15-min, suppress-within-30-min-window,
only-respond_to_unaddressed-chats, cold-start suppression, and survive-Redis-failure.

The check now rides the reconciler's existing dialog pass (no separate loop and
no independent ``get_dialogs()`` call). ``check_silent_streams`` operates on a
caller-supplied dialog list; ``check_silent_chat`` is the per-dialog primitive
the reconciler invokes inside its own loop.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.silent_stream import (
    SILENCE_THRESHOLD_SECONDS,
    WARN_SUPPRESSION_SECONDS,
    SilentStreamState,
    check_silent_chat,
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


# Bridge "started" long ago so cold-start suppression never interferes
EARLY_START = NOW - 10 * 3600


def _state(bridge_start_ts=EARLY_START, warned_chats=None):
    state = SilentStreamState(bridge_start_ts=bridge_start_ts)
    if warned_chats is not None:
        state.warned_chats = warned_chats
    return state


def _patch_last_event(value):
    return patch(
        "bridge.silent_stream.get_last_event_ts",
        new_callable=AsyncMock,
        return_value=value,
    )


class TestCheckSilentStreams:
    """The dialog-list orchestration that the reconciler drives."""

    @pytest.mark.asyncio
    async def test_warns_after_silence_threshold(self):
        """A chat silent past the threshold emits exactly one warning."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        state = _state()

        with _patch_last_event(NOW - (SILENCE_THRESHOLD_SECONDS + 60)):
            emitted = await check_silent_streams(
                dialogs=[dialog],
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                state=state,
                now=NOW,
            )

        assert emitted == 1
        assert -1001 in state.warned_chats

    @pytest.mark.asyncio
    async def test_no_warn_when_recently_active(self):
        """A chat active within the threshold does not warn."""
        dialog = _make_dialog("Cyndra Dev", -1001)

        with _patch_last_event(NOW - 60):  # 1 minute ago
            emitted = await check_silent_streams(
                dialogs=[dialog],
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                state=_state(),
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_skip_when_no_prior_activity(self):
        """A chat with no last_event record is skipped (no baseline → no signal)."""
        dialog = _make_dialog("Cyndra Dev", -1001)

        with _patch_last_event(None):
            emitted = await check_silent_streams(
                dialogs=[dialog],
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                state=_state(),
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_only_respond_to_unaddressed_chats(self):
        """Mention-gated chats (respond_to_unaddressed=False) are not watched."""
        dialog = _make_dialog("Mention Only", -1002)

        with _patch_last_event(NOW - (SILENCE_THRESHOLD_SECONDS + 60)):
            emitted = await check_silent_streams(
                dialogs=[dialog],
                monitored_groups=["mention only"],
                find_project_fn=MagicMock(return_value=_project(respond_to_unaddressed=False)),
                state=_state(),
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_suppress_within_window(self):
        """A second scan within the suppression window does not re-warn."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        # Already warned 5 minutes ago, inside the 30-min suppression window.
        state = _state(warned_chats={-1001: NOW - 5 * 60})

        with _patch_last_event(NOW - (SILENCE_THRESHOLD_SECONDS + 60)):
            emitted = await check_silent_streams(
                dialogs=[dialog],
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                state=state,
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_rewarns_after_suppression_window(self):
        """After the suppression window elapses, a still-silent chat warns again."""
        dialog = _make_dialog("Cyndra Dev", -1001)
        state = _state(warned_chats={-1001: NOW - (WARN_SUPPRESSION_SECONDS + 60)})

        with _patch_last_event(NOW - (SILENCE_THRESHOLD_SECONDS + 60)):
            emitted = await check_silent_streams(
                dialogs=[dialog],
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                state=state,
                now=NOW,
            )

        assert emitted == 1

    @pytest.mark.asyncio
    async def test_cold_start_suppression(self):
        """No warning fires within the silence threshold after bridge startup."""
        dialog = _make_dialog("Cyndra Dev", -1001)

        # Bridge started 5 minutes ago — below the 15-min threshold.
        with _patch_last_event(NOW - (SILENCE_THRESHOLD_SECONDS + 60)):
            emitted = await check_silent_streams(
                dialogs=[dialog],
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                state=_state(bridge_start_ts=NOW - 5 * 60),
                now=NOW,
            )

        assert emitted == 0

    @pytest.mark.asyncio
    async def test_empty_monitored_groups(self):
        """No monitored groups → no scan, no warnings."""
        emitted = await check_silent_streams(
            dialogs=[_make_dialog("Cyndra Dev", -1001)],
            monitored_groups=[],
            find_project_fn=MagicMock(),
            state=_state(),
            now=NOW,
        )
        assert emitted == 0

    @pytest.mark.asyncio
    async def test_does_not_fetch_its_own_dialogs(self):
        """The check operates on the supplied dialog list — it never calls get_dialogs.

        This is the no-go guard for issue #1408: the silent-gap check must ride
        the reconciler's existing dialog pass, adding no recurring get_dialogs()
        call of its own.
        """
        assert not hasattr(check_silent_streams, "client")
        # check_silent_streams has no client parameter at all — supplying one fails.
        with pytest.raises(TypeError):
            await check_silent_streams(
                client=AsyncMock(),  # noqa
                dialogs=[],
                monitored_groups=["x"],
                find_project_fn=MagicMock(),
                state=_state(),
            )

    @pytest.mark.asyncio
    async def test_survives_redis_failure(self):
        """get_last_event_ts returning None on failure does not crash the scan."""
        dialog = _make_dialog("Cyndra Dev", -1001)

        # get_last_event_ts swallows failures and returns None — the scan must
        # treat it as "no baseline" and continue without raising.
        with _patch_last_event(None):
            emitted = await check_silent_streams(
                dialogs=[dialog],
                monitored_groups=["cyndra dev"],
                find_project_fn=MagicMock(return_value=_project()),
                state=_state(),
                now=NOW,
            )

        assert emitted == 0


class TestCheckSilentChat:
    """The per-dialog primitive the reconciler calls inside its own loop."""

    @pytest.mark.asyncio
    async def test_warns_for_silent_unaddressed_chat(self):
        state = _state()
        with _patch_last_event(NOW - (SILENCE_THRESHOLD_SECONDS + 60)):
            warned = await check_silent_chat(
                chat_id=-1001,
                chat_title="Cyndra Dev",
                project=_project(),
                state=state,
                now=NOW,
            )
        assert warned is True
        assert state.warned_chats[-1001] == NOW

    @pytest.mark.asyncio
    async def test_cold_start_short_circuits_before_redis(self):
        """During the cold-start window the per-chat check returns without touching Redis."""
        with _patch_last_event(NOW - (SILENCE_THRESHOLD_SECONDS + 60)) as p:
            warned = await check_silent_chat(
                chat_id=-1001,
                chat_title="Cyndra Dev",
                project=_project(),
                state=_state(bridge_start_ts=NOW - 60),
                now=NOW,
            )
        assert warned is False
        p.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_mention_gated_before_redis(self):
        """Non-respond_to_unaddressed chats short-circuit before touching Redis."""
        with _patch_last_event(NOW - (SILENCE_THRESHOLD_SECONDS + 60)) as p:
            warned = await check_silent_chat(
                chat_id=-1002,
                chat_title="Mention Only",
                project=_project(respond_to_unaddressed=False),
                state=_state(),
                now=NOW,
            )
        assert warned is False
        p.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_project_is_skipped(self):
        with _patch_last_event(NOW - (SILENCE_THRESHOLD_SECONDS + 60)):
            warned = await check_silent_chat(
                chat_id=-1003,
                chat_title="No Project",
                project=None,
                state=_state(),
                now=NOW,
            )
        assert warned is False
