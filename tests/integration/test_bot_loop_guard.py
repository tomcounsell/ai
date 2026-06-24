"""Integration test: a registered bot's inbound message never spawns a session.

This proves the deterministic loop-guard invariant (issue #1574) at the routing
layer, the layer that decides spawn-vs-record. It would loop under a naive
implementation where a registered bot resolved a project (its no-reply_to reply
keeps spawning fresh sessions).

We verify two independent layers:
  1. A registered bot id resolves NO project via the spawn-path resolvers
     (find_project_for_dm / find_project_for_chat) — so the bridge's
     `if not project: return` guard alone already blocks the spawn.
  2. The explicit deterministic guard: find_project_for_bot(id) is truthy and
     should_respond_sync returns False for it — belt-and-suspenders, covering
     DM and group, regardless of reply-to or question content.
"""

from __future__ import annotations

import pytest

from bridge import routing


@pytest.fixture
def registered_bot_map():
    """Register a bot peer the way the bridge does at startup, kept ONLY in the
    bot map (never in DM_USER_TO_PROJECT / GROUP_TO_PROJECT)."""
    bot_id = 8837490628
    saved_bots = dict(routing.BOT_ID_TO_PROJECT)
    saved_dm = dict(routing.DM_USER_TO_PROJECT)
    saved_groups = dict(routing.GROUP_TO_PROJECT)
    saved_whitelist = set(routing.DM_WHITELIST)

    routing.BOT_ID_TO_PROJECT.clear()
    routing.BOT_ID_TO_PROJECT[bot_id] = {"_key": "valor", "name": "Valor AI"}
    # Deliberately do NOT add to DM_USER_TO_PROJECT / GROUP_TO_PROJECT / whitelist.
    yield bot_id

    routing.BOT_ID_TO_PROJECT.clear()
    routing.BOT_ID_TO_PROJECT.update(saved_bots)
    routing.DM_USER_TO_PROJECT.clear()
    routing.DM_USER_TO_PROJECT.update(saved_dm)
    routing.GROUP_TO_PROJECT.clear()
    routing.GROUP_TO_PROJECT.update(saved_groups)
    routing.DM_WHITELIST.clear()
    routing.DM_WHITELIST.update(saved_whitelist)


def test_registered_bot_resolves_no_project_on_spawn_path(registered_bot_map):
    """Layer 1: the bot does not resolve a project via the spawn-path resolvers,
    so the bridge's `if not project: return` already blocks any session."""
    bot_id = registered_bot_map
    assert routing.find_project_for_dm(bot_id) is None
    # Even a chat title lookup must not resolve it (it's not a monitored group).
    assert routing.find_project_for_chat("cyndra_staff_bot") is None


def test_deterministic_guard_silences_bot_dm_and_group(registered_bot_map):
    """Layer 2: the explicit guard returns False for the bot in BOTH paths,
    even when the reply contains a question (which the #1318 heuristic would
    NOT silence)."""
    bot_id = registered_bot_map

    # DM path, bot reply WITH a question — must still be silent.
    assert (
        routing.should_respond_sync(
            text="Hello! How can I help you today?",
            is_dm=True,
            project={"_key": "valor"},
            sender_id=bot_id,
        )
        is False
    )

    # Group path, respond_to_all=True — must still be silent for the bot.
    assert (
        routing.should_respond_sync(
            text="bot chatter in a group",
            is_dm=False,
            project={"_key": "valor", "telegram": {"respond_to_all": True}},
            sender_id=bot_id,
        )
        is False
    )


def test_non_bot_sender_unaffected(registered_bot_map):
    """A real human DM sender is not silenced by the bot guard."""
    assert (
        routing.should_respond_sync(
            text="hey, can you help?",
            is_dm=True,
            project={"_key": "valor"},
            sender_id=42,  # not a registered bot
        )
        is True
    )
