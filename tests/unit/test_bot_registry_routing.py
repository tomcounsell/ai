"""Routing tests for the registered-bot loop-guard (issue #1574).

Covers find_project_for_bot resolution and the should_respond_sync guard that
makes a registered bot never trigger a response, in both DM and group paths.
These manipulate the module-level BOT_ID_TO_PROJECT map directly (as the bridge
populates it at startup) and restore it afterward.
"""

from __future__ import annotations

import pytest

from bridge import routing


@pytest.fixture
def registered_bot():
    """Register a bot peer in the routing module for the duration of a test.

    Also pins the DM-path module globals RESPOND_TO_DMS/DM_WHITELIST to their
    defaults: a sibling test importing bridge.telegram_bridge on the same xdist
    worker overwrites routing.DM_WHITELIST with the real whitelist
    (bridge/telegram_bridge.py:674-675), which would make the non-bot DM sender
    fall outside the whitelist and flip should_respond_sync to False (#2093).
    """
    bot_id = 8837490628
    saved = dict(routing.BOT_ID_TO_PROJECT)
    saved_respond_to_dms = routing.RESPOND_TO_DMS
    saved_dm_whitelist = routing.DM_WHITELIST
    routing.BOT_ID_TO_PROJECT[bot_id] = {"_key": "valor", "name": "Valor AI"}
    routing.RESPOND_TO_DMS = True
    routing.DM_WHITELIST = set()
    yield bot_id
    routing.BOT_ID_TO_PROJECT.clear()
    routing.BOT_ID_TO_PROJECT.update(saved)
    routing.RESPOND_TO_DMS = saved_respond_to_dms
    routing.DM_WHITELIST = saved_dm_whitelist


def test_find_project_for_bot_hit(registered_bot):
    proj = routing.find_project_for_bot(registered_bot)
    assert proj is not None
    assert proj["_key"] == "valor"


def test_find_project_for_bot_miss(registered_bot):
    assert routing.find_project_for_bot(999999) is None


def test_find_project_for_bot_none_sender():
    assert routing.find_project_for_bot(None) is None


def test_should_respond_sync_dm_bot_is_silenced(registered_bot):
    """A registered bot DM must never trigger a response, even though DMs would
    otherwise pass should_respond_sync."""
    assert (
        routing.should_respond_sync(
            text="Hello! How can I help you today?",  # bot reply WITH a question
            is_dm=True,
            project={"_key": "valor"},
            sender_id=registered_bot,
        )
        is False
    )


def test_should_respond_sync_group_bot_is_silenced(registered_bot):
    """Same deterministic guard in the group path."""
    assert (
        routing.should_respond_sync(
            text="some bot chatter",
            is_dm=False,
            project={"_key": "valor", "telegram": {"respond_to_all": True}},
            sender_id=registered_bot,
        )
        is False
    )


def test_should_respond_sync_non_bot_dm_still_responds(registered_bot):
    """A normal (non-bot) DM sender is unaffected by the guard."""
    assert (
        routing.should_respond_sync(
            text="hi",
            is_dm=True,
            project={"_key": "valor"},
            sender_id=111111,  # not a registered bot
        )
        is True
    )
