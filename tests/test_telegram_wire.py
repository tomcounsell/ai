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
        self.dropped = asyncio.get_running_loop().create_future()

    @property
    def disconnected(self):
        return asyncio.shield(self.dropped)  # as Telethon's client gives it: a new outer future each time

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
def test_a_drop_with_or_without_an_error_is_returned_and_reconnected(error, caplog):
    """And leaves no unretrieved error for asyncio to log at ERROR, which
    Sentry's logging integration would report."""
    import gc

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
            w.client.dropped.set_result(None)
        else:
            w.client.dropped.set_exception(error)  # what Telethon's sender does on an errored drop
        await asyncio.wait_for(reconnected.wait(), 1)
        keep.cancel()

    asyncio.run(go())
    gc.collect()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_a_cancel_of_the_drop_waiter_still_cancels():
    async def go():
        w = wire()
        t = asyncio.create_task(w.disconnected())
        await asyncio.sleep(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(go())


def _real(tmp_path) -> TelethonWire:
    """A real Telethon client on a temp session; only the network calls are stood in."""
    w = TelethonWire(tmp_path / "s", 1, "x")
    w.premium = False

    async def yes():
        return True

    w.client.is_user_authorized = yes
    return w


def test_a_reconnect_stops_the_dropped_connections_update_and_keepalive_loops(tmp_path):
    """The Captain, 2026-11-08: each reconnect left the dropped connection's
    loops running, so the stale keepalives dropped every new connection
    within a minute and the stale update loops broke each other's update
    state (VALOR-MJ, MK), then disconnected under the sender (MN, MP)."""

    async def go():
        w = _real(tmp_path)
        forever = asyncio.Event().wait
        old = [asyncio.create_task(forever()), asyncio.create_task(forever())]
        w.client._updates_handle, w.client._keepalive_handle = old  # left by the dropped connection
        await asyncio.sleep(0)
        seen = {}

        async def connect():
            seen["old done at connect"] = [t.done() for t in old]
            new = [asyncio.create_task(forever()), asyncio.create_task(forever())]
            w.client._updates_handle, w.client._keepalive_handle = new  # as Telethon's connect starts them
            seen["new"] = new

        w.client.connect = connect
        await w.connect()
        assert seen["old done at connect"] == [True, True]
        assert all(t.cancelled() for t in old)
        assert not any(t.done() for t in seen["new"])
        # Telethon's disconnect closed the session file; it reopens on the next write.
        w.client.session.set_dc(2, "149.154.167.40", 443)
        w.client.session.save()
        for t in seen["new"]:
            t.cancel()

    asyncio.run(go())


def test_a_connect_that_failed_after_telethon_started_its_loops_is_torn_down_by_the_next(tmp_path):
    from bridges.telegram.wire import NotConnected

    async def go():
        w = _real(tmp_path)
        started = []

        async def connect():
            started.append([asyncio.create_task(asyncio.Event().wait()) for _ in range(2)])
            w.client._updates_handle, w.client._keepalive_handle = started[-1]

        async def authorized():
            if len(started) == 1:
                raise TimeoutError(60, "Operation timed out")
            return True

        w.client.connect = connect
        w.client.is_user_authorized = authorized
        with pytest.raises(NotConnected):
            await w.connect()
        assert not any(t.done() for t in started[0])
        await w.connect()
        assert all(t.cancelled() for t in started[0])
        assert len(started) == 2 and not any(t.done() for t in started[1])
        for t in started[1]:
            t.cancel()

    asyncio.run(go())


def test_a_message_whose_handling_the_reconnect_cancelled_is_retaken_by_the_gap_fill(tmp_path):
    """With `sequential_updates` the live handler runs in the update loop,
    so the reconnect's teardown cancels a message still being received."""
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime

    from bridges.telegram.bridge import TelegramBridge
    from bridges.telegram.wire import Msg

    chat = -100
    msg = Msg(chat_id=chat, id=42, date=datetime.now(UTC), sender_id=7, text="hi")

    class Wire:
        def on_message(self, handler):
            pass

        async def history(self, chat_id, **kw):
            return [msg]

        async def mark_read(self, chat_id, max_id):
            pass

    class Kernel:
        inbound_dir = str(tmp_path)
        Inbound = dict
        split_text = None

        def __init__(self):
            self.rows, self.entered = [], asyncio.Event()

        def owns(self, channel, chat_id):
            return True

        def owned(self, channel):
            return [str(chat)]

        def transcribe(self, a):
            return a

        @asynccontextmanager
        async def conn(self):
            yield None

        async def lowest(self, conn, channel, chat_id):
            return 41

        async def recorded(self, conn, channel, chat_id, ids):
            return {r["message_id"] for r in self.rows} & set(ids)

        async def receive(self, conn, inbound):
            if not self.entered.is_set():
                self.entered.set()
                await asyncio.Event().wait()  # cancelled here, before it is recorded
            self.rows.append(inbound)

    async def go():
        k = Kernel()
        b = TelegramBridge(Wire(), k, seen=tmp_path / "seen", sends=tmp_path / "sends")
        live = asyncio.create_task(b.handle(msg))
        await k.entered.wait()
        live.cancel()  # the old update loop, cancelled by the reconnect's teardown
        with pytest.raises(asyncio.CancelledError):
            await live
        assert await b.fill()
        return k.rows

    rows = asyncio.run(go())
    assert [r["message_id"] for r in rows] == ["42"]
