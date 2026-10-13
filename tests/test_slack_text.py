"""How a post Slack stored is matched with the text that was requested."""

import pytest

from bridges.slack.send import same
from tests.slack_emulator import slack_text


@pytest.mark.parametrize(
    "requested",
    [
        "plain words",
        "  padded \n",
        "<@U0TOM> hello",
        "see <#C0OPS> and <!here>",
        "a < b & c > d &amp; e",
        "<@U0TOM> see https://example.com/a?b=1&c=2 in <#C0OPS> & <this>",
    ],
)
def test_a_post_as_slack_stores_it_matches_the_text_requested(requested):
    assert same(slack_text(requested), requested)


@pytest.mark.parametrize(
    ("stored", "requested"),
    [
        ("<@U0TOM> hello", "<@U0ELSE> hello"),
        ("<@U0TOM> hello", "hello"),
        ("hello", "hello there"),
    ],
)
def test_a_different_post_does_not_match(stored, requested):
    assert not same(stored, requested)
