"""`TelethonWire`'s mapping of what Telethon does when a connection drops."""

import asyncio

import pytest

from bridges.telegram.wire import InDoubt, NotConnected, TelethonWire

pytestmark = pytest.mark.spend(usd=0)


class Client:
    """Telethon's client as the wire sees it: each request waits on a
    future the test resolves, as Telethon's sender does."""

    def __init__(self):
        self.pending: asyncio.Future | None = None
        self.disconnected = asyncio.get_running_loop().create_future()

    def is_connected(self):
        return True

    async def _wait(self, *_, **__):
        self.pending = asyncio.get_running_loop().create_future()
        return await self.pending

    get_messages = send_read_acknowledge = download_media = __call__ = _wait


def wire() -> TelethonWire:
    w = TelethonWire.__new__(TelethonWire)
    w.client = Client()
    return w


READS = {
    "history": lambda w: w.history(1),
    "get": lambda w: w.get(1, [2]),
    "mark_read": lambda w: w.mark_read(1, 2),
    "download": lambda w: w.download(type("M", (), {"raw": None})()),
}


async def _dropped(call, w):
    t = asyncio.create_task(call(w))
    while w.client.pending is None:
        await asyncio.sleep(0)
    w.client.pending.cancel()  # Telethon cancelling a dropped connection's requests
    return t


@pytest.mark.parametrize("name", READS)
def test_a_read_cancelled_by_a_drop_is_not_connected(name):
    async def go():
        w = wire()
        with pytest.raises(NotConnected):
            await (await _dropped(READS[name], w))

    asyncio.run(go())


def test_a_send_cancelled_by_a_drop_is_in_doubt():
    async def go():
        w = wire()
        with pytest.raises(InDoubt):
            await (await _dropped(lambda w: w._send(object()), w))

    asyncio.run(go())


@pytest.mark.parametrize("call", [READS["history"], lambda w: w._send(object())], ids=["read", "send"])
def test_a_cancel_of_the_caller_still_cancels(call):
    async def go():
        w = wire()
        t = asyncio.create_task(call(w))
        while w.client.pending is None:
            await asyncio.sleep(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(go())


@pytest.mark.parametrize("error", [TimeoutError(60, "Operation timed out"), OSError("reset"), None])
def test_a_drop_with_or_without_an_error_is_returned_and_reconnected(error):
    from bridges.telegram.bridge import TelegramBridge

    async def go():
        w = wire()
        b = TelegramBridge.__new__(TelegramBridge)
        b.wire = w
        reconnected = asyncio.Event()

        async def connect():
            reconnected.set()
            await asyncio.Event().wait()  # the next drop never comes

        b.connect = connect
        keep = asyncio.create_task(b._keep_connected())
        if error is None:
            w.client.disconnected.set_result(None)
        else:
            w.client.disconnected.set_exception(error)  # what Telethon's sender does on an errored drop
        await asyncio.wait_for(reconnected.wait(), 1)
        keep.cancel()

    asyncio.run(go())


def test_a_cancel_of_the_drop_waiter_still_cancels():
    async def go():
        w = wire()
        t = asyncio.create_task(w.disconnected())
        await asyncio.sleep(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(go())
