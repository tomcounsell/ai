"""Behaviors of the local chat bridge the main tests leave open: what `/send`
accepts, what `/log` leaves out, a bare `approve`, the launchd job, and the
static routes. Real bridge server, test database; ports of 6540 to 6549, or of
`VALOR_TEST_PORTS` when set."""

import asyncio
import os
import plistlib
import socket
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
import pytest

from bridges import local
from bridges.local import __main__ as local_main
from core import bridge as port
from core import db, intake, notices
from tests import bridges
from tests.bridges import new_task, of_type
from tests.ports import listen
from tests.ports import span as ports_span
from tests.telegram_port import until
from tests.test_local_bridge import Page, bind, local_received, run

pytestmark = pytest.mark.spend(usd=0)

PORTS = ports_span((6540, 6549))  # VALOR_TEST_PORTS when set


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
    with bridges.operator(tmp_path), bridges.configure(operator_channel="local", operator_chat="local"):
        (tmp_path / "projects" / "valor.toml").write_text(
            'name = "valor"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
        )
        yield tmp_path


@asynccontextmanager
async def serving(dsn, tmp_path):
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


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"text": "no id"},
        {"id": "", "text": "empty id"},
        {"id": "a", "text": 7},
        {"id": "b"},
        {"id": "c", "text": "x", "reply_to": 5},
    ],
)
def test_send_refuses_a_body_that_is_not_id_text_reply_to_and_records_nothing(dsn, op, body):
    async def go():
        async with serving(dsn, op) as page:
            before = len(await local_received(dsn))
            h = {local.TOKEN_HEADER: page.token}
            async with page.http.post(f"{page.base}/send", json=body, headers=h) as r:
                status = r.status
            async with page.http.post(f"{page.base}/send", data=b"not json{", headers=h) as r:
                garbled = r.status
            return status, garbled, len(await local_received(dsn)) - before

    status, garbled, recorded = run(go())
    assert (status, garbled, recorded) == (400, 400, 0)


def test_the_page_and_its_script_are_served_without_the_token(dsn, op):
    async def go():
        async with serving(dsn, op) as page:
            async with page.http.get(f"{page.base}/") as r:
                html = (r.status, r.content_type, await r.text())
            async with page.http.get(f"{page.base}/chat.js") as r:
                js = (r.status, r.content_type, await r.text())
            vendored = {}
            for name in local.VENDORED:
                async with page.http.get(f"{page.base}/vendor/{name}") as r:
                    vendored[name] = (r.status, r.content_type, await r.read())
            missing = []
            for path in (
                "/vendor/other.js",
                "/vendor/../__init__.py",
                "/vendor/%2e%2e/__init__.py",
                "/vendor/",
            ):
                async with page.http.get(f"{page.base}{path}") as r:
                    missing.append(r.status)
            return html, js, vendored, missing

    (hs, ht, html), (js_s, js_t, js), vendored, missing = run(go())
    assert (hs, ht, js_s) == (200, "text/html", 200) and "javascript" in js_t
    assert "X-Valor-Token" in js
    tags = [
        html.index(f'<script src="{src}">')
        for src in ("/vendor/marked.umd.js", "/vendor/purify.min.js", "/chat.js")
    ]
    assert tags == sorted(tags)
    assert set(vendored) == {"marked.umd.js", "purify.min.js"}
    for name, (status, kind, body) in vendored.items():
        assert (status, kind) == (200, "text/javascript")
        assert body == (Path(local.__file__).parent / "vendor" / name).read_bytes()
    assert missing == [404] * 4


def test_the_log_shows_only_what_was_sent_on_the_local_chat(dsn, op):
    """A notice sent on Telegram, and a notice requested but not yet sent,
    are not on the page."""

    async def go():
        task = await new_task(dsn)
        async with serving(dsn, op) as page:
            async with await db.connect(dsn) as conn:
                tg = await notices.request(
                    conn, task, kind="test", about_key=f"tg:{task}", text="on telegram", channel="telegram"
                )
                shown = await notices.request(
                    conn,
                    task,
                    kind="test",
                    about_key=f"lc:{task}",
                    text="on the page",
                    channel="local",
                    chat_id="local",
                )
            await page.row(shown)
            await asyncio.sleep(0.3)
            return tg, shown, await page.log()

    tg, _shown, log = run(go())
    texts = [r["text"] for r in log]
    assert any("on the page" in t for t in texts) and not any("on telegram" in t for t in texts)
    assert tg not in [r["message_id"] for r in log]


def test_a_bare_approve_that_replies_to_nothing_starts_a_task(dsn, op):
    async def go():
        async with serving(dsn, op) as page:
            return await bind(dsn, await page.post("approve"))

    bound = run(go())
    assert bound["as"] == "start"


def test_a_reply_to_an_unknown_id_is_recorded_not_rejected(dsn, op):
    async def go():
        async with serving(dsn, op) as page:
            posted = await page.post("hello?", reply_to="nope-" + uuid.uuid4().hex)
            async with await db.connect(dsn) as conn:
                await intake.bind(conn)
            return posted, await of_type(dsn, "message.bound", received_id=posted["received_id"])

    posted, (bound,) = run(go())
    assert posted["duplicate"] is False and bound["as"] in ("start", "none")


def test_the_launchd_job_runs_the_local_bridge_with_its_environment(monkeypatch):
    monkeypatch.setenv("VALOR_LOCAL_PORT", "8799")
    monkeypatch.setenv("VALOR_OPERATOR_CHANNEL", "local")
    monkeypatch.setenv("VALOR_SECRET_SOMETHING", "must-not-appear")
    job = plistlib.loads(local_main.plist(python="/usr/bin/python3"))
    assert job["Label"] == "com.valor.kernel.local"
    assert job["ProgramArguments"] == ["/usr/bin/python3", "-m", "bridges.local", "run"]
    assert job["KeepAlive"] is True and job["RunAtLoad"] is True
    assert job["EnvironmentVariables"]["VALOR_LOCAL_PORT"] == "8799"
    assert job["EnvironmentVariables"]["VALOR_OPERATOR_CHANNEL"] == "local"
    assert "VALOR_SECRET_SOMETHING" not in job["EnvironmentVariables"]
    assert job["StandardOutPath"].endswith("local.log")


def test_the_bridge_listens_on_loopback_only(dsn, op):
    async def go():
        async with serving(dsn, op) as page:
            port_ = int(page.base.rsplit(":", 1)[1])
            # The address this Mac uses to reach the network (no packet is sent).
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                try:
                    probe.connect(("192.0.2.1", 9))
                except OSError:
                    pytest.skip("this Mac has no network address but loopback")
                mine = probe.getsockname()[0]
            if mine.startswith("127."):
                pytest.skip("this Mac has no network address but loopback")
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(mine, port_), 2)
                w.close()
                return [mine]
            except TimeoutError, OSError:
                return []

    assert run(go()) == []
