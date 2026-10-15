"""The gateway meters a model call, its opened and charged rows carrying the
turn's id, against a local upstream replaying a recorded provider response
(`tests/fixtures/messages_stream_haiku.sse` and `messages_haiku.json`).

Every expected charge is worked by hand from the fixture's usage and the
Haiku 4.5 price checked on 2026-10-01 ($1 input and $5 output per million
tokens, which is 1 and 5 micro-dollars per token), never computed by
`spending.cost`.

Live spend: none.
"""

import asyncio
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from core import db, ledger, tasks
from core.gateway import Gateway
from tests.ports import listen

pytestmark = pytest.mark.spend(usd=0)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STREAM = (FIXTURES / "messages_stream_haiku.sse").read_bytes()
WHOLE = (FIXTURES / "messages_haiku.json").read_bytes()
BODY = {"model": "claude-haiku-4-5", "max_tokens": 32, "messages": [{"role": "user", "content": "hi"}]}

# 14 input tokens x 1 micro-dollar + 4 output tokens x 5 micro-dollars.
COMPLETE = 14 * 1 + 4 * 5  # 34


async def _upstream(body: bytes, *, content_type: str):
    async def handle(request: web.Request) -> web.StreamResponse:
        await request.read()
        response = web.StreamResponse(headers={"content-type": content_type})
        await response.prepare(request)
        await response.write(body)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/messages", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", listen())
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


def _call(dsn, body: bytes, *, content_type: str, path: str = "/v1/messages"):
    async def go():
        runner, url = await _upstream(body, content_type=content_type)
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="meter"))
        gateway = Gateway(dsn, upstream=url)
        await gateway.start(port=listen())
        base = gateway.issue(task, "turn-1")
        async with aiohttp.ClientSession() as http, http.post(URL(base + path, encoded=True), json=BODY) as r:
            got = (r.status, await r.read())
        await gateway.drain(task)
        await gateway.close()
        await runner.cleanup()
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
            written = await ledger.read(conn, task)
        (opened,) = [r["payload"] for r in written if r["type"] == "gateway.opened"]
        (charged,) = [r["payload"] for r in written if r["type"] == "gateway.charged"]
        return got, state, opened, charged

    return asyncio.run(go())


def _check(result, *, body: bytes, usd: int):
    (got_status, got_body), state, opened, charged = result
    assert got_status == 200 and got_body == body  # forwarded byte for byte
    assert opened["turn_id"] == charged["turn_id"] == "turn-1"
    assert opened["call_id"] == charged["call_id"]
    assert charged["usd_micros"] == usd and state["spent_usd_micros"] == usd
    assert charged["complete"] is True
    assert charged["price_checked"] == "2026-10-01"
    assert not state["open_calls"] and tasks.audit(state) == []
    assert charged["unpriced"] is False and state["unpriced_calls"] == []


def test_a_recorded_stream_is_charged_what_the_provider_reported(dsn):
    result = _call(dsn, STREAM, content_type="text/event-stream")
    _check(result, body=STREAM, usd=COMPLETE)
    assert result[3]["usage"]["input_tokens"] == 14 and result[3]["usage"]["output_tokens"] == 4


def test_a_recorded_whole_response_is_charged_the_same_on_an_unnormal_path(dsn):
    result = _call(dsn, WHOLE, content_type="application/json", path="/v1/.//messages")
    _check(result, body=WHOLE, usd=COMPLETE)
