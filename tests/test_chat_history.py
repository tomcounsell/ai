"""A turn reads and searches a chat's history through its running bridge:
`core.bridge.reads` over each bridge's `read`, `valor-telegram read` and
`valor-slack read` (`tools/valor_read.py`), and the per-turn install and
`VALOR_CHAT` (`core/__main__.py` `_turn_for`)."""

import asyncio
import json
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridges.slack import __main__ as slack_main
from bridges.slack.bridge import SlackBridge
from bridges.slack.wire import Refused as SlackRefused
from bridges.telegram import gap
from bridges.telegram.bridge import TelegramBridge
from core import bridge, workspace
from tests import bridges
from tests.telegram_emulator import EmulatorWire
from tools import valor_read

pytestmark = pytest.mark.spend(usd=0)

GROUP = -1_000_000_900_001


def get(port: int, **query) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}/history?" + "&".join(f"{k}={v}" for k, v in query.items())
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


async def served(read):
    runner = await bridge.reads(read, 0)
    return runner, runner.addresses[0][1]


# -- Telegram, over the emulator ---------------------------------------------


async def telegram(emu):
    wire = EmulatorWire(emu.url)
    await wire.connect()
    runner, port = await served(TelegramBridge(wire, SimpleNamespace(split_text=None)).read)
    return wire, runner, port


def test_telegram_latest_newest_first_with_sender_time_id_and_reply(emu):
    emu.control(chats=[{"id": GROUP, "kind": "supergroup"}])
    first = emu.inject(GROUP, "the deploy plan", sender_id=7, sender_name="Tom", live=False)
    emu.inject(GROUP, "", service=True, live=False)
    emu.call("inject", chat=GROUP, text="", live=False, media={"kind": "photo", "name": "a.jpg"})
    mine = emu.call("inject", chat=GROUP, text="on it", out=True, sender_id=1000, reply_to=first, live=False)

    async def go():
        wire, runner, port = await telegram(emu)
        try:
            status, out = await asyncio.to_thread(get, port, chat=GROUP)
        finally:
            await runner.cleanup()
            await wire.close()
        assert status == 200
        got = out["messages"]
        assert [m["id"] for m in got] == [mine["id"], first + 2, first]  # the service message is left out
        assert got[0] | {"sent_at": None} == {
            "id": mine["id"], "sent_at": None, "sender_id": 1000, "sender_name": "Someone",
            "out": True, "reply_to": first, "text": "on it", "media": None,
        }  # fmt: skip
        assert got[1]["media"] == {"kind": "photo", "name": "a.jpg"}
        assert got[2]["sender_name"] == "Tom" and got[2]["sent_at"].endswith("+00:00")

    asyncio.run(go())


def test_telegram_search_paging_limit_and_empty_chat(emu):
    emu.control(chats=[{"id": GROUP, "kind": "supergroup"}, {"id": GROUP - 1, "kind": "supergroup"}])
    ids = [emu.inject(GROUP, f"note {i}" + (" Deploy" if i % 2 else ""), live=False) for i in range(105)]

    async def go():
        wire, runner, port = await telegram(emu)
        try:
            return [
                await asyncio.to_thread(get, port, **q)
                for q in (
                    {"chat": GROUP, "q": "deploy", "limit": 3},
                    {"chat": GROUP, "before": ids[10], "limit": 5},
                    {"chat": GROUP, "limit": 500},
                    {"chat": GROUP - 1},
                )
            ]
        finally:
            await runner.cleanup()
            await wire.close()

    (s1, found), (s2, older), (s3, clamped), (s4, empty) = asyncio.run(go())
    assert [m["id"] for m in found["messages"]] == [ids[103], ids[101], ids[99]]
    assert [m["id"] for m in older["messages"]] == ids[5:10][::-1]
    assert len(clamped["messages"]) == 100
    assert empty == {"messages": []} and {s1, s2, s3, s4} == {200}


def test_telegram_disconnected_is_503_and_bad_chat_400(emu):
    async def go():
        wire, runner, port = await telegram(emu)
        try:
            await wire.disconnect()
            down = await asyncio.to_thread(get, port, chat=GROUP)
            bad = await asyncio.to_thread(get, port, chat="the-team")
            none = await asyncio.to_thread(get, port, q="x")
        finally:
            await runner.cleanup()
            await wire.close()
        return down, bad, none

    down, bad, none = asyncio.run(go())
    assert down == (503, {"error": "not connected"})
    assert bad == (400, {"error": "a Telegram chat and message are named by number"})
    assert none[0] == 400


def test_gap_fill_reads_history_without_a_search():
    calls = []

    class Wire:
        async def history(self, chat_id, **kw):
            calls.append(kw)
            return []

    async def recorded(ids):
        return set()

    asyncio.run(gap.missing(Wire(), GROUP, stop_id=0, recorded=recorded))
    assert calls == [{"offset_id": 0, "limit": gap.PAGE}]


# -- Slack, over a fake wire --------------------------------------------------


class SlackFake:
    user_id = "UVALOR"

    def __init__(self, matches=(), refuse=None):
        self.calls = []
        self.matches = list(matches)
        self.refuse = refuse

    async def open_im(self, user):
        return "D0TOM"

    async def user_name(self, user):
        return {"UTOM": "Tom Counsell", "UVALOR": "Valor Engels"}.get(user, "")

    async def history(self, channel, *, latest=None, limit):
        self.calls.append(("history", channel, latest, limit))
        return [
            {"ts": "1792000002.000200", "user": "UVALOR", "text": "done", "thread_ts": "1792000001.000100"},
            {"ts": "1792000001.000100", "user": "UTOM", "text": "ship it", "files": [{"name": "spec.pdf"}]},
        ], None

    async def search(self, query, *, count):
        self.calls.append(("search", query, count))
        if self.refuse:
            raise SlackRefused(self.refuse)
        return self.matches


def slack_read(fake, **query):
    async def go():
        runner, port = await served(SlackBridge(fake, SimpleNamespace(split_text=None)).read)
        try:
            return await asyncio.to_thread(get, port, **query)
        finally:
            await runner.cleanup()

    return asyncio.run(go())


def test_slack_history_shape_and_before_pages_strictly_older():
    fake = SlackFake()
    status, out = slack_read(fake, chat="T0ABC/C0TEAM", before="1792000009.000000", limit=2)
    assert status == 200 and fake.calls == [("history", "C0TEAM", "1792000009.000000", 2)]
    reply, root = out["messages"]
    assert reply == {
        "id": "1792000002.000200", "sent_at": "2026-10-14T17:46:42.000200+00:00", "sender_id": "UVALOR",
        "sender_name": "Valor Engels", "out": True, "reply_to": "1792000001.000100", "text": "done",
        "media": None,
    }  # fmt: skip
    assert root["reply_to"] is None and root["media"] == {"kind": "document", "name": "spec.pdf"}
    assert root["sender_name"] == "Tom Counsell" and root["out"] is False


def test_slack_search_in_the_channel_only_and_in_a_dm():
    link = "https://x.slack.com/archives/C0TEAM/p1792000003000300?thread_ts=1792000001.000100"
    matches = [
        {"ts": "1792000003.000300", "user": "UTOM", "text": "the deploy", "channel": {"id": "C0TEAM"}, "permalink": link},
        {"ts": "1792000004.000400", "user": "UTOM", "text": "deploy elsewhere", "channel": {"id": "C0OTHER"}},
    ]  # fmt: skip
    fake = SlackFake(matches)
    status, out = slack_read(fake, chat="T0ABC/C0TEAM", q="deploy", limit=20)
    assert status == 200 and fake.calls == [("search", "deploy in:<#C0TEAM>", 20)]
    assert [(m["id"], m["reply_to"]) for m in out["messages"]] == [("1792000003.000300", "1792000001.000100")]

    dm = SlackFake([{**matches[0], "channel": {"id": "D0TOM"}}])
    status, out = slack_read(dm, chat="T0ABC/UTOM", q="deploy")
    assert dm.calls == [("search", "deploy in:<@UTOM>", 50)] and len(out["messages"]) == 1


def test_slack_search_without_scope_is_503_and_before_with_search_400():
    assert slack_read(SlackFake(refuse="missing_scope"), chat="T0ABC/C0TEAM", q="x") == (
        503,
        {"error": "missing_scope"},
    )
    status, out = slack_read(SlackFake(), chat="T0ABC/C0TEAM", q="x", before="1.0")
    assert (status, out) == (400, {"error": "before does not apply to a Slack search"})
    assert "search:read" in slack_main.manifest()["oauth_config"]["scopes"]["user"]


# -- the command --------------------------------------------------------------


class Stub:
    """A read endpoint on a free port in a thread of its own, answering
    one Telegram chat's two messages."""

    def __init__(self):
        self.asked = []
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def read(chat, q, limit, before):
            self.asked.append((chat, q, limit, before))
            return [
                {"id": 12, "sent_at": "2026-10-16T10:05:00+00:00", "sender_name": "Valor", "out": True,
                 "reply_to": 11, "text": "yes", "media": None},
                {"id": 11, "sent_at": "2026-10-16T10:00:00+00:00", "sender_name": "Tom", "out": False,
                 "reply_to": None, "text": "can we ship\nthis?", "media": {"kind": "photo", "name": "x.png"}},
            ]  # fmt: skip

        async def start():
            self.runner, self.port = await served(read)
            ready.set()

        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(start(), self.loop)
        ready.wait(10)

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.stop()


def test_cli_prints_oldest_first_for_the_turns_chat(stub, monkeypatch, capsys):
    monkeypatch.setenv("VALOR_TELEGRAM_READ_PORT", str(stub.port))
    monkeypatch.setenv("VALOR_CHAT", "telegram:-1003890616618")
    assert valor_read.main(["read", "--search", "ship", "-n", "5"], "/x/bin/valor-telegram") == 0
    assert stub.asked == [("-1003890616618", "ship", 5, None)]
    assert capsys.readouterr().out.splitlines() == [
        "[2026-10-16 10:00 UTC] Tom (message 11): can we ship",
        "this? [photo: x.png]",
        "[2026-10-16 10:05 UTC] Valor (you) (message 12, replying to 11): yes",
    ]


def test_cli_names_what_is_missing(monkeypatch, capsys):
    monkeypatch.setenv("VALOR_CHAT", "slack:T0ABC/C0TEAM")
    assert valor_read.main(["read"], "valor-telegram") == 2
    assert "name the chat; this turn's own chat is slack:T0ABC/C0TEAM" in capsys.readouterr().err
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("VALOR_TELEGRAM_READ_PORT", str(port))
    assert valor_read.main(["read", "-100"], "valor-telegram") == 1
    assert f"the telegram bridge is not answering on 127.0.0.1:{port}" in capsys.readouterr().err


def test_cli_runs_on_the_turns_python3(stub, tmp_path):
    """As installed, under the `python3` a turn's PATH finds (3.9 on macOS)."""
    workspace.install_tools(tmp_path)
    out = subprocess.run(
        [shutil.which("python3"), str(tmp_path / "valor-telegram"), "read", "-100", "--json"],
        env={"VALOR_TELEGRAM_READ_PORT": str(stub.port), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert [m["id"] for m in json.loads(out.stdout)] == [12, 11]


# -- every turn gets the tools and its chat ----------------------------------


def turn_env(tmp_path, **brief):
    from core import __main__, tasks

    profile = workspace.check_harness(workspace.Layout(tmp_path), workspace.fresh_dir(tmp_path / "c"), [], {})
    b = tasks.Brief(instruction="x", harness={"sandbox_profile": profile["sandbox_profile"]}, **brief)
    return __main__._turn_for("p", None, b)("http://h:1", "b", "t").env


def test_each_turn_installs_the_tools_and_names_its_chat(tmp_path, monkeypatch):
    # A turn's own chat (the suite run inside a task) is no chat of these.
    monkeypatch.delenv("VALOR_CHAT", raising=False)
    (tmp_path / "bin").mkdir()
    mirror = str(tmp_path / "t" / "kernel.git")
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "flowers.toml").write_text(
        bridges.VALOR_SPEC.replace("valor", "flowers") + 'chats = ["slack:T0ABC/C0F", "telegram:-1005"]\n'
    )
    (projects / "bare.toml").write_text(bridges.VALOR_SPEC.replace("valor", "bare"))
    with bridges.configure(projects_dir=str(projects), telegram_read_port=9101):
        session = turn_env(tmp_path, mirror=mirror, chat={"channel": "telegram", "chat_id": "-1009"})
        task = turn_env(tmp_path, mirror=mirror, project={"name": "flowers"})
        bare = turn_env(tmp_path, mirror=mirror, project={"name": "bare"})
        unprovisioned = turn_env(tmp_path, project={"name": "flowers"})
    assert session["VALOR_CHAT"] == "telegram:-1009" and session["VALOR_TELEGRAM_READ_PORT"] == "9101"
    assert task["VALOR_CHAT"] == "slack:T0ABC/C0F"
    assert "VALOR_CHAT" not in bare and "VALOR_CHAT" not in unprovisioned
    for name in workspace.WORKSPACE_TOOLS:
        assert (tmp_path / "bin" / name).stat().st_mode & 0o111
    assert sorted(p.name for p in (tmp_path / "bin").iterdir()) == sorted(workspace.WORKSPACE_TOOLS)


def test_two_installs_at_once_both_succeed(tmp_path):
    errors = []

    def install():
        try:
            for _ in range(20):
                workspace.install_tools(tmp_path)
        except Exception as e:  # noqa: BLE001  collected for the assertion
            errors.append(e)

    threads = [threading.Thread(target=install) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    source = Path(workspace.TOOLS_DIR / "valor_read.py").read_bytes()
    assert (tmp_path / "valor-slack").read_bytes() == source
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(workspace.WORKSPACE_TOOLS)
