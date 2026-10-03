"""The real `wire.py` on Telegram's test servers, on a test account.

Runs only when `VALOR_LIVE=1` and `VALOR_TELEGRAM_TEST_DC=1`, with a test
account's session signed in by `python -m bridges.telegram login --test-dc
--session PATH`, PATH given in `VALOR_TELEGRAM_TEST_SESSION`, and the API id
and hash in the key directory. It writes to the account's Saved Messages
only. Never Valor's session, never the ledger.
"""

import asyncio
import hashlib
import os
from datetime import UTC, datetime

import pytest

from bridges.telegram.bridge import TelegramBridge
from bridges.telegram.kernel import from_core
from bridges.telegram.wire import DuplicateRandomId, TelethonWire
from core.broker import Action, Unknown

pytestmark = [
    pytest.mark.spend(usd=0),
    pytest.mark.skipif(
        os.environ.get("VALOR_LIVE") != "1" or os.environ.get("VALOR_TELEGRAM_TEST_DC") != "1",
        reason="live Telegram test servers: VALOR_LIVE=1 and VALOR_TELEGRAM_TEST_DC=1",
    ),
]


def wire() -> TelethonWire:
    from bridges.telegram.__main__ import credentials

    api_id, api_hash = credentials()
    return TelethonWire(os.environ["VALOR_TELEGRAM_TEST_SESSION"], api_id, api_hash, test_dc=True)


def test_send_split_file_lookup_and_duplicate_on_the_test_servers(dsn, tmp_path):
    async def go():
        w = wire()
        await w.connect()
        try:
            me = (await w.client.get_me()).id
            chat = str(me)
            bridge = TelegramBridge(w, from_core(dsn), sends=tmp_path / "telegram-sends.json")
            perform, lookup = bridge.performers()["telegram.send_message"]
            at = datetime.now(UTC).isoformat()
            stamp = datetime.now(UTC).isoformat()

            f = tmp_path / "evidence.txt"
            f.write_bytes(stamp.encode())
            files = [{"path": str(f), "sha256": hashlib.sha256(stamp.encode()).hexdigest()}]
            action = Action(
                "telegram.send_message", chat, {"text": f"{stamp}\n" + "a" * 4100, "files": files}
            )
            out = await perform(action, f"live:{stamp}")
            assert len(out["sent"]) == 3
            assert await lookup(action, f"live:{stamp}", at) == out

            # The same key again: Telegram drops the repeat.
            with pytest.raises(Unknown):
                await perform(action, f"live:{stamp}")
            with pytest.raises(DuplicateRandomId):
                from bridges.telegram.send import random_id

                await w.send_text(
                    int(chat), "repeat", random_id=random_id(f"live:{stamp}", 0), reply_to=None, topic_id=None
                )

            # Downloads go through the real wire.
            [msg] = await w.get(int(chat), [int(out["sent"][2]["message_id"])])
            assert await w.download(msg) == stamp.encode()
        finally:
            await w.disconnect()

    asyncio.run(go())
