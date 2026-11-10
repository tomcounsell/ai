"""One Slack bridge serves every workspace in `slack-keys` (issue 3680),
over fake wires: each chat goes to its own team's wire, and one
workspace down, spent or reconnecting leaves the other serving."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from bridges.slack import __main__ as slack_main
from bridges.slack import gap
from bridges.slack import wire as slack_wire
from bridges.slack.bridge import SlackBridge
from bridges.slack.wire import NotConnected
from core import broker, intake


def run(coro):
    return asyncio.run(coro)


class Wire:
    """One workspace: `team_id` from `slack-keys`, `user_id` once connected."""

    def __init__(self, team, user="UVALOR", *, fails=None):
        self.team_id, self.user_id = team, ""
        self._user = user
        self.fails = fails  # an exception every connect raises
        self.up = False
        self.calls, self.acked, self.posted, self.reacted = [], [], [], []
        self.history_fails = None
        self.on_message = self.on_hello = None

    async def connect(self, on_message, on_hello):
        self.calls.append("connect")
        if self.fails:
            raise self.fails
        self.user_id, self.up = self._user, True
        self.on_message, self.on_hello = on_message, on_hello

    async def connected(self):
        return self.up

    async def close(self):
        self.up = False

    async def ack(self, envelope_id):
        self.acked.append(envelope_id)

    async def user_name(self, user):
        return "Tom"

    async def history(self, channel, *, oldest=None, inclusive=False, cursor=None, limit=None, latest=None):
        if latest is not None and oldest is None:  # a receive's read of Valor's posts before it
            self.calls.append(("posted", channel))
            return [], None
        self.calls.append(("history", channel))
        if self.history_fails:
            raise self.history_fails
        return [{"ts": "1793700000.000100", "user": "UTOM", "text": "hi", "type": "message"}], None

    async def replies(self, channel, ts, **kw):
        return [], None

    async def post(self, channel, text, *, thread_ts, metadata):
        if not self.user_id:
            raise NotConnected(f"{self.team_id} is not connected yet")
        self.posted.append((channel, text))
        return "1793700001.000100"

    async def react(self, channel, ts, name):
        self.reacted.append((channel, ts, name))

    async def unreact(self, channel, ts, name):
        pass


def kernel(owned=(), got=None):
    got = [] if got is None else got

    @asynccontextmanager
    async def conn():
        yield None

    async def receive(c, inbound):
        got.append(inbound)

    async def none(*a):
        return None

    async def empty(*a):
        return set()

    return SimpleNamespace(
        owns=lambda channel, chat: chat in owned, owned=lambda channel: list(owned), conn=conn,
        receive=receive, Inbound=intake.Inbound, inbound_dir="/nonexistent", transcribe=lambda a: a,
        threads=empty, lowest_ts=none, recorded=empty, root_of=none, claimed=empty, serve_tick_s=0.01,
        Failed=broker.Failed, Unknown=broker.Unknown, Release=object, ReactionDue=type(None),
        split_text=lambda t: [t], operators=(),
    )  # fmt: skip


def two(owned=(), got=None, *, seen):
    a, b = Wire("T0AAA", "UVALORA"), Wire("T0BBB", "UVALORB")
    return SlackBridge([a, b], kernel(owned, got), seen=seen), a, b


def connect_all(bridge):
    async def go():
        up = asyncio.Event()
        for w in bridge.wires.values():
            await bridge.connect(w, up)

    run(go())


# -- tokens ------------------------------------------------------------------


def test_credentials_are_every_whole_pair_and_none_is_refused(tmp_path, monkeypatch):
    from bridges.slack import kernel as slack_kernel

    keys = tmp_path / "slack-keys"
    monkeypatch.setattr(slack_kernel, "keyfile", lambda: keys)
    keys.write_text(
        "T0AAA_SLACK_APP_TOKEN=xapp-a\nT0AAA_SLACK_USER_TOKEN=xoxp-a\n"
        "T0BBB_SLACK_APP_TOKEN=xapp-b\nT0BBB_SLACK_USER_TOKEN=xoxp-b\nT0CCC_SLACK_USER_TOKEN=xoxp-c\n"
    )
    assert slack_main.credentials() == {"T0AAA": ("xapp-a", "xoxp-a"), "T0BBB": ("xapp-b", "xoxp-b")}
    keys.write_text("T0CCC_SLACK_USER_TOKEN=xoxp-c\n")
    with pytest.raises(SystemExit, match="no workspace's tokens"):
        slack_main.credentials()


def test_keys_copies_each_pair_of_this_mac_under_its_own_team_and_no_other_macs(tmp_path, monkeypatch):
    from core.credentials import _parse_env

    vault = tmp_path / ".env"
    vault.write_text(
        "SLACK_APP_TOKEN_VALOR_THE_CAPTAIN=xapp-y\nSLACK_USER_TOKEN_VALOR_THE_CAPTAIN=xoxp-y\n"
        "SLACK_APP_TOKEN_VALOR_THE_CAPTAIN__CLIENT=xapp-c\nSLACK_USER_TOKEN_VALOR_THE_CAPTAIN__CLIENT=xoxp-c\n"
        "SLACK_APP_TOKEN_VALOR_THE_CAPTAIN_2=xapp-x\nSLACK_USER_TOKEN_VALOR_THE_CAPTAIN_2=xoxp-x\n"
    )
    teams = {"xoxp-y": "T0AAA", "xoxp-c": "T0BBB", "xoxp-x": "T0XXX"}
    asked = []
    monkeypatch.setattr(slack_wire, "team_of", lambda token: asked.append(token) or teams[token])
    keyfile = tmp_path / "slack-keys"
    assert set(slack_main.copy(vault, keyfile, "Valor the Captain").values()) == {"written"}
    assert _parse_env(keyfile.read_text()) == {
        "T0AAA_SLACK_APP_TOKEN": "xapp-y", "T0AAA_SLACK_USER_TOKEN": "xoxp-y",
        "T0BBB_SLACK_APP_TOKEN": "xapp-c", "T0BBB_SLACK_USER_TOKEN": "xoxp-c",
    }  # fmt: skip
    assert asked == ["xoxp-y", "xoxp-c"]


# -- receiving ---------------------------------------------------------------


def msg(ts="1793700000.000100", text="<@UVALORB> hello", channel="C0SAME"):
    return {
        "type": "message",
        "channel": channel,
        "channel_type": "channel",
        "user": "UTOM",
        "ts": ts,
        "text": text,
    }


def test_a_message_is_its_own_workspaces_chat_named_by_its_own_valor(tmp_path):
    """The same channel id in two workspaces is two chats: only the owned
    one is received, with Valor's own tag named from that workspace's id."""
    got = []
    b, wa, wb = two(owned=("T0BBB/C0SAME",), got=got, seen=tmp_path / "seen.json")
    connect_all(b)
    run(wb.on_message("E1", msg()))
    run(wa.on_message("E2", msg(text="<@UVALORA> hello")))
    [m] = got
    assert (m.chat_id, m.sender_id, m.text) == ("T0BBB/C0SAME", "T0BBB/UTOM", "<@UVALORB|valor> hello")
    assert (wb.acked, wa.acked) == (["E1"], ["E2"])


def test_gap_fill_reads_each_chat_on_its_own_wire_and_one_workspace_failing_stops_not_the_other(tmp_path):
    """A down, then A's budget spent: B's chats are filled each time, and
    a chat of a workspace with no keys is not read."""
    b, wa, wb = two(owned=("T0AAA/C0A", "T0BBB/C0B", "T0ZZZ/C0Z"), seen=tmp_path / "seen.json")
    connect_all(b)
    wa.history_fails = NotConnected("A is down")
    run(b.tick())
    assert wa.calls[1:] == [("history", "C0A")] and wb.calls[1:] == [("history", "C0B")]

    b, wa, wb = two(owned=("T0AAA/C0A", "T0BBB/C0B"), seen=tmp_path / "seen2.json")
    connect_all(b)
    b.budgets["T0AAA"] = gap.Budget(calls=0)
    run(b.tick())
    assert wa.calls[1:] == [] and wb.calls[1:] == [("history", "C0B")]


def test_a_workspace_that_reconnects_fills_only_its_own_chats(tmp_path):
    """B's socket drops and comes back (a new `hello`) while A serves: the
    hello's gap fill reads B's chats only, and A still receives."""
    got = []
    b, wa, wb = two(owned=("T0AAA/C0A", "T0BBB/C0B"), got=got, seen=tmp_path / "seen.json")
    connect_all(b)

    async def hello():
        wb.on_hello()
        await asyncio.gather(*b._tasks)
        await wa.on_message("E1", msg(channel="C0A", text="still here"))

    run(hello())
    # A's receive reads only Valor's posts before its message, on A's own wire.
    assert ("history", "C0B") in wb.calls and wa.calls[1:] == [("posted", "C0A")]
    assert [m.chat_id for m in got] == ["T0AAA/C0A"] and wa.acked == ["E1"]


# -- sending -----------------------------------------------------------------


def release(target, text="hello"):
    return broker.Action("slack.send_message", target, {"text": text})


def test_a_send_goes_on_its_own_workspaces_wire_and_another_team_fails(tmp_path):
    b, wa, wb = two(seen=tmp_path / "seen.json")
    connect_all(b)
    perform, lookup = b.performers()["slack.send_message"]
    out = run(perform(release("t0bbb/C0B"), "k1"))
    assert out["sent"][0]["chat_id"] == "T0BBB/C0B" and wb.posted == [("C0B", "hello")] and not wa.posted
    with pytest.raises(broker.Failed, match="T0ZZZ is not a connected workspace"):
        run(perform(release("T0ZZZ/C0Z"), "k2"))
    assert run(lookup(release("T0ZZZ/C0Z"), "k2", "2026-11-03T00:00:00+00:00")) is None


def test_a_send_to_a_workspace_not_yet_connected_fails_and_its_lookup_waits(tmp_path):
    """A send fails visibly rather than waiting on a workspace whose token
    may be revoked; a lookup in doubt stays in flight until it connects."""
    b, wa, _ = two(seen=tmp_path / "seen.json")
    run(b.connect(wa, asyncio.Event()))  # B never connects
    perform, lookup = b.performers()["slack.send_message"]
    with pytest.raises(broker.Failed, match="T0BBB is not connected yet"):
        run(perform(release("T0BBB/C0B"), "k1"))
    with pytest.raises(broker.Unknown, match="not connected to T0BBB yet"):
        run(lookup(release("T0BBB/C0B"), "k1", "2026-11-03T00:00:00+00:00"))


def test_a_reaction_is_placed_on_its_own_workspaces_wire(tmp_path):
    b, wa, wb = two(seen=tmp_path / "seen.json")
    connect_all(b)
    results = []

    class Box:
        async def reacted(self, item, error=None):
            results.append(error)

    item = SimpleNamespace(chat_id="T0BBB/C0B", message_id="1.0", emoji="+1", label="thumbs_up", previous=())
    run(b._react(item, Box()))
    run(b._react(SimpleNamespace(**{**vars(item), "chat_id": "T0ZZZ/C0Z"}), Box()))
    assert wb.reacted == [("C0B", "1.0", "+1")] and not wa.reacted
    assert results == [None, "T0ZZZ is not a connected workspace"]


def test_one_workspace_failing_to_connect_lets_the_other_receive_and_send(tmp_path):
    """A's token is refused on every try; the outbox is consumed once B is
    up, and B receives and sends while A keeps trying."""

    @dataclass
    class Send:
        action: broker.Action
        key: str

    got = []
    a = Wire("T0AAA", fails=slack_wire.Refused("invalid_auth"))
    w = Wire("T0BBB", "UVALORB")
    k = kernel(("T0BBB/C0B",), got)
    k.Release = Send
    b = SlackBridge([a, w], k, seen=tmp_path / "seen.json")

    class Outbox:
        def __aiter__(self):
            return self._items()

        async def _items(self):
            yield Send(release("T0BBB/C0B"), "k1")
            await w.on_message("E1", msg(channel="C0B"))
            await asyncio.sleep(0.05)

        async def perform(self, item):
            perform, _ = b.performers()[item.action.action_type]
            await perform(item.action, item.key)

    run(asyncio.wait_for(b.run(Outbox()), 5))
    assert a.calls.count("connect") >= 2 and not a.user_id
    assert w.posted == [("C0B", "hello")] and [m.chat_id for m in got] == ["T0BBB/C0B"]


def test_the_outbox_first_sees_every_workspace_after_its_first_try(tmp_path):
    """The Captain's first update after the second workspace came in named
    it not connected for a whole tick (2026-11-03): consuming began once the
    first workspace was up, before the second's connect returned. Now the
    first round waits for each workspace's first try; a refused one does
    not hold it."""

    class Slow(Wire):
        async def connect(self, on_message, on_hello):
            await asyncio.sleep(0.05)
            await super().connect(on_message, on_hello)

    seen = []
    a, slow, refused = Wire("T0AAA"), Slow("T0BBB"), Wire("T0CCC", fails=slack_wire.Refused("invalid_auth"))
    b = SlackBridge([a, slow, refused], kernel(), seen=tmp_path / "seen.json")

    class Outbox:
        def __aiter__(self):
            return self._items()

        async def _items(self):
            seen.append(await b.connected())
            return
            yield

    run(asyncio.wait_for(b.run(Outbox()), 5))
    assert seen == [{"T0AAA": True, "T0BBB": True, "T0CCC": False}]
