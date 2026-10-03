"""The real wire on Valor's account, in a window Tom opens.

Skipped unless `VALOR_TELEGRAM_WINDOW=1`, which only Tom sets, with the
bridge's job booted out and `VALOR_TELEGRAM_WINDOW_CHAT` naming the
"Valor rebuild" group's id. A child performs one send on the real wire and
pauses once `SendMessageRequest` has returned; the test kills it by its pid
and the lookup finds exactly one message.
"""

import asyncio
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest

from bridges.telegram.bridge import TelegramBridge
from bridges.telegram.kernel import session_path
from bridges.telegram.wire import TelethonWire
from tests.telegram_kernel import Action, StandIn

pytestmark = [
    pytest.mark.spend(usd=0),
    pytest.mark.skipif(os.environ.get("VALOR_TELEGRAM_WINDOW") != "1", reason="Tom's window only"),
]


def test_killed_after_telegram_accepted_a_real_send(tmp_path):
    from bridges.telegram.__main__ import credentials

    chat = os.environ["VALOR_TELEGRAM_WINDOW_CHAT"]
    stamp = datetime.now(UTC).isoformat()
    text, key, mark = f"window check {stamp}", f"window:{stamp}", tmp_path / "mark"
    at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.telegram_child",
            "perform-live",
            str(session_path()),
            chat,
            text,
            key,
            str(mark),
        ]
    )
    deadline = time.monotonic() + 60
    while not mark.exists():
        assert time.monotonic() < deadline and proc.poll() is None
        time.sleep(0.1)
    proc.kill()
    proc.wait(10)

    async def look():
        api_id, api_hash = credentials()
        w = TelethonWire(session_path(), api_id, api_hash)
        await w.connect()
        try:
            _, lookup = TelegramBridge(w, StandIn(owned=[chat]).kernel()).performers()[
                "telegram.send_message"
            ]
            return await lookup(Action("telegram.send_message", chat, {"text": text}), key, at)
        finally:
            await w.disconnect()

    found = asyncio.run(look())
    assert found is not None and len(found["sent"]) == 1
