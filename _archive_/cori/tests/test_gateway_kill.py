"""Revoke as kill, and the ledger a killed call leaves. Plan 04 task 7.

The stop button is physical here: a revoked Brief reads no further chunk,
the provider connection is dropped, and the call is charged its reserve
because the provider billed a generation the gateway did not see finish.
Every way a call can end writes a closing row, and no lock outlives a call.

The fake provider runs under uvicorn on a loopback port rather than behind
an in-process transport, because timing is the thing under test: an
in-process transport buffers the whole response and there is nothing left
to cut. The client is the response's own byte iterator, so closing it is
exactly what a reader that goes away does.
"""

import asyncio
import json
import uuid

import httpx
import psycopg
import pytest
import uvicorn

from gateway import log as gwlog
from gateway.app import CUT_EVENT, handle
from gateway.budget import canonical, cost
from gateway.core import Gateway
from infra.models import load
from tests.conftest import dsn, requires_postgres
from tests.gateway_fakes import FakeTree, FakeUpstream, request_for, text_stream

pytestmark = requires_postgres

MODEL = "claude-opus-5"
PRICES = load().model("frontier").usd_micros_per_mtok
PLENTY = 100_000_000


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


def body(**rest) -> dict:
    return {
        "model": MODEL,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
        **rest,
    }


@pytest.fixture
def upstream() -> FakeUpstream:
    fake = FakeUpstream(count=50)
    fake.events = text_stream(MODEL, "hello", output_tokens=20, input_tokens=60)
    fake.chunk_delay = 0.02
    return fake


@pytest.fixture
async def served(upstream):
    """The fake provider on a loopback port of its own."""
    config = uvicorn.Config(upstream, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


@pytest.fixture
def tree() -> FakeTree:
    return FakeTree()


@pytest.fixture
def gateway(served, tree) -> Gateway:
    return Gateway(
        provider_key="not-a-key", connect=connect, tree=tree, upstream=served
    )


async def seat(gateway, tree, conn, space, *, usd_micros=PLENTY):
    brief = uuid.uuid4().hex
    token = await gateway.issue_token(
        conn, brief_id=brief, generation=1, model_ref=MODEL, space=space
    )
    await conn.commit()
    tree.add(brief, usd_micros)
    return brief, {"x-api-key": token.get_secret_value()}


async def revoke(gateway, brief):
    async with await connect() as conn:
        await gateway.revoke(conn, brief)
        await conn.commit()


async def closing_rows(brief):
    async with await connect() as conn:
        return await (
            await conn.execute(
                "SELECT event, reason, usage FROM gateway_log WHERE brief_id = %s "
                "AND event IN ('response', 'cut', 'upstream_error') ORDER BY id",
                (brief,),
            )
        ).fetchall()


def charge_of(usage: dict) -> int:
    return cost({k: v for k, v in usage.items() if k.endswith("tokens")}, PRICES)


# ---------------------------------------------------------------------------
# Revoke during a stream


async def test_revoke_cuts_the_stream_within_two_chunks(
    gateway, upstream, tree, kernel, space
):
    brief, headers = await seat(gateway, tree, kernel, space)
    response = await handle(gateway, request_for(body(stream=True), headers))
    chunks, after = [], None

    async for chunk in response.body_iterator:
        chunks.append(chunk)
        if after is not None:
            after += 1
        elif len(chunks) == 1:
            await revoke(gateway, brief)
            after = 0

    assert after <= 2
    assert chunks[-1] == CUT_EVENT
    assert len(chunks) < len(upstream.events)
    assert len(upstream.message_calls) == 1


async def test_the_cut_row_is_charged_the_reserve_and_consumed_incurred(
    gateway, upstream, tree, kernel, space
):
    """The generation is bumped before the charge lands: `consume` runs no
    generation check, so a cut call's charge lands after the stop that cut
    it (seams §3.2, ruling 6)."""
    brief, headers = await seat(gateway, tree, kernel, space)
    response = await handle(gateway, request_for(body(stream=True), headers))

    async for _ in response.body_iterator:
        await revoke(gateway, brief)
        tree.generations[brief] = 2

    await gateway.drain()
    rows = await closing_rows(brief)
    assert [r[0] for r in rows] == ["cut"]
    usage = rows[0][2]
    assert usage["charged_reserved"] is True
    assert usage["output_tokens"] == 64
    assert usage["usd_micros"] == charge_of(usage)
    assert tree.consumed[-1][1].usd_micros == usage["usd_micros"]
    assert tree.consumed[-1][2] is True
    assert not gateway.lock(brief).locked()


async def test_a_revoked_brief_reaches_upstream_no_more(
    gateway, upstream, tree, kernel, space
):
    brief, headers = await seat(gateway, tree, kernel, space)
    await revoke(gateway, brief)

    for _ in range(3):
        response = await handle(gateway, request_for(body(), headers))
        assert response.status_code == 403
    assert upstream.message_calls == []


async def test_a_non_streaming_client_gets_403_when_cut(
    gateway, upstream, tree, kernel, space
):
    brief, headers = await seat(gateway, tree, kernel, space)

    async def revoke_now(payload):
        await revoke(gateway, brief)

    upstream.on_message = revoke_now
    response = await handle(gateway, request_for(body(), headers))

    assert response.status_code == 403
    assert json.loads(response.body)["error"]["type"] == "permission_error"
    await gateway.drain()
    assert (await closing_rows(brief))[0][0] == "cut"


# ---------------------------------------------------------------------------
# The client and the provider going away


async def test_client_disconnect_closes_call_and_releases_lock(
    gateway, upstream, tree, kernel, space
):
    brief, headers = await seat(gateway, tree, kernel, space)
    response = await handle(gateway, request_for(body(stream=True), headers))

    async for _ in response.body_iterator:
        break  # the reader goes away after message_start
    await response.body_iterator.aclose()

    await gateway.drain()
    rows = await closing_rows(brief)
    assert rows[0][0] == "cut"
    assert rows[0][2]["charged_reserved"] is True
    assert tree.consumed[-1][2] is True

    assert not gateway.lock(brief).locked()
    again = await asyncio.wait_for(
        handle(gateway, request_for(body(), headers)), timeout=10
    )
    assert again.status_code == 200


async def test_a_client_that_drops_before_any_byte_consumes_nothing(
    gateway, upstream, tree, kernel, space
):
    upstream.chunk_delay = 0.5
    brief, headers = await seat(gateway, tree, kernel, space)
    response = await handle(gateway, request_for(body(stream=True), headers))

    await response.body_iterator.aclose()  # nothing was ever read

    await gateway.drain()
    assert [r[:2] for r in await closing_rows(brief)] == [
        ("upstream_error", "client_disconnect")
    ]
    assert tree.consumed == []


async def test_an_upstream_timeout_after_message_start_is_a_cut(
    served, tree, kernel, space, upstream
):
    """The provider billed a generation the gateway did not see finish
    (spike 03, surprise 2)."""
    upstream.stall_after = 1
    gateway = Gateway(
        provider_key="not-a-key",
        connect=connect,
        tree=tree,
        client=httpx.AsyncClient(
            base_url=served,
            timeout=httpx.Timeout(connect=5.0, read=0.3, write=5.0, pool=5.0),
        ),
    )
    brief, headers = await seat(gateway, tree, kernel, space)

    response = await handle(gateway, request_for(body(), headers))
    assert response.status_code == 200

    await gateway.drain()
    rows = await closing_rows(brief)
    assert rows[0][0] == "cut"
    assert rows[0][2]["charged_reserved"] is True
    assert tree.consumed[-1][2] is True


async def test_an_upstream_timeout_before_any_byte_consumes_nothing(
    served, tree, kernel, space, upstream
):
    upstream.stall_after = 0
    gateway = Gateway(
        provider_key="not-a-key",
        connect=connect,
        tree=tree,
        client=httpx.AsyncClient(
            base_url=served,
            timeout=httpx.Timeout(connect=5.0, read=0.3, write=5.0, pool=5.0),
        ),
    )
    brief, headers = await seat(gateway, tree, kernel, space)

    await handle(gateway, request_for(body(), headers))

    await gateway.drain()
    assert [r[:2] for r in await closing_rows(brief)] == [("upstream_error", "timeout")]
    assert tree.consumed == []


# ---------------------------------------------------------------------------
# Restart


async def test_start_closes_a_request_the_last_process_left_open(
    gateway, tree, kernel, space
):
    """A gateway killed mid-stream leaves a conservative ledger, the way the
    broker reconciles a dangling intent."""
    brief, _ = await seat(gateway, tree, kernel, space)
    call = body(max_tokens=500)
    sha = gwlog.body_sha256(canonical(call))
    async with await connect() as conn:
        await gwlog.insert_body(conn, sha, call)
        await gwlog.insert_log(
            conn,
            event="request",
            brief_id=brief,
            generation=1,
            space_id=space,
            model=MODEL,
            call=7,
            request_sha256=sha,
            estimated_input=300,
        )
        await conn.commit()

    restarted = Gateway(provider_key="not-a-key", connect=connect, tree=tree)
    async with await connect() as conn:
        for row in await gwlog.dangling_requests(conn):
            tree.allocations.setdefault(row[0], PLENTY)
            tree.generations.setdefault(row[0], 1)
    await restarted.start()

    rows = await closing_rows(brief)
    assert [r[:2] for r in rows] == [("cut", "reconciled")]
    usage = rows[0][2]
    assert usage["charged_reserved"] is True
    assert usage["output_tokens"] == 500 and usage["input_tokens"] == 300
    assert usage["usd_micros"] == cost(
        {"input_tokens": 300, "output_tokens": 500}, PRICES
    )
    assert [c for c in tree.consumed if c[0] == brief][0][1].usd_micros == (
        usage["usd_micros"]
    )

    # The call counter continues from the log's maximum for the Brief.
    async with await connect() as conn:
        assert await gwlog.last_call(conn, brief) == 7
