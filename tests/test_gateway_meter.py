"""The gateway meters a successful call, against a local upstream replaying a
recorded provider response (`tests/fixtures/`, recorded through the gateway
by `record_messages.py`; see `recorded.json`).

Every expected charge is worked by hand from the fixture's usage and the
Haiku 4.5 price checked on 2026-10-01 ($1 input and $5 output per million
tokens, which is 1 and 5 micro-dollars per token), never computed by
`budget.cost`.

Live spend: none.
"""

import asyncio
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from core import db, tasks
from core.gateway import Gateway

pytestmark = pytest.mark.spend(usd=0)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STREAM = (FIXTURES / "messages_stream_haiku.sse").read_bytes()
WHOLE = (FIXTURES / "messages_haiku.json").read_bytes()
BODY = {"model": "claude-haiku-4-5", "max_tokens": 32, "messages": [{"role": "user", "content": "hi"}]}

# 14 input tokens x 1 micro-dollar + 4 output tokens x 5 micro-dollars.
COMPLETE = 14 * 1 + 4 * 5  # 34
# Cut before `message_delta`: the input `message_start` reported (14) and
# every output token the call allowed (max_tokens 32), never less than the
# invoice could be.
CUT = 14 * 1 + 32 * 5  # 174


async def _upstream(body: bytes, *, status: int = 200, content_type: str, chunk: int | None = None):
    async def handle(request: web.Request) -> web.StreamResponse:
        await request.read()
        response = web.StreamResponse(status=status, headers={"content-type": content_type})
        await response.prepare(request)
        step = chunk or len(body) or 1
        for i in range(0, len(body), step):
            await response.write(body[i : i + step])
            await asyncio.sleep(0)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/messages", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


def _call(dsn, body: bytes, *, status: int = 200, content_type: str, chunk: int | None = None):
    async def go():
        runner, url = await _upstream(body, status=status, content_type=content_type, chunk=chunk)
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="meter", budget_usd_micros=100_000))
        gateway = Gateway(dsn, upstream=url)
        await gateway.start()
        base = gateway.issue(task, "turn-1")
        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=BODY) as r:
            got = (r.status, await r.read())
        await gateway.drain(task)
        await gateway.close()
        await runner.cleanup()
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
            charged = await (
                await conn.execute(
                    "SELECT payload FROM events WHERE task_id = %s AND type = 'gateway.charged'", (task,)
                )
            ).fetchone()
        return got, state, charged[0]

    return asyncio.run(go())


def _check(result, *, body: bytes, status: int, usd: int, complete: bool):
    (got_status, got_body), state, charged = result
    assert got_status == status and got_body == body  # forwarded byte for byte
    assert charged["usd_micros"] == usd and state["charged_usd_micros"] == usd
    assert charged["complete"] is complete
    assert charged["price_checked"] == "2026-10-01"
    assert not state["open_reservations"] and tasks.audit(state) == []


def test_a_recorded_stream_is_charged_what_the_provider_reported(dsn):
    result = _call(dsn, STREAM, content_type="text/event-stream")
    _check(result, body=STREAM, status=200, usd=COMPLETE, complete=True)
    assert result[2]["usage"]["input_tokens"] == 14 and result[2]["usage"]["output_tokens"] == 4


def test_the_same_stream_in_seven_byte_chunks_split_mid_line_is_charged_the_same(dsn):
    result = _call(dsn, STREAM, content_type="text/event-stream", chunk=7)
    _check(result, body=STREAM, status=200, usd=COMPLETE, complete=True)


def test_a_recorded_whole_response_is_charged_the_same(dsn):
    result = _call(dsn, WHOLE, content_type="application/json")
    _check(result, body=WHOLE, status=200, usd=COMPLETE, complete=True)


def test_a_stream_cut_before_its_final_usage_is_charged_every_allowed_output_token(dsn):
    cut = STREAM[: STREAM.index(b"event: message_delta")]
    result = _call(dsn, cut, content_type="text/event-stream")
    _check(result, body=cut, status=200, usd=CUT, complete=False)


def test_an_overloaded_provider_is_charged_nothing(dsn):
    error = b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'
    result = _call(dsn, error, status=529, content_type="application/json")
    _check(result, body=error, status=529, usd=0, complete=False)


# -- the credential: the gateway's, never the turn's ---------------------------------------


async def _recording_upstream(seen: list, status: int = 200):
    async def handle(request: web.Request) -> web.Response:
        await request.read()
        seen.append({k.lower(): v for k, v in request.headers.items()})
        return web.Response(status=status, body=WHOLE, headers={"content-type": "application/json"})

    app = web.Application()
    app.router.add_post("/v1/messages", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


def _credentialed_call(dsn, credential, *, status=200, headers=None):
    from core.gateway import TURN_TOKEN

    async def go():
        seen: list = []
        runner, url = await _recording_upstream(seen, status)
        gateway = Gateway(dsn, upstream=url, credential=credential)
        await gateway.start()
        try:
            async with await db.connect(dsn) as conn:
                task = await tasks.start(conn, tasks.Brief(instruction="x", budget_usd_micros=1_000_000))
            base = gateway.issue(task, "turn-1")
            sent = {"authorization": f"Bearer {TURN_TOKEN}", "x-api-key": "sk-turn-key", **(headers or {})}
            async with (
                aiohttp.ClientSession() as s,
                s.post(f"{base}/v1/messages", json=BODY, headers=sent) as r,
            ):
                return r.status, await r.json(), seen
        finally:
            await gateway.close()
            await runner.cleanup()

    return asyncio.run(go())


def test_the_gateway_replaces_the_turns_placeholder_with_the_kernels_credential(dsn, tmp_path):
    from core.gateway import ClaudeLogin

    token = tmp_path / "claude-token"
    token.write_text("kernel-held-token\n")
    status, _, seen = _credentialed_call(dsn, ClaudeLogin(str(token)))
    assert status == 200
    assert seen[0]["authorization"] == "Bearer kernel-held-token"
    assert "x-api-key" not in seen[0]


def test_no_kernel_credential_is_a_401_naming_the_remedy_and_no_call(dsn, tmp_path):
    from core.gateway import ClaudeLogin

    login = ClaudeLogin(str(tmp_path / "absent"), service=f"valor-test-no-such-item-{tmp_path.name}")
    status, body, seen = _credentialed_call(dsn, login)
    assert status == 401 and not seen
    assert "no Claude login in the Keychain" in body["error"]["message"]


def test_a_401_upstream_rereads_the_credential(dsn, tmp_path):
    from core.gateway import ClaudeLogin

    token = tmp_path / "claude-token"
    token.write_text("first\n")
    login = ClaudeLogin(str(token), ttl_s=3600)
    assert login.token() == "first"
    token.write_text("second\n")
    assert login.token() == "first"  # cached
    status, _, seen = _credentialed_call(dsn, login, status=401)
    assert status == 401 and seen[0]["authorization"] == "Bearer first"
    assert login.token() == "second"  # the 401 invalidated the cache
