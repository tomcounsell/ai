"""The gateway meters a successful call, against a local upstream replaying a
recorded provider response (`tests/fixtures/`, recorded through the gateway
by `record_messages.py`; see `recorded.json`).

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

from core import db, ledger, spending, tasks
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
            task = await tasks.start(conn, tasks.Brief(instruction="meter"))
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
    assert charged["usd_micros"] == usd and state["spent_usd_micros"] == usd
    assert charged["complete"] is complete
    assert charged["price_checked"] == "2026-10-01"
    assert not state["open_calls"] and tasks.audit(state) == []


def test_a_recorded_stream_is_charged_what_the_provider_reported(dsn):
    result = _call(dsn, STREAM, content_type="text/event-stream")
    _check(result, body=STREAM, status=200, usd=COMPLETE, complete=True)
    assert result[2]["usage"]["input_tokens"] == 14 and result[2]["usage"]["output_tokens"] == 4


def test_the_same_stream_in_seven_byte_chunks_split_mid_line_is_charged_the_same(dsn):
    result = _call(dsn, STREAM, content_type="text/event-stream", chunk=7)
    _check(result, body=STREAM, status=200, usd=COMPLETE, complete=True)


def test_a_call_on_a_task_with_any_spend_is_never_refused_for_money(dsn):
    """Spending is metered, never a wall: a task that has already spent
    $1,000 and holds a call opened with a $1,000 worst case still has its
    next call forwarded, opened, and charged."""

    async def go():
        runner, url = await _upstream(WHOLE, content_type="application/json")
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="spent"))
            await spending.open_call(
                conn, task, {"call_id": f"{task}-old", "turn_id": "t", "estimate_usd_micros": 10**9}
            )
            await spending.charge(conn, task, f"{task}-earlier", 10**9, {})
        gateway = Gateway(dsn, upstream=url)
        await gateway.start()
        base = gateway.issue(task, "turn-1")
        body = {**BODY, "max_tokens": 10**8}  # a worst case of $500
        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=body) as r:
            got = r.status
        await gateway.drain(task)
        await gateway.close()
        await runner.cleanup()
        async with await db.connect(dsn) as conn:
            return got, await tasks.status(conn, task), [r["type"] for r in await ledger.read(conn, task)]

    got, state, kinds = asyncio.run(go())
    assert got == 200 and "gateway.refused" not in kinds
    assert kinds.count("gateway.opened") == 2 and kinds.count("gateway.charged") == 2
    assert state["spent_usd_micros"] == 10**9 + COMPLETE
    assert list(state["open_calls"]) == [f"{state['task_id']}-old"]


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


def _credentialed_call(dsn, credential, *, status=200, headers=None, path="/v1/messages", method="post"):
    from core.gateway import TURN_TOKEN

    async def go():
        seen: list = []
        runner, url = await _recording_upstream(seen, status)
        gateway = Gateway(dsn, upstream=url, credential=credential)
        await gateway.start()
        try:
            async with await db.connect(dsn) as conn:
                task = await tasks.start(conn, tasks.Brief(instruction="x"))
            base = gateway.issue(task, "turn-1")
            sent = {"authorization": f"Bearer {TURN_TOKEN}", "x-api-key": "sk-turn-key", **(headers or {})}
            async with (
                aiohttp.ClientSession() as s,
                s.request(method, f"{base}{path}", json=BODY, headers=sent) as r,
            ):
                return r.status, await r.json(content_type=None), seen
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

    login = ClaudeLogin(str(tmp_path / "absent"), keychain=lambda: None)
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


def test_the_credential_goes_only_to_the_messages_api_and_the_model_list(dsn, tmp_path):
    from core.gateway import ClaudeLogin, credentialed

    token = tmp_path / "claude-token"
    token.write_text("kernel-held-token\n")
    status, body, seen = _credentialed_call(dsn, ClaudeLogin(str(token)), path="/v1/files", method="get")
    assert status == 403 and not seen and "carries no turn" in body["error"]["message"]
    assert (
        credentialed("v1/messages")
        and credentialed("v1/messages/count_tokens/")
        and credentialed("v1/models")
    )
    assert credentialed("v1/models/claude-opus-5-5") and not credentialed("v1/models/../x")
    assert (
        not credentialed("v1/files")
        and not credentialed("v1/messages/batches")
        and not credentialed("v1/organizations")
    )


def _keychain_json(expires_ms, token="kc-token"):
    import json
    import time

    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": token,
                "expiresAt": expires_ms or int(time.time() * 1000) + 3600_000,
            }
        }
    )


def test_the_kernels_claude_login_is_read_from_its_file_or_the_keychain_and_refused_when_unusable(tmp_path):
    from core.gateway import ClaudeLogin, CredentialUnavailable

    absent = str(tmp_path / "absent")
    assert ClaudeLogin(absent, keychain=lambda: _keychain_json(None)).token() == "kc-token"
    with pytest.raises(CredentialUnavailable, match="expired"):
        ClaudeLogin(absent, keychain=lambda: _keychain_json(1000)).token()
    for raw in ('{"x": 1}', "not json", '{"claudeAiOauth": {"accessToken": "t", "expiresAt": "soon"}}'):
        with pytest.raises(CredentialUnavailable, match="not in the shape"):
            ClaudeLogin(absent, keychain=lambda raw=raw: raw).token()
    empty = tmp_path / "empty-token"
    empty.write_text("\n")
    assert ClaudeLogin(str(empty), keychain=lambda: _keychain_json(None)).token() == "kc-token"
    file_token = tmp_path / "claude-token"
    file_token.write_text("from-file\n")
    assert (
        ClaudeLogin(str(file_token), keychain=lambda: _keychain_json(None, "from-keychain")).token()
        == "from-file"
    )


def test_an_expired_login_is_read_from_the_keychain_at_most_once_a_minute(tmp_path):
    from core.gateway import ClaudeLogin, CredentialUnavailable

    calls = []

    def keychain():
        calls.append(1)
        return _keychain_json(1000)

    login = ClaudeLogin(str(tmp_path / "absent"), keychain=keychain)
    for _ in range(5):
        with pytest.raises(CredentialUnavailable):
            login.token()
        login.invalidate()  # as a 401 would
    assert len(calls) == 2  # the first 401 after a read may read once more; then once a minute


def _raw_calls(dsn, credential, paths):
    """Each path sent exactly as written (no client normalising), and what
    the upstream saw: its raw path and authorization."""
    from yarl import URL

    from core.gateway import TURN_TOKEN

    async def go():
        seen: list = []

        async def handle(request: web.Request) -> web.Response:
            seen.append((request.raw_path, request.headers.get("authorization")))
            return web.Response(status=200, body=b"{}", headers={"content-type": "application/json"})

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        gateway = Gateway(dsn, upstream=url, credential=credential)
        await gateway.start()
        out = {}
        try:
            async with await db.connect(dsn) as conn:
                task = await tasks.start(conn, tasks.Brief(instruction="x"))
            base = gateway.issue(task, "turn-1")
            async with aiohttp.ClientSession() as s:
                for p in paths:
                    async with s.get(
                        URL(base + p, encoded=True), headers={"authorization": f"Bearer {TURN_TOKEN}"}
                    ) as r:
                        out[p] = r.status
        finally:
            await gateway.close()
            await runner.cleanup()
        return out, seen

    return asyncio.run(go())


def test_no_path_trick_carries_the_credential_anywhere_but_the_allowed_paths(dsn, tmp_path):
    from core.gateway import ClaudeLogin

    token = tmp_path / "claude-token"
    token.write_text("kernel-held-token\n")
    attacks = [
        "/v1/models/../../api/oauth/profile",
        "/v1/models/%2e%2e/%2e%2e/api/oauth/profile",
        "/v1/models/..%2f..%2fapi/oauth/profile",
        "/v1/models%2f..%2f..%2fapi/oauth",
        "/v1/models/./x",
        "/v1/models//x",
        "/v1/models/a..b",
        "/v1/files",
        "/v1/messages/batches",
        "/v1/organizations",
        "/api/oauth/profile",
    ]
    statuses, seen = _raw_calls(
        dsn, ClaudeLogin(str(token)), [*attacks, "/v1/models/claude-opus-5-5", "/v1/models"]
    )
    assert all(statuses[p] in (400, 403) for p in attacks), statuses
    assert seen == [
        ("/v1/models/claude-opus-5-5", "Bearer kernel-held-token"),
        ("/v1/models", "Bearer kernel-held-token"),
    ]


def test_without_a_credential_the_gateway_still_refuses_tricky_paths_before_forwarding(dsn):
    statuses, seen = _raw_calls(
        dsn, None, ["/v1/models/../x", "/v1/models/%2e%2e/x", "/v1//messages", "/v1/models"]
    )
    assert statuses["/v1/models/../x"] == statuses["/v1/models/%2e%2e/x"] == statuses["/v1//messages"] == 400
    assert seen == [("/v1/models", "Bearer valor-turn-holds-no-credential")]


def test_an_allowed_path_with_an_escaped_query_reaches_upstream_byte_for_byte(dsn, tmp_path):
    from core.gateway import ClaudeLogin

    token = tmp_path / "claude-token"
    token.write_text("kernel-held-token\n")
    path = "/v1/models?after_id=a%2Fb%20c&limit=2"
    statuses, seen = _raw_calls(dsn, ClaudeLogin(str(token)), [path])
    assert statuses[path] == 200 and seen == [(path, "Bearer kernel-held-token")]


# -- no timeout of its own: an exit or a disconnect cuts a call ----------------------------


async def _raw_upstream(*, answer: bytes | None = None, hang_up: bool = False, status: str = "200 OK"):
    """A local upstream on a bare socket: it reads a request's head, then
    sends `answer` (if any) under `status` and waits, or hangs up at once. `seen` is set
    when a request arrives; `closed` when the gateway closes its side."""
    seen, closed = asyncio.Event(), asyncio.Event()

    async def on(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        seen.set()
        if hang_up:
            writer.close()
            return
        if answer is not None:
            head = (
                f"HTTP/1.1 {status}\r\n".encode()
                + b"content-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n"
            )
            writer.write(head + f"{len(answer):x}\r\n".encode() + answer + b"\r\n")
            await writer.drain()
        while await reader.read(65536):
            pass
        closed.set()
        writer.close()

    server = await asyncio.start_server(on, "127.0.0.1", 0)
    return server, f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", seen, closed


async def _rows(dsn, task, kind):
    async with await db.connect(dsn) as conn:
        return [r["payload"] for r in await ledger.read(conn, task) if r["type"] == kind]


async def _new_task(dsn) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="cut"))


def test_the_gateway_sets_no_upstream_timeout_of_its_own(dsn):
    async def go():
        gateway = Gateway(dsn, upstream="http://127.0.0.1:9")
        await gateway.start()
        timeout = gateway._session.timeout
        await gateway.close()
        return timeout

    timeout = asyncio.run(go())
    assert timeout.total is None and timeout.connect is None
    assert timeout.sock_read is None and timeout.sock_connect is None


def test_a_client_that_disconnects_cuts_its_call(dsn):
    async def go():
        server, url, seen, closed = await _raw_upstream()
        task = await _new_task(dsn)
        gateway = Gateway(dsn, upstream=url)
        await gateway.start()
        base = gateway.issue(task, "turn-1")

        async def client():
            async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=BODY) as r:
                await r.read()

        pending = asyncio.create_task(client())
        await asyncio.wait_for(seen.wait(), 10)
        pending.cancel()
        await asyncio.wait_for(gateway.drain(task), 10)
        await asyncio.wait_for(closed.wait(), 10)
        await gateway.close()
        server.close()
        return await _rows(dsn, task, "gateway.opened"), await _rows(dsn, task, "gateway.charged")

    opened, charged = asyncio.run(go())
    assert len(opened) == 1 and len(charged) == 1
    assert charged[0]["cut"] is True and charged[0]["unsent"] is False
    assert charged[0]["usd_micros"] == opened[0]["estimate_usd_micros"]  # nothing reported: the worst case


def test_a_401_whose_client_leaves_at_once_still_rereads_the_credential(dsn, tmp_path):
    from core.gateway import ClaudeLogin

    token = tmp_path / "claude-token"
    token.write_text("first\n")
    login = ClaudeLogin(str(token), ttl_s=3600)
    assert login.token() == "first"
    token.write_text("second\n")

    async def go():
        started = STREAM[: STREAM.index(b"event: message_delta")]
        server, url, _, closed = await _raw_upstream(answer=started, status="401 Unauthorized")
        task = await _new_task(dsn)
        gateway = Gateway(dsn, upstream=url, credential=login)
        await gateway.start()
        base = gateway.issue(task, "turn-1")

        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=BODY) as r:
            got = r.status  # the head only; the client leaves before the body ends
        await asyncio.wait_for(gateway.drain(task), 10)
        await asyncio.wait_for(closed.wait(), 10)
        await gateway.close()
        server.close()
        return got

    assert asyncio.run(go()) == 401
    assert login.token() == "second"


def test_a_started_stream_whose_client_leaves_is_charged_once(dsn):
    async def go():
        started = STREAM[: STREAM.index(b"event: message_delta")]
        server, url, _, closed = await _raw_upstream(answer=started)
        task = await _new_task(dsn)
        gateway = Gateway(dsn, upstream=url)
        await gateway.start()
        base = gateway.issue(task, "turn-1")
        first = asyncio.Event()

        async def client():
            async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=BODY) as r:
                await r.content.readany()
                first.set()
                await r.read()

        pending = asyncio.create_task(client())
        await asyncio.wait_for(first.wait(), 10)
        pending.cancel()
        await asyncio.wait_for(gateway.drain(task), 10)
        await asyncio.wait_for(closed.wait(), 10)
        await gateway.close()
        server.close()
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
        return await _rows(dsn, task, "gateway.opened"), await _rows(dsn, task, "gateway.charged"), state

    opened, charged, state = asyncio.run(go())
    assert len(opened) == 1 and len(charged) == 1
    assert charged[0]["usd_micros"] == CUT and charged[0]["cut"] is True
    assert not state["open_calls"] and tasks.audit(state) == []


def test_an_unreachable_upstream_is_a_502_naming_it(dsn):
    async def go():
        task = await _new_task(dsn)
        gateway = Gateway(dsn, upstream="http://127.0.0.1:6561")  # nothing listens here
        await gateway.start()
        base = gateway.issue(task, "turn-1")
        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=BODY) as r:
            got = r.status, await r.json()
        await gateway.drain(task)
        await gateway.close()
        return got, await _rows(dsn, task, "gateway.charged")

    (status, body), charged = asyncio.run(go())
    assert status == 502 and "could not reach the provider" in body["error"]["message"]
    assert charged[0]["unsent"] is True and charged[0]["usd_micros"] == 0


def test_an_upstream_that_closes_before_answering_is_a_502_naming_it(dsn):
    async def go():
        server, url, _, _ = await _raw_upstream(hang_up=True)
        task = await _new_task(dsn)
        gateway = Gateway(dsn, upstream=url)
        await gateway.start()
        base = gateway.issue(task, "turn-1")
        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=BODY) as r:
            got = r.status, await r.json()
        await gateway.drain(task)
        await gateway.close()
        server.close()
        return got, await _rows(dsn, task, "gateway.charged")

    (status, body), charged = asyncio.run(go())
    assert status == 502 and "ended before it answered" in body["error"]["message"]
    assert len(charged) == 1 and charged[0]["cut"] is True


def _held_open(dsn, monkeypatch, leave):
    """A call held inside its open, then `leave(gateway, task, pending)`,
    then let go: what the upstream saw and the call's rows."""
    real = spending.open_call
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(conn, task_id, call):
        entered.set()
        await release.wait()
        return await real(conn, task_id, call)

    monkeypatch.setattr(spending, "open_call", held)

    async def go():
        server, url, seen, _ = await _raw_upstream()
        task = await _new_task(dsn)
        gateway = Gateway(dsn, upstream=url)
        await gateway.start()
        base = gateway.issue(task, "turn-1")

        async def client():
            async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=BODY) as r:
                return r.status

        pending = asyncio.create_task(client())
        await asyncio.wait_for(entered.wait(), 10)
        await leave(gateway, task, pending)
        release.set()
        await asyncio.wait_for(gateway.drain(task), 10)
        await asyncio.sleep(0.2)
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await gateway.close()
        server.close()
        return (
            seen.is_set(),
            await _rows(dsn, task, "gateway.opened"),
            await _rows(dsn, task, "gateway.charged"),
        )

    return asyncio.run(go())


def test_a_call_revoked_while_it_opens_is_not_sent_and_is_charged_nothing(dsn, monkeypatch):
    async def revoke(gateway, task, pending):
        gateway.revoke(task)

    sent, opened, charged = _held_open(dsn, monkeypatch, revoke)
    assert not sent
    assert len(opened) == 1 and len(charged) == 1
    assert charged[0]["unsent"] is True and charged[0]["usd_micros"] == 0


def test_a_call_whose_client_leaves_while_it_opens_is_not_sent_and_is_charged_nothing(dsn, monkeypatch):
    async def leave(gateway, task, pending):
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await asyncio.sleep(0.2)  # the gateway's handler hears the disconnect

    sent, opened, charged = _held_open(dsn, monkeypatch, leave)
    assert not sent
    assert len(opened) == 1 and len(charged) == 1
    assert charged[0]["unsent"] is True and charged[0]["usd_micros"] == 0


def test_a_revoke_while_a_call_is_being_charged_leaves_one_charge(dsn, monkeypatch):
    real = spending.charge
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(conn, task_id, call_id, usd, detail):
        entered.set()
        await release.wait()
        return await real(conn, task_id, call_id, usd, detail)

    monkeypatch.setattr(spending, "charge", held)

    async def go():
        runner, url = await _upstream(WHOLE, content_type="application/json")
        task = await _new_task(dsn)
        gateway = Gateway(dsn, upstream=url)
        await gateway.start()
        base = gateway.issue(task, "turn-1")

        async def client():
            async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=BODY) as r:
                return r.status

        pending = asyncio.create_task(client())
        await asyncio.wait_for(entered.wait(), 10)
        gateway.revoke(task)
        release.set()
        await asyncio.wait_for(gateway.drain(task), 10)
        await asyncio.gather(pending, return_exceptions=True)
        await gateway.close()
        await runner.cleanup()
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
        return await _rows(dsn, task, "gateway.charged"), state

    charged, state = asyncio.run(go())
    assert len(charged) == 1 and charged[0]["usd_micros"] == COMPLETE
    assert not state["open_calls"] and tasks.audit(state) == []


def test_a_login_expiring_in_seconds_is_still_used(tmp_path):
    import time

    from core.gateway import ClaudeLogin

    soon = int(time.time() * 1000) + 10_000
    assert ClaudeLogin(str(tmp_path / "absent"), keychain=lambda: _keychain_json(soon)).token() == "kc-token"
