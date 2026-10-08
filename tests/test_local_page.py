"""The local chat page, run: `chat.js` in a real headless Chromium against the
real bridge server and the test database.

The browser is `settings.browser` when it exists, else Google Chrome's
own binary; the test skips when neither is there. The page is driven over
the Chrome DevTools protocol (no Playwright in the venv). Ports: the
bridge on a port of 6540 to 6549 (of `VALOR_TEST_PORTS` when set), the browser's debugging port on another."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
import pytest

from bridges import local
from core import bridge as port
from core import broker, db, notices
from core.settings import settings
from tests import bridges
from tests.bridges import new_task, of_type
from tests.ports import listen
from tests.ports import span as ports_span
from tests.telegram_port import until
from tests.test_local_bridge import SEND, bind, local_received, run

pytestmark = pytest.mark.spend(usd=0)

PORTS = ports_span((6540, 6549))  # VALOR_TEST_PORTS when set
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def browser() -> str | None:
    for path in (settings.browser, CHROME):
        if path and os.access(path, os.X_OK):
            return path
    return None


def free_ports(n: int) -> list[int]:
    if os.environ.get("VALOR_TEST_PORTS"):
        return [listen() for _ in range(n)]
    got = []
    for p in range(PORTS[0], PORTS[1] + 1):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            got.append(p)
        if len(got) == n:
            return got
    raise AssertionError(f"no free ports in {PORTS[0]}-{PORTS[1]}")


@pytest.fixture
def op(tmp_path):
    with bridges.operator(tmp_path), bridges.configure(operator_channel="local", operator_chat="local"):
        (tmp_path / "projects" / "valor.toml").write_text(
            'name = "valor"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
        )
        yield tmp_path


class Tab:
    """One page over the DevTools protocol."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse):
        self.ws, self.n = ws, 0

    async def call(self, method: str, **params):
        self.n += 1
        want = self.n
        await self.ws.send_json({"id": want, "method": method, "params": params})
        while True:
            msg = await asyncio.wait_for(self.ws.receive_json(), 15)
            if msg.get("id") == want:
                assert "error" not in msg, msg
                return msg["result"]

    async def js(self, expression: str):
        got = await self.call(
            "Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True
        )
        assert "exceptionDetails" not in got, got
        return got["result"].get("value")

    async def goto(self, url: str) -> None:
        await self.call("Page.navigate", url=url)
        await until(lambda: self.js("document.readyState === 'complete'"), timeout=10)

    async def wait(self, expression: str, timeout: float = 8.0) -> None:
        await until(lambda: self.js(expression), timeout=timeout)

    async def type_and_send(self, text: str) -> None:
        await self.js(f"document.getElementById('text').value = {json.dumps(text)}")
        await self.js("document.querySelector('button[type=submit]').click()")


@asynccontextmanager
async def page_up(dsn, tmp_path, *, hold_bridge=None):
    """The bridge run through `serve`, and a headless browser with one tab."""
    exe = browser()
    if exe is None:
        pytest.skip("no headless Chromium: neither settings.browser nor Google Chrome is installed")
    bridge_port, debug_port = free_ports(2)
    tokenfile = tmp_path / "local-token"
    bridge = local.LocalBridge(dsn, bridge_port, str(tokenfile))
    if hold_bridge is not None:
        hold_bridge.append(bridge)
    served = asyncio.create_task(port.serve(bridge, dsn))
    profile = tempfile.mkdtemp(prefix="valor-test-chrome-")
    chrome = None
    try:

        async def up():
            assert not served.done(), served
            try:
                _, w = await asyncio.open_connection("127.0.0.1", bridge_port)
            except OSError:
                return False
            w.close()
            return True

        await until(up, timeout=15)
        errors = Path(f"{profile}.err")
        with errors.open("w") as err:  # noqa: ASYNC230
            # The browser is the test's client for the page, not under test:
            # its own sandbox is off, as `look` runs it, since the sandbox a
            # check runs the suite under denies applying another, and its
            # socket directory is the suite's temp directory, since that
            # sandbox denies the user temp directory it uses by default.
            chrome = subprocess.Popen(  # noqa: ASYNC220
                [
                    exe,
                    "--headless=new" if exe == CHROME else "--headless",
                    "--no-sandbox",
                    f"--remote-debugging-port={debug_port}",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=err,
                env={**os.environ, "MAC_CHROMIUM_TMPDIR": tempfile.gettempdir()},
            )
        async with aiohttp.ClientSession() as http:
            ws_url = None

            async def listening():
                nonlocal ws_url
                if chrome.poll() is not None:
                    raise AssertionError(f"the browser exited {chrome.returncode}: {errors.read_text()}")
                try:
                    async with http.get(f"http://127.0.0.1:{debug_port}/json/list") as r:
                        tabs = [t for t in await r.json() if t.get("type") == "page"]
                except aiohttp.ClientError, OSError:
                    return False
                if tabs:
                    ws_url = tabs[0]["webSocketDebuggerUrl"]
                return bool(tabs)

            await until(listening, timeout=20)
            async with http.ws_connect(ws_url) as ws:
                tab = Tab(ws)
                await tab.call("Page.enable")
                await tab.call("Runtime.enable")
                yield tab, f"http://127.0.0.1:{bridge_port}", tokenfile.read_text().strip(), http
    finally:
        if chrome is not None:
            chrome.terminate()
            try:
                chrome.wait(10)
            except subprocess.TimeoutExpired:
                chrome.kill()
        shutil.rmtree(profile, ignore_errors=True)
        Path(f"{profile}.err").unlink(missing_ok=True)
        served.cancel()
        with pytest.raises(asyncio.CancelledError):
            await served


def test_the_page_sends_shows_replies_and_renders_text_as_text(dsn, op):
    words = "<img src=x onerror=window.pwned=1>"

    async def go():
        async with page_up(dsn, op) as (tab, base, token, _):
            await tab.goto(f"{base}/#{token}")
            assert await tab.js("document.title") == "Valor, local"

            # Send: the row appears, the box clears, the bridge recorded it once and verified.
            before = len(await local_received(dsn))
            await tab.type_and_send("hello from the page")
            await tab.wait(
                "[...document.querySelectorAll('#log li.tom')].some(l => l.textContent === 'hello from the page')"
            )
            await tab.wait(
                "document.getElementById('text').value === '' && !document.getElementById('text').disabled"
            )
            got = [r for r in await local_received(dsn) if r["text"] == "hello from the page"]
            assert len(got) == 1 and got[0]["verified"] is True and got[0]["reply_to"] is None
            assert len(await local_received(dsn)) == before + 1

            # Valor's notice arrives by the poll, and its text is not run.
            task = await new_task(dsn)
            async with await db.connect(dsn) as conn:
                held = await broker.request(
                    conn, bridges.declared(), task, broker.Action(SEND, "local", {"text": words})
                )
                (nid,) = await notices.owe(conn, task)
            await tab.wait("!!document.querySelector('#log li[data-event-id].valor')", timeout=10)
            mine = (
                "[...document.querySelectorAll('#log li.valor')].filter(l => l.textContent.includes("
                + json.dumps(held.effect_id)
                + "))[0]"
            )
            await tab.wait(f"!!{mine}", timeout=10)
            assert await tab.js("document.querySelectorAll('#log img').length") == 0

            # Click it: the next send is a reply to it, and only that one.
            await tab.js(f"{mine}.click()")
            assert await tab.js(f"{mine}.classList.contains('chosen')")
            assert "Replying to:" in await tab.js("document.getElementById('reply').textContent")
            await tab.type_and_send("approve")
            await tab.wait("document.getElementById('reply').textContent === ''")
            await tab.wait("!document.querySelector('#log li.chosen')")
            # Other tests post `approve` to the same session's ledger, so
            # only the replies to this notice count.
            replied = [
                r for r in await local_received(dsn) if r["text"] == "approve" and r["reply_to"] == nid
            ]
            assert len(replied) == 1
            bound = await bind(dsn, {"received_id": replied[0]["received_id"]})
            assert bound["as"] == "approve"

            # The approved text is shown, once, as text.
            await until(
                lambda: of_type(dsn, "effect.outcome", effect_id=held.effect_id),
                timeout=10,
            )
            await tab.wait(
                f"[...document.querySelectorAll('#log li.valor')].filter(l => l.textContent === {json.dumps(words)}).length === 1",
                timeout=10,
            )
            assert await tab.js("window.pwned === undefined")
            assert await tab.js("document.querySelectorAll('#log img').length") == 0

            # The next send is not a reply.
            await tab.type_and_send("and another")
            await tab.wait(
                "[...document.querySelectorAll('#log li.tom')].some(l => l.textContent === 'and another')"
            )
            (again,) = [r for r in await local_received(dsn) if r["text"] == "and another"]
            assert again["reply_to"] is None

            # Rows stay oldest first.
            ids = await tab.js(
                "[...document.querySelectorAll('#log li')].map(l => Number(l.dataset.eventId))"
            )
            assert ids == sorted(ids) and len(ids) == len(set(ids))

    run(go())


def test_a_401_stops_the_polling_and_the_sending(dsn, op):
    async def go():
        held: list = []
        async with page_up(dsn, op, hold_bridge=held) as (tab, base, token, _):
            await tab.goto(f"{base}/#{token}")
            await tab.type_and_send("before")
            await tab.wait(
                "[...document.querySelectorAll('#log li.tom')].some(l => l.textContent === 'before')"
            )
            assert await tab.js("document.getElementById('status').textContent") == ""

            # The bridge's token changes under the open page.
            held[0].token = "rotated-" + uuid.uuid4().hex
            await tab.wait(
                "document.getElementById('status').textContent.includes('python -m bridges.local open')",
                timeout=8,
            )
            assert await tab.js("document.getElementById('text').disabled")

            # No more polling: a new notice never shows, and a send is not made.
            sent_before = len(await local_received(dsn))
            task = await new_task(dsn)
            async with await db.connect(dsn) as conn:
                await notices.request(conn, task, kind="test", about_key=f"t:{task}", text="after the 401")
            await asyncio.sleep(5)
            assert not await tab.js(
                "[...document.querySelectorAll('#log li')].some(l => l.textContent === 'after the 401')"
            )
            await tab.js("document.getElementById('text').value = 'ignored'")
            await tab.js("document.getElementById('form').requestSubmit()")
            await asyncio.sleep(1)
            assert len(await local_received(dsn)) == sent_before

    run(go())


def test_a_page_opened_without_or_with_a_wrong_token_says_to_reopen(dsn, op):
    async def go():
        async with page_up(dsn, op) as (tab, base, _token, _):
            await tab.goto(f"{base}/")
            await tab.wait(
                "document.getElementById('status').textContent.includes('python -m bridges.local open')"
            )
            assert await tab.js("document.getElementById('text').disabled")
            await tab.goto(f"{base}/#wrong")
            await tab.wait(
                "document.getElementById('status').textContent.includes('python -m bridges.local open')",
                timeout=8,
            )
            assert not [r for r in await local_received(dsn) if r["text"] == "ignored"]

    run(go())


def test_a_send_while_the_bridge_is_down_is_retried_with_the_same_id_and_records_once(dsn, op):
    async def go():
        held: list = []
        async with page_up(dsn, op, hold_bridge=held) as (tab, base, token, _):
            await tab.goto(f"{base}/#{token}")
            bridge = held[0]
            await bridge.close()
            await tab.type_and_send("sent into the dark")
            await asyncio.sleep(1)
            assert await tab.js("document.getElementById('text').value") == "sent into the dark"
            assert not [r for r in await local_received(dsn) if r["text"] == "sent into the dark"]
            await bridge.start()
            await tab.wait("document.getElementById('text').value === ''", timeout=10)
            got = [r for r in await local_received(dsn) if r["text"] == "sent into the dark"]
            assert len(got) == 1
            await tab.wait(
                "[...document.querySelectorAll('#log li.tom')].some(l => l.textContent === 'sent into the dark')",
                timeout=6,
            )

    run(go())


async def notice_rows(dsn, tab, base, token, http, texts: list[str]) -> list[str]:
    """One of Valor's notices on the local chat per text; a JS expression
    for each one's row, once the page shows them all."""
    task = await new_task(dsn)
    async with await db.connect(dsn) as conn:
        nids = [
            await notices.request(conn, task, kind="test", about_key=f"t:{task}:{i}", text=t, channel="local")
            for i, t in enumerate(texts)
        ]
    event_ids: dict = {}

    async def logged():
        async with http.get(f"{base}/log", headers={local.TOKEN_HEADER: token}) as r:
            rows = (await r.json())["rows"]
        event_ids.update({row["message_id"]: row["event_id"] for row in rows if row["message_id"] in nids})
        return len(event_ids) == len(nids)

    await until(logged, timeout=10)
    rows = [f"document.querySelector('#log > li[data-event-id=\"{event_ids[n]}\"]')" for n in nids]
    await tab.wait(" && ".join(f"!!{r}" for r in rows), timeout=10)
    return rows


# Each would set window.pwned if it ever ran.
HOSTILE = [
    "<script>window.pwned=1</script>",
    '<img src=x onerror="window.pwned=1">',
    "[click me](javascript:window.pwned=1)",
    '<a href="javascript:window.pwned=1">raw</a>',
    '<svg onload="window.pwned=1"></svg>',
    '![x](x" onerror="window.pwned=1)',
    "[spaced]( JaVaScRiPt:window.pwned=1)",
]


def test_a_hostile_row_renders_with_nothing_live(dsn, op):
    async def go():
        async with page_up(dsn, op) as (tab, base, token, http):
            await tab.goto(f"{base}/#{token}")
            rows = await notice_rows(dsn, tab, base, token, http, HOSTILE)
            live = "#log script, #log img, #log svg, #log iframe, #log math"
            assert await tab.js(f"document.querySelectorAll('{live}').length") == 0
            handlers = await tab.js(
                "[...document.querySelectorAll('#log *')].flatMap(e => [...e.attributes]"
                ".map(a => a.name)).filter(n => n.toLowerCase().startsWith('on'))"
            )
            assert handlers == []
            hrefs = await tab.js(
                "[...document.querySelectorAll('#log a[href]')].map(a => a.getAttribute('href'))"
            )
            assert not [h for h in hrefs if h.strip().lower().startswith("javascript:")], hrefs
            # HTML typed in a text shows as typed (a notice ends with its id).
            for text, row in zip(HOSTILE, rows, strict=True):
                if text.startswith("<"):
                    assert (await tab.js(f"{row}.textContent")).startswith(text)
            # The Markdown link, clicked, runs nothing; a deferred handler
            # would have fired by the second look.
            link = f"{rows[2]}.querySelector('a')"
            assert await tab.js(f"{link}.textContent") == "click me"
            await tab.js(
                f"{link}.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true}}))"
            )
            assert await tab.js("window.pwned === undefined")
            await asyncio.sleep(1)
            assert await tab.js("window.pwned === undefined")

    run(go())


MARKDOWN = """# A heading

- one
- two

1. first
2. second

Some **bold** and `inline` and a [link](https://example.com/).

```
line one
line two
```

| Name | Value |
|---|---|
| a | 1 |
"""


def test_markdown_renders(dsn, op):
    async def go():
        async with page_up(dsn, op) as (tab, base, token, http):
            await tab.goto(f"{base}/#{token}")
            (row,) = await notice_rows(dsn, tab, base, token, http, [MARKDOWN])
            assert await tab.js(f"{row}.querySelector('h1').textContent") == "A heading"
            items = "[...{}.querySelectorAll('{} > li')].map(l => l.textContent)"
            assert await tab.js(items.format(row, "ul")) == ["one", "two"]
            assert await tab.js(items.format(row, "ol")) == ["first", "second"]
            assert await tab.js(f"{row}.querySelector('strong').textContent") == "bold"
            assert await tab.js(f"{row}.querySelector(':not(pre) > code').textContent") == "inline"
            assert await tab.js(f"{row}.querySelector('pre > code').textContent") == "line one\nline two\n"
            assert await tab.js(f"[...{row}.querySelectorAll('table th')].map(t => t.textContent)") == [
                "Name",
                "Value",
            ]
            link = f"{row}.querySelector('a')"
            assert await tab.js(f"{link}.getAttribute('href')") == "https://example.com/"
            assert await tab.js(f"{link}.target") == "_blank"
            assert "noopener" in await tab.js(f"{link}.rel")

            # A click on the link does not choose the row. The test cancels
            # the click so no new tab opens; the row's handler still sees it.
            await tab.js(
                "document.getElementById('log').addEventListener('click', e => e.preventDefault(), true)"
            )
            await tab.js(f"{link}.click()")
            assert await tab.js("!document.querySelector('#log li.chosen')")
            await tab.js(f"{row}.querySelector('h1').click()")
            assert await tab.js(f"{row}.classList.contains('chosen')")

            # Tom's rows render too.
            await tab.type_and_send("**from tom**")
            await tab.wait(
                "[...document.querySelectorAll('#log > li.tom')]"
                ".some(l => l.querySelector('strong')?.textContent === 'from tom')"
            )

    run(go())
