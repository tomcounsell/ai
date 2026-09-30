"""The one route. Plan 04 task 6.

The gateway is the only path a model call takes, so these tests are the
record of what it refuses, what it forwards, and what it writes down. The
upstream is a fake ASGI app and the tree is a fake with a ledger, so every
assertion is about the gateway's own behaviour.
"""

import asyncio
import json
import uuid

import httpx
import psycopg
import pytest

from gateway.app import build_app
from gateway.budget import canonical, cost, reserve
from gateway.core import Gateway
from gateway.log import body_sha256
from infra.models import load
from tests.conftest import dsn, requires_postgres
from tests.gateway_fakes import FakeTree, FakeUpstream, sse, text_stream

pytestmark = requires_postgres

MODEL = "claude-opus-5"
PRICES = load().model("frontier").usd_micros_per_mtok
PLENTY = 10_000_000


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


@pytest.fixture
def upstream() -> FakeUpstream:
    fake = FakeUpstream(count=50)
    fake.events = text_stream(MODEL, "hello", output_tokens=20, input_tokens=60)
    return fake


@pytest.fixture
def tree() -> FakeTree:
    return FakeTree()


@pytest.fixture
def gateway(upstream, tree) -> Gateway:
    return Gateway(
        provider_key="not-a-key", connect=connect, tree=tree, client=upstream.client()
    )


@pytest.fixture
def client(gateway) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app(gateway)), base_url="http://gw"
    )


def body(**rest) -> dict:
    return {
        "model": MODEL,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
        **rest,
    }


async def mint(gateway, conn, space, *, generation=1, model_ref=MODEL, cap=None):
    brief = uuid.uuid4().hex
    token = await gateway.issue_token(
        conn,
        brief_id=brief,
        generation=generation,
        model_ref=model_ref,
        space=space,
        cap=cap,
    )
    await conn.commit()
    return brief, token.get_secret_value()


async def seat(gateway, tree, conn, space, *, usd_micros=PLENTY, generation=1):
    brief, plaintext = await mint(gateway, conn, space, generation=generation)
    tree.add(brief, usd_micros, generation=generation)
    return brief, {"x-api-key": plaintext}


async def rows(conn, brief, event=None):
    sql = (
        "SELECT event, reason, call, estimated_input, usage, stop_reason, cache_state "
        "FROM gateway_log WHERE brief_id = %s "
        "AND event NOT IN ('token_issued', 'token_revoked') ORDER BY id"
    )
    found = await (await conn.execute(sql, (brief,))).fetchall()
    return [r for r in found if event is None or r[0] == event]


# ---------------------------------------------------------------------------
# Forwarding


async def test_stream_relayed_byte_identical(gateway, upstream, client, kernel, space):
    brief, headers = await seat(gateway, gateway.tree, kernel, space)
    async with client.stream(
        "POST", "/v1/messages", json=body(stream=True), headers=headers
    ) as response:
        assert response.status_code == 200
        relayed = b"".join([chunk async for chunk in response.aiter_bytes()])

    assert relayed == sse(upstream.events)
    # Streaming is forced upstream whatever the client asked.
    assert upstream.message_calls[0]["stream"] is True


async def test_a_non_streaming_client_still_streams_upstream(
    gateway, upstream, client, kernel, space
):
    _, headers = await seat(gateway, gateway.tree, kernel, space)
    await client.post("/v1/messages", json=body(), headers=headers)
    assert upstream.message_calls[0]["stream"] is True


async def test_non_streaming_client_gets_assembled_message(
    gateway, client, kernel, space
):
    _, headers = await seat(gateway, gateway.tree, kernel, space)
    response = await client.post("/v1/messages", json=body(), headers=headers)

    assert response.status_code == 200
    message = response.json()
    assert message["content"] == [{"type": "text", "text": "hello"}]
    assert message["stop_reason"] == "end_turn"
    assert message["usage"]["output_tokens"] == 20


# ---------------------------------------------------------------------------
# The refusal ladder, one test per reason


async def test_refused_unknown_token(client, kernel):
    response = await client.post(
        "/v1/messages", json=body(), headers={"x-api-key": "cori-nope"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"

    found = await (
        await kernel.execute(
            "SELECT brief_id, generation, space_id FROM gateway_log "
            "WHERE event = 'refused' AND reason = 'unknown_token'"
        )
    ).fetchall()
    assert (None, None, None) in found


async def test_refused_revoked(gateway, upstream, client, kernel, space):
    brief, headers = await seat(gateway, gateway.tree, kernel, space)
    await gateway.revoke(kernel, brief)
    await kernel.commit()

    response = await client.post("/v1/messages", json=body(), headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["type"] == "permission_error"
    assert [r[:2] for r in await rows(kernel, brief)] == [("refused", "revoked")]
    assert upstream.message_calls == []


async def test_refused_model(gateway, client, kernel, space):
    brief, headers = await seat(gateway, gateway.tree, kernel, space)
    response = await client.post(
        "/v1/messages", json=body(model="claude-haiku-4-5-20251001"), headers=headers
    )
    assert response.status_code == 403
    assert [r[:2] for r in await rows(kernel, brief)] == [("refused", "model")]


async def test_refused_stale_generation(gateway, tree, client, kernel, space):
    brief, headers = await seat(gateway, tree, kernel, space, generation=1)
    tree.generations[brief] = 2  # the Brief was stopped and restarted

    response = await client.post("/v1/messages", json=body(), headers=headers)

    assert response.status_code == 403
    assert [r[:2] for r in await rows(kernel, brief)] == [
        ("refused", "stale_generation")
    ]


async def test_refused_budget(gateway, tree, client, kernel, space):
    """The reserve is the input at the cache write rate plus every output
    token the client allowed, so a Brief with less than that is refused."""
    brief, headers = await seat(gateway, tree, kernel, space, usd_micros=10)

    response = await client.post("/v1/messages", json=body(), headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "budget exhausted"
    row = (await rows(kernel, brief))[0]
    assert row[:2] == ("refused", "budget")
    assert row[3] == 50  # the exact count, not an estimate


async def test_budget_refusal_after_count_tokens_failure(
    gateway, upstream, tree, client, kernel, space
):
    """When the counting endpoint cannot be reached the failure is a row of
    its own and the fallback stands in for the exact count."""
    upstream.count_status = 500
    brief, headers = await seat(gateway, tree, kernel, space, usd_micros=10)

    response = await client.post("/v1/messages", json=body(), headers=headers)

    assert response.status_code == 403
    written = await rows(kernel, brief)
    assert [r[:2] for r in written] == [
        ("upstream_error", "count_tokens:500"),
        ("refused", "budget"),
    ]
    assert written[0][2] is None  # no call was made
    assert written[1][3] == len(canonical(body())) // 3 + 1


# ---------------------------------------------------------------------------
# The rows


async def test_request_row_durable_before_upstream(
    gateway, upstream, client, kernel, space
):
    brief, headers = await seat(gateway, gateway.tree, kernel, space)
    seen = {}

    async def check(payload):
        async with await connect() as conn:
            seen["rows"] = await (
                await conn.execute(
                    "SELECT call, request_sha256, estimated_input FROM gateway_log "
                    "WHERE brief_id = %s AND event = 'request'",
                    (brief,),
                )
            ).fetchall()
            seen["body"] = await (
                await conn.execute(
                    "SELECT body FROM request_bodies WHERE sha256 = %s",
                    (body_sha256(canonical(body())),),
                )
            ).fetchall()

    upstream.on_message = check
    await client.post("/v1/messages", json=body(), headers=headers)

    assert seen["rows"] == [(1, body_sha256(canonical(body())), 50)]
    assert seen["body"] == [[body()]] or seen["body"] == [(body(),)]


async def test_consume_called_incurred_with_billed(
    gateway, tree, client, kernel, space
):
    brief, headers = await seat(gateway, tree, kernel, space)
    await client.post("/v1/messages", json=body(), headers=headers)

    billed = cost(
        {"input_tokens": 60, "output_tokens": 20},
        PRICES,
    )
    assert tree.consumed == [
        (brief, type(tree.consumed[0][1])(usd_micros=billed), True)
    ]
    row = (await rows(kernel, brief, "response"))[0]
    usage = row[4]
    assert usage["usd_micros"] == billed
    assert usage["charged_reserved"] is False
    assert usage["input_tokens"] == 60 and usage["output_tokens"] == 20
    assert row[5] == "end_turn" and row[6] == "none"
    assert row[1] is None


async def test_overrun_recorded_in_full_and_row_marked(
    gateway, upstream, tree, client, kernel, space
):
    """Overrun is reachable only by estimate error: the count says nothing
    is being sent and the provider bills a million input tokens."""
    upstream.count = 0
    upstream.events = text_stream(MODEL, "hi", output_tokens=1, input_tokens=1_000_000)
    allowance = reserve(0, 1, PRICES).usd_micros
    brief, headers = await seat(gateway, tree, kernel, space, usd_micros=allowance)

    response = await client.post(
        "/v1/messages", json=body(max_tokens=1), headers=headers
    )
    assert response.status_code == 200

    billed = cost({"input_tokens": 1_000_000, "output_tokens": 1}, PRICES)
    assert tree.consumed[0][1].usd_micros == billed and tree.consumed[0][2] is True
    assert tree.overruns == [(brief, billed - allowance)]
    assert tree.allocations[brief] == 0
    row = (await rows(kernel, brief, "response"))[0]
    assert row[1] == "overrun" and row[4]["usd_micros"] == billed

    # And the next call is refused by the pre-check.
    again = await client.post("/v1/messages", json=body(max_tokens=1), headers=headers)
    assert again.status_code == 403
    assert (await rows(kernel, brief))[-1][:2] == ("refused", "budget")


async def test_upstream_429_relayed_and_logged(
    gateway, upstream, tree, client, kernel, space
):
    """The gateway never retries: a spend decision belongs to the caller."""
    upstream.status = 429
    upstream.error_body = b'{"type":"error","error":{"type":"rate_limit_error"}}'
    brief, headers = await seat(gateway, tree, kernel, space)

    response = await client.post("/v1/messages", json=body(), headers=headers)

    assert response.status_code == 429
    assert response.content == upstream.error_body
    assert [r[:3] for r in await rows(kernel, brief)] == [
        ("request", None, 1),
        ("upstream_error", "429", 1),
    ]
    assert tree.consumed == []


# ---------------------------------------------------------------------------
# The lock and the turn token


async def test_concurrent_calls_on_one_brief_serialize(
    gateway, upstream, client, kernel, space
):
    upstream.chunk_delay = 0.01
    brief, headers = await seat(gateway, gateway.tree, kernel, space)

    await asyncio.gather(
        *[client.post("/v1/messages", json=body(), headers=headers) for _ in range(3)]
    )

    assert upstream.most_concurrent == 1
    assert [r[2] for r in await rows(kernel, brief, "request")] == [1, 2, 3]


async def test_turn_token_meters_against_its_cap_and_calls_no_tree_function(
    gateway, upstream, tree, client, kernel, space
):
    """A turn id has no objective, so the fake tree raises `KeyError` for
    it: the call proceeds only because the gateway never asked."""
    cap_micros = reserve(50, 64, PRICES).usd_micros
    from schemas.budget import Budget

    turn, plaintext = await mint(
        gateway, kernel, space, generation=0, cap=Budget(usd_micros=cap_micros)
    )
    headers = {"x-api-key": plaintext}

    first = await client.post("/v1/messages", json=body(), headers=headers)
    assert first.status_code == 200
    assert tree.consumed == [] and tree.checks == []

    second = await client.post("/v1/messages", json=body(), headers=headers)
    assert second.status_code == 403
    written = await rows(kernel, turn)
    assert written[-1][:2] == ("refused", "budget")
    spent = (await rows(kernel, turn, "response"))[0][4]["usd_micros"]
    assert spent > 0


async def test_a_body_that_is_not_json_is_a_bad_request(client):
    response = await client.post(
        "/v1/messages", content=b"{", headers={"x-api-key": "cori-nope"}
    )
    assert response.status_code == 400


async def test_an_uncaught_error_in_start_still_releases_the_lock(
    gateway, upstream, tree, client, kernel, space
):
    """`start()` catches two httpx errors and releases the lock on both. Any
    other end, the `CancelledError` a client disconnect during the upstream
    connect raises included, has to release it too, or every later call for
    the brief waits forever."""
    brief, headers = await seat(gateway, tree, kernel, space)

    async def explode(payload):
        raise RuntimeError("upstream connect went wrong")

    upstream.on_message = explode
    with pytest.raises(RuntimeError):
        await client.post("/v1/messages", json=body(), headers=headers)

    assert gateway.lock(brief).locked() is False

    upstream.on_message = None
    second = await asyncio.wait_for(
        client.post("/v1/messages", json=body(), headers=headers), timeout=5
    )
    assert second.status_code == 200
    # No call ends without a closing row: nothing was generated, so the
    # abandoned call is an `upstream_error` and consumes nothing.
    assert [r[:2] for r in await rows(kernel, brief)] == [
        ("request", None),
        ("upstream_error", "connect"),
        ("request", None),
        ("response", None),
    ]
    assert len(tree.consumed) == 1
