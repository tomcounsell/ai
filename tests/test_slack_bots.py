"""A person's Slack message written through an app is theirs; a bot's
post is not (2026-11-24: Tom's replies through Yudame Tools carried the
app's `bot_id` and were dropped). Made-up ids; the shapes are Slack's."""

from __future__ import annotations

import pytest

from bridges.slack import gap, inbound
from tests.test_slack_workspaces import connect_all, run, two

APP = {"id": "B0APP", "app_id": "A0APP", "name": "Some App", "team_id": "T0BBB"}
PERSON_VIA_APP = {"bot_id": "B0APP", "bot_profile": APP}
BOT_POST = {"user": "U0BOT", "bot_id": "B0BOT", "bot_profile": {**APP, "id": "B0BOT", "user_id": "U0BOT"}}
BARE_BOT_ID = {"bot_id": "B0BOT"}
OTHER_USER_PROFILE = {"bot_id": "B0APP", "bot_profile": {**APP, "user_id": "U0ELSE"}}

CASES = [
    ("a person's message", {}, True),
    ("a person's message through an app", PERSON_VIA_APP, True),
    ("an app's profile naming another user", OTHER_USER_PROFILE, True),
    ("a bot's post, its profile naming its user", BOT_POST, False),
    ("a bot_id with no bot_profile", BARE_BOT_ID, False),
    ("a bot_message with no user", {"subtype": "bot_message", "user": None, "bot_id": "B0X"}, False),
    ("Valor's own post through its app", {"user": "UVALORB", **PERSON_VIA_APP}, False),
    ("Slackbot", {"user": "USLACKBOT"}, False),
    ("an edit", {"subtype": "message_changed", **PERSON_VIA_APP}, False),
]


def message(ts, extra):
    m = {
        "type": "message",
        "channel": "C0SAME",
        "channel_type": "channel",
        "user": "UTOM",
        "ts": ts,
        "text": "hi",
    }
    return {k: v for k, v in {**m, **extra}.items() if v is not None}


@pytest.mark.parametrize("name, extra, kept", CASES, ids=[c[0] for c in CASES])
def test_who_wrote_it_decides_live_and_in_gap_fill(tmp_path, name, extra, kept):
    got = []
    b, _, wb = two(owned=("T0BBB/C0SAME",), got=got, seen=tmp_path / "seen.json")
    connect_all(b)
    assert inbound.wanted(message("1", extra), "UVALORB") is kept

    run(wb.on_message("E1", message("1793700000.000100", extra)))
    assert wb.acked == ["E1"]  # dropped or recorded, either way acked
    assert len(got) == int(kept)

    page = [message("1793700000.000200", extra)]
    run(
        b._receive_found(
            "T0BBB/C0SAME", gap.Pass(channel="C0SAME", start="0", inclusive=True, top="0"), False, page, wb
        )
    )
    assert [m.message_id for m in got] == ["1793700000.000100", "1793700000.000200"][: 2 * int(kept)]
    assert all(m.sender_id == "T0BBB/UTOM" for m in got)
