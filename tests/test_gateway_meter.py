"""The gateway meters a model call, its rows carrying the turn's id, against a
local upstream replaying a recorded response (`tests/fixtures/messages_*`).
Each charge is worked by hand from the fixture's usage at Haiku 4.5's price of
2026-10-01 (1 and 5 micro-dollars per input and output token), never by
`spending.cost`. Live spend: none.
"""

import asyncio
import sys
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from core import db, ledger, runs, tasks
from core.gateway import Gateway
from tests import bridges
from tests.ports import listen
from tests.test_kernel import audit, new_task

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
    assert not state["open_calls"] and audit(state) == []
    assert charged["unpriced"] is False and state["unpriced_calls"] == []


def test_a_recorded_stream_is_charged_what_the_provider_reported(dsn):
    result = _call(dsn, STREAM, content_type="text/event-stream")
    _check(result, body=STREAM, usd=COMPLETE)
    assert result[3]["usage"]["input_tokens"] == 14 and result[3]["usage"]["output_tokens"] == 4


def test_a_recorded_whole_response_is_charged_the_same_on_an_unnormal_path(dsn):
    result = _call(dsn, WHOLE, content_type="application/json", path="/v1/.//messages")
    _check(result, body=WHOLE, usd=COMPLETE)


# -- a call's front model shares its task with the worker's turns -----------------


async def _held_upstream(release: asyncio.Event):
    """An upstream that sends the first half of the recorded stream, waits
    for `release`, then sends the rest."""

    async def handle(request: web.Request) -> web.StreamResponse:
        await request.read()
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        await response.write(STREAM[: len(STREAM) // 2])
        await release.wait()
        await response.write(STREAM[len(STREAM) // 2 :])
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/messages", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", listen())
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


def _worker(tmp_path, seen: dict, script: str = "pass"):
    def build(url, brief, turn_id):
        seen["url"] = url
        return runs.TurnCommand(argv=[sys.executable, "-c", script], env={}, cwd=str(tmp_path), harness="stub",
                                tmpdir=str(tmp_path / "tmp"))  # fmt: skip

    return build


async def _post(url: str) -> tuple[int, bytes]:
    async with aiohttp.ClientSession() as http, http.post(url + "/v1/messages", json=BODY) as r:
        return r.status, await r.read()


def test_a_worker_turns_end_leaves_the_call_front_models_token_working(dsn, tmp_path):
    """The Captain, 2026-11-05: a call's worker turn ended at 06:03:28 and
    every front-model draft after it was refused 403, so Valor went silent.
    The turn's end retires its own token; the front's still answers."""

    async def go():
        runner, url = await _upstream(STREAM, content_type="text/event-stream")
        task = await new_task(dsn)
        gateway = await bridges.gateway(dsn, upstream=url)
        front = gateway.issue(task, "front")
        before = await _post(front)
        seen: dict = {}
        ended = await runs.run_turn(gateway, task, _worker(tmp_path, seen), dsn=dsn)
        after = await _post(front)
        worker_after = await _post(seen["url"])
        await gateway.drain(task)
        await gateway.close()
        await runner.cleanup()
        async with await db.connect(dsn) as conn:
            charged = [r["payload"] for r in await ledger.read(conn, task) if r["type"] == "gateway.charged"]
            return before, ended, after, worker_after, charged, await tasks.status(conn, task)

    before, ended, after, worker_after, charged, state = asyncio.run(go())
    assert before == (200, STREAM) and ended["outcome"] == "done"
    assert after == (200, STREAM)
    assert worker_after[0] == 403  # the finished turn's own token is still refused
    assert [c["turn_id"] for c in charged] == ["front", "front"]
    assert all(c["usd_micros"] == COMPLETE for c in charged) and audit(state) == []


def test_a_front_stream_in_flight_outlives_a_worker_turns_end_and_is_not_waited_on(dsn, tmp_path):
    async def go():
        release = asyncio.Event()
        runner, url = await _held_upstream(release)
        task = await new_task(dsn)
        gateway = await bridges.gateway(dsn, upstream=url)
        front = asyncio.create_task(_post(gateway.issue(task, "front")))
        await asyncio.sleep(0.5)
        ended = await asyncio.wait_for(runs.run_turn(gateway, task, _worker(tmp_path, {}), dsn=dsn), 30)
        still_open = not front.done()
        release.set()
        got = await front
        await gateway.drain(task)
        await gateway.close()
        await runner.cleanup()
        async with await db.connect(dsn) as conn:
            return ended, still_open, got, await tasks.status(conn, task)

    ended, still_open, got, state = asyncio.run(go())
    assert ended["outcome"] == "done" and still_open
    assert got == (200, STREAM) and audit(state) == []


def test_a_stop_still_cuts_the_front_models_stream_and_refuses_its_token(dsn, tmp_path):
    async def go():
        release = asyncio.Event()
        runner, url = await _held_upstream(release)
        task = await new_task(dsn)
        gateway = await bridges.gateway(dsn, upstream=url)
        base = gateway.issue(task, "front")
        front = asyncio.create_task(_post(base))
        turn = asyncio.create_task(
            runs.run_turn(gateway, task, _worker(tmp_path, {}, "import time; time.sleep(60)"), dsn=dsn)
        )
        await asyncio.sleep(1.0)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="the call was stopped")
        ended = await asyncio.wait_for(turn, 30)
        try:
            cut = await asyncio.wait_for(front, 10)
        except aiohttp.ClientError as exc:
            cut = exc
        after = await _post(base)
        release.set()
        await gateway.drain(task)
        await gateway.close()
        await runner.cleanup()
        async with await db.connect(dsn) as conn:
            return ended, cut, after, await tasks.status(conn, task)

    ended, cut, after, state = asyncio.run(go())
    assert ended["outcome"] == "stopped"
    assert cut != (200, STREAM)  # cut short, not run to its end
    assert after[0] == 403 and audit(state) == []
