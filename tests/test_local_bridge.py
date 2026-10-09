"""The local chat bridge over its real HTTP server and the test database.

Each test runs the bridge through `core.bridge.serve` on a port of
6530 to 6539 (of `VALOR_TEST_PORTS` when set), with its token file in the test's directory, and talks to it
as the page does: `/log` and `/send` with the token header. The kernel's
binding is `intake.bind`, called after each post."""

import asyncio
import os
import socket
import stat
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import aiohttp
import pytest

from bridges import local
from bridges.local import __main__ as local_main
from core import bridge as port
from core import broker, db, intake, machine, notices, tasks
from core.machine import State
from core.settings import Settings
from tests import bridges
from tests.bridges import OPERATOR_CHAT, OPERATOR_EMAIL, new_task, of_type, rows
from tests.ports import listen
from tests.ports import span as ports_span
from tests.telegram_port import until
from tests.test_intake import a_send, msg, say

pytestmark = pytest.mark.spend(usd=0)

SEND = "local.send_message"
PORTS = ports_span((6530, 6539))  # VALOR_TEST_PORTS when set


def run(coro):
    return asyncio.run(coro)


def free_port() -> int:
    if os.environ.get("VALOR_TEST_PORTS"):
        return listen()
    for p in range(PORTS[0], PORTS[1] + 1):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return p
    raise AssertionError(f"no free port in {PORTS[0]}-{PORTS[1]}")


@pytest.fixture
def op(tmp_path):
    """This machine with the local chat as the operator channel, and a
    `valor` project spec for a started task to fall under."""
    with bridges.operator(tmp_path), bridges.configure(operator_channel="local", operator_chat="local"):
        (tmp_path / "projects" / "valor.toml").write_text(
            'name = "valor"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
        )
        yield tmp_path


class Page:
    """What the page does, over HTTP."""

    def __init__(self, http: aiohttp.ClientSession, base: str, token: str):
        self.http, self.base, self.token = http, base, token

    async def log(self) -> list[dict]:
        async with self.http.get(f"{self.base}/log", headers={local.TOKEN_HEADER: self.token}) as r:
            assert r.status == 200
            return (await r.json())["rows"]

    async def post(self, text: str, reply_to: str | None = None, id: str | None = None) -> dict:
        body = {"id": id or uuid.uuid4().hex, "text": text, "reply_to": reply_to}
        async with self.http.post(
            f"{self.base}/send", json=body, headers={local.TOKEN_HEADER: self.token}
        ) as r:
            assert r.status == 200
            return await r.json()

    async def row(self, message_id: str) -> dict:
        """The `/log` row with this message id, once it is there."""
        found: list[dict] = []

        async def there():
            found[:] = [r for r in await self.log() if r["message_id"] == message_id]
            return found

        await until(there)
        (row,) = found
        return row


@asynccontextmanager
async def serving(dsn, tmp_path):
    """The bridge run as launchd runs it, through `serve`."""
    tokenfile = tmp_path / "local-token"
    p = free_port()
    served = asyncio.create_task(port.serve(local.LocalBridge(dsn, p, str(tokenfile)), dsn))

    async def up():
        assert not served.done(), served
        try:
            _, w = await asyncio.open_connection("127.0.0.1", p)
        except OSError:
            return False
        w.close()
        return True

    try:
        await until(up, timeout=15)
        async with aiohttp.ClientSession() as http:
            yield Page(http, f"http://127.0.0.1:{p}", tokenfile.read_text().strip())
    finally:
        served.cancel()
        with pytest.raises(asyncio.CancelledError):
            await served


async def bind(dsn, posted: dict) -> dict:
    async with await db.connect(dsn) as conn:
        await intake.bind(conn)
    (bound,) = await of_type(dsn, "message.bound", received_id=posted["received_id"])
    return bound


async def local_received(dsn) -> list[dict]:
    return await of_type(dsn, "message.received", channel="local")


async def held_send(dsn, text: str) -> tuple[str, str]:
    task = await new_task(dsn)
    async with await db.connect(dsn) as conn:
        held = await broker.request(
            conn, bridges.declared(), task, broker.Action(SEND, "local", {"text": text})
        )
    assert held.kind == "released", held
    return task, held.effect_id


def test_a_posted_message_is_recorded_once_and_starts_a_task_under_valor(dsn, op):
    async def go():
        async with serving(dsn, op) as page:
            mid = uuid.uuid4().hex
            first = await page.post("write a haiku", id=mid)
            again = await page.post("write a haiku", id=mid)
            assert again == {**first, "duplicate": True} and first["duplicate"] is False
            (got,) = [r for r in await local_received(dsn) if r["message_id"] == mid]
            bound = await bind(dsn, first)
            async with await db.connect(dsn) as conn:
                project = (await tasks.brief(conn, bound["task_id"])).project
            return got, bound, project

    got, bound, project = run(go())
    assert got["task_id"] == "local" and got["verified"] is True
    assert {k: got[k] for k in ("chat_id", "chat_kind", "sender_id", "sender_name", "reply_to", "text")} == {
        "chat_id": "local",
        "chat_kind": "dm",
        "sender_id": "local",
        "sender_name": "Tom",
        "reply_to": None,
        "text": "write a haiku",
    }
    assert datetime.fromisoformat(got["sent_at"]).utcoffset().total_seconds() == 0
    assert bound["as"] == "start" and project == {"name": "valor"}


def test_a_requested_send_is_shown_once_with_no_report(dsn, op):
    words = "<img src=x onerror=alert(1)>"

    async def go():
        _, effect = await held_send(dsn, words)
        async with serving(dsn, op) as page:
            shown = await page.row(effect)
            await asyncio.sleep(0.5)  # another wake of the outbox shows nothing more
            log = await page.log()
        return effect, shown, log

    effect, shown, log = run(go())
    assert shown["text"] == words and shown["from"] == "valor"
    assert [r["text"] for r in log].count(words) == 1
    # A send to Tom's own page is itself the message: it owes no report.
    assert not run(of_type(dsn, "notice.requested", about_key=f"report:{effect}"))
    (released,) = run(of_type(dsn, "release.requested", effect_id=effect))
    assert released["owner"] == "local"
    (outcome,) = run(of_type(dsn, "effect.outcome", effect_id=effect))
    assert outcome["kind"] == "done"
    assert outcome["result"] == {"sent": [{"channel": "local", "chat_id": "local", "message_id": effect}]}


def test_stop_by_reply_and_a_near_miss_steers_with_a_notice_in_reply(dsn, op):
    async def go():
        task = await new_task(dsn)
        async with serving(dsn, op) as page:
            async with await db.connect(dsn) as conn:
                nid = await notices.request(conn, task, kind="test", about_key=f"t:{task}", text="about it")
            notice = await page.row(nid)

            near_post = await page.post("Stop!", reply_to=notice["message_id"])
            near = await bind(dsn, near_post)
            (said,) = [
                n
                for n in await of_type(dsn, "notice.requested")
                if n["about_key"] == f"reply:{near['received_id']}"
            ]
            reply = await page.row(said["notice_id"])

            stop = await bind(dsn, await page.post("stop", reply_to=notice["message_id"]))
        return task, near, said, reply, near_post, stop

    task, near, said, reply, near_post, stop = run(go())
    assert near["as"] == "steer"
    assert said["channel"] == "local" and said["chat_id"] == "local" and "Not a stop" in said["text"]
    (posted,) = [r for r in run(local_received(dsn)) if r["received_id"] == near_post["received_id"]]
    assert said["reply_to"] == reply["reply_to"] == posted["message_id"]
    assert stop["as"] == "stop"
    assert machine.fold(run(rows(dsn, task))).state is State.STOPPED


def test_without_the_token_header_nothing_is_read_or_recorded(dsn, op):
    async def go():
        async with serving(dsn, op) as page:
            before = len(await local_received(dsn))
            body = {"id": uuid.uuid4().hex, "text": "forged", "reply_to": None}
            statuses = []
            for headers, query in (
                ({}, ""),
                ({local.TOKEN_HEADER: "wrong"}, ""),
                ({}, f"?token={page.token}"),
            ):
                async with page.http.get(f"{page.base}/log{query}", headers=headers) as r:
                    statuses.append(r.status)
                async with page.http.post(f"{page.base}/send{query}", json=body, headers=headers) as r:
                    statuses.append(r.status)
            after = len(await local_received(dsn))
            async with page.http.options(
                f"{page.base}/send",
                headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
            ) as r:
                preflight = dict(r.headers)
            async with page.http.get(f"{page.base}/") as r:
                framing = r.headers.get("Content-Security-Policy")
        return statuses, before, after, preflight, framing

    statuses, before, after, preflight, framing = run(go())
    assert statuses == [401] * 6 and before == after
    assert not [h for h in preflight if h.lower().startswith("access-control-allow-")]
    assert framing == "frame-ancestors 'none'; script-src 'self'"


class Killed(BaseException):
    """The bridge dies between the intent and the outcome."""


class Dies(local.LocalBridge):
    async def perform(self, action, key):
        raise Killed


def test_killed_between_intent_and_outcome_the_restart_reconciles_done_and_shows_it_once(dsn, op):
    async def go():
        _, effect = await held_send(dsn, "survives a crash")
        async with bridges.outbox(dsn, Dies(dsn)) as box:
            (item,) = [i for i in await box.due() if getattr(i, "effect_id", None) == effect]
            with pytest.raises(Killed):
                await box.perform(item)
        assert await of_type(dsn, "effect.intent", effect_id=effect)
        assert not await of_type(dsn, "effect.outcome", effect_id=effect)
        async with serving(dsn, op) as page:
            shown = await page.row(effect)
            log = await page.log()
        return effect, shown, log

    effect, shown, log = run(go())
    (outcome,) = run(of_type(dsn, "effect.outcome", effect_id=effect))
    assert outcome["kind"] == "done" and outcome["reconciled"] is True
    assert outcome["result"]["sent"] == [{"channel": "local", "chat_id": "local", "message_id": effect}]
    assert shown["text"] == "survives a crash" and [r["message_id"] for r in log].count(effect) == 1


def test_the_log_returns_a_row_whose_id_committed_after_a_later_one(dsn, op):
    async def go():
        async with serving(dsn, op) as page, await db.connect(dsn) as early:
            mid = uuid.uuid4().hex
            async with early.transaction():
                inbound = intake.Inbound(
                    channel="local",
                    chat_id="local",
                    chat_kind="dm",
                    message_id=mid,
                    sender_id="local",
                    sender_name="Tom",
                    sent_at="2026-10-06T00:00:00+00:00",
                    text="committed last",
                )
                await intake.receive(early, inbound)
                later = await page.post("committed first")
                (later_row,) = [
                    r for r in await local_received(dsn) if r["received_id"] == later["received_id"]
                ]
                seen = [r["message_id"] for r in await page.log()]
                assert later_row["message_id"] in seen and mid not in seen
            log = await page.log()
        return mid, later_row["message_id"], log

    mid, later, log = run(go())
    ids = [r["message_id"] for r in log]
    assert mid in ids and later in ids
    assert ids.index(mid) < ids.index(later)


@pytest.mark.parametrize(
    ("target", "payload", "said"),
    [
        ("-1001234", {"text": "hi"}, "not the local chat"),
        ("local", {"text": 7}, "must be a string"),
        ("local", {"text": "hi", "files": [{"path": "/x", "sha256": "0" * 64}]}, "text only"),
        ("local", {"text": "hi"}, None),
    ],
)
def test_local_send_message_refusals(op, target, payload, said):
    got = run(port.DECLARED[SEND].refuse(None, broker.Action(SEND, target, payload)))
    assert (got is None) if said is None else (said in got)


def test_ownership_follows_the_operator_channel(tmp_path):
    with bridges.operator(tmp_path):
        assert intake.owns("telegram", OPERATOR_CHAT) and not intake.owns("local", "local")
        with bridges.configure(operator_channel="local", operator_chat="local"):
            assert intake.owns("local", "local")
            assert not intake.owns("telegram", OPERATOR_CHAT) and not intake.owns("telegram", "local")


@pytest.mark.parametrize(
    ("channel", "chat", "expected"),
    [("local", "-1001", "local"), ("telegram", "-1001", "-1001"), ("telegram", None, None)],
)
def test_the_operator_chat_is_local_whenever_the_operator_channel_is(monkeypatch, channel, chat, expected):
    monkeypatch.setenv("VALOR_OPERATOR_CHANNEL", channel)
    if chat is None:
        monkeypatch.delenv("VALOR_OPERATOR_CHAT", raising=False)
    else:
        monkeypatch.setenv("VALOR_OPERATOR_CHAT", chat)
    assert Settings().operator_chat == expected


def test_an_email_binding_notice_goes_to_and_names_the_operator_channel(dsn, op, monkeypatch):
    monkeypatch.setitem(intake.VERIFY, "email", lambda inbound: True)

    async def go():
        task = await new_task(dsn)
        mailed = await a_send(dsn, task, "email", "thread-local")
        bound = await say(
            dsn,
            msg("Stop please", reply_to=mailed, channel="email", sender=OPERATOR_EMAIL, chat="thread-local"),
        )
        said = [
            n
            for n in await of_type(dsn, "notice.requested")
            if n["about_key"] == f"reply:{bound['received_id']}"
        ]
        return bound, said

    bound, said = run(go())
    assert bound["as"] == "steer"
    (notice,) = said
    assert notice["channel"] == "local" and notice["chat_id"] == "local" and notice["reply_to"] is None
    assert "by reply on the local chat page" in notice["text"]


def test_run_makes_the_token_mode_600_and_keeps_one_that_exists(dsn, op):
    made = op / "local-token"

    async def go():
        async with serving(dsn, op) as page:
            return page.token

    token = run(go())
    assert stat.S_IMODE(os.stat(made).st_mode) == 0o600 and len(token) >= 32
    os.chmod(made, 0o600)
    made.write_text("kept\n")
    assert run(go()) == "kept" and made.read_text() == "kept\n"


def test_a_crash_while_making_the_token_leaves_no_token_file(tmp_path, monkeypatch):
    """The token reaches its name whole or not at all: a crash before the
    link leaves no `local-token`, and the next start makes one."""
    made = tmp_path / "local-token"

    def crash(src, dst):
        assert Path(src).read_text().strip()
        raise KeyboardInterrupt

    monkeypatch.setattr(local.os, "link", crash)
    with pytest.raises(KeyboardInterrupt):
        local.ensure_token(made)
    assert not made.exists()
    monkeypatch.undo()
    token = local.ensure_token(made)
    assert len(token) >= 32 and made.read_text() == token + "\n"
    assert stat.S_IMODE(os.stat(made).st_mode) == 0o600
    assert local.ensure_token(made) == token
    assert sorted(p.name for p in tmp_path.iterdir()) == ["local-token"]


def test_open_with_no_token_file_writes_nothing(tmp_path, capsys):
    keys = tmp_path / "keys"
    keys.mkdir()
    with bridges.configure(pg_passfile=str(keys / "pgpass")):
        assert local_main.main(["open"]) == 1
    assert list(keys.iterdir()) == []
    assert "start the bridge first" in capsys.readouterr().err
