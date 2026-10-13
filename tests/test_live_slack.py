"""The real wire on Valor's Slack account, in a test channel.

Skipped without `slack-keys` in the key directory and
`VALOR_SLACK_TEST_CHANNEL` naming a channel id in Valor's workspace. It
posts twice and reads the channel back with `include_all_metadata`:

- whether Slack kept the marker on a user-token post, which says which
  `lookup` branch the bridge runs (the marker, or the text match);
- how Slack stored a part with a URL, an `&`, and a trailing newline, and
  that the text match finds it.

Both are printed; run with `-s` to see them.
"""

import asyncio
import os
from datetime import UTC, datetime

import pytest

from bridges.slack.kernel import keyfile
from bridges.slack.send import EVENT_TYPE, marker, normal
from bridges.slack.wire import SlackWire

CHANNEL = os.environ.get("VALOR_SLACK_TEST_CHANNEL", "")

pytestmark = [
    pytest.mark.spend(usd=0),
    pytest.mark.skipif(not keyfile().exists() or not CHANNEL, reason="needs slack-keys and a test channel"),
]


async def _noop(*_args) -> None:
    return None


def test_a_user_token_post_and_how_slack_stores_it():
    from bridges.slack.__main__ import credentials

    stamp = datetime.now(UTC).isoformat()
    key = marker(f"live:{stamp}")
    text = f"live check {stamp}: see https://example.com/a?b=1&c=2 & more\n"

    async def go():
        wire = SlackWire(*credentials())
        await wire.connect(_noop, lambda: None)
        try:
            meta = {"event_type": EVENT_TYPE, "event_payload": {"key": key, "part": 0, "parts": 1}}
            marked = await wire.post(CHANNEL, f"marker check {stamp}", thread_ts=None, metadata=meta)
            plain = await wire.post(CHANNEL, text, thread_ts=None, metadata=None)
            page, _ = await wire.history(CHANNEL, oldest=marked, inclusive=True)
            return {m["ts"]: m for m in page}, marked, plain
        finally:
            await wire.close()

    found, marked, plain = asyncio.run(go())
    kept = found[marked].get("metadata", {}).get("event_payload", {}).get("key") == key
    stored = found[plain]["text"]
    print(f"\nmarker kept on a user-token post: {kept}")
    print(f"stored form of the URL part: {stored!r}")
    assert normal(stored) == text.strip()
