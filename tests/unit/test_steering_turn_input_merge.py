"""D3 (#2458): steering drain must not drop the session's own unconsumed message.

When a steering message lands on a session BEFORE its first turn (e.g. the
reply-to path steers into a still-``pending`` session), the turn-boundary
drain previously replaced ``enriched_text`` with ``steering_msgs[0]`` — the
session's own ``message_text`` was silently lost.

``agent.session_executor.merge_steering_turn_input`` is the production
predicate: it combines the session's own text with the steering text when the
own message has not yet been consumed (no prior claude session UUID), and
keeps the replace behavior once the own message has already run a turn.
"""

import pytest

from agent.session_executor import merge_steering_turn_input


def test_first_turn_combines_own_message_with_steering():
    result = merge_steering_turn_input(
        own_text="screenshot: this needs to be clearer about what's missing",
        steering_text="this is for the new 360 report when it can't generate",
        own_message_consumed=False,
    )
    # Both messages survive, own message first (it arrived first).
    assert "clearer about what's missing" in result
    assert "360 report" in result
    assert result.index("clearer") < result.index("360 report")


def test_consumed_own_message_is_not_replayed():
    """After the first turn, own_text is stale — steering alone is the turn input."""
    result = merge_steering_turn_input(
        own_text="original already-consumed message",
        steering_text="follow-up steer",
        own_message_consumed=True,
    )
    assert result == "follow-up steer"


@pytest.mark.parametrize("own_text", [None, "", "   ", "None"])
def test_empty_or_sentinel_own_text_yields_steering_only(own_text):
    result = merge_steering_turn_input(
        own_text=own_text,
        steering_text="follow-up steer",
        own_message_consumed=False,
    )
    assert result == "follow-up steer"
