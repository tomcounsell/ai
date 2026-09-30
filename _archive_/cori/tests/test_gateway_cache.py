"""The prefix rule. Plan 04 task 8; Properties P4.

The gateway never places a breakpoint and never refuses a call over one. It
judges what it sees: a prefix that changed on this call and the one before
it is changing every call, so its breakpoints are stripped from what goes
upstream and the request row says `unstable`. The body the log keeps is the
one the caller sent, breakpoints included, because the log is the trace of
what was asked, not of what was forwarded.
"""

import json
import uuid

import httpx
import psycopg
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gateway import budget as gwbudget
from gateway.app import _decide_cache, build_app
from gateway.core import Gateway
from tests.conftest import dsn, requires_postgres
from tests.gateway_fakes import FakeTree, FakeUpstream, text_stream

pytestmark = requires_postgres

MODEL = "claude-opus-5"
PLENTY = 100_000_000

PURE = settings(max_examples=2000, deadline=None)


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


# ---------------------------------------------------------------------------
# P4: the rule itself, over the history the call path keeps


class _State:
    """What `_decide_cache` reads and writes on a brief's state."""

    def __init__(self):
        self.prefixes: list[str] = []


class _Gateway:
    def __init__(self, state):
        self._state = state

    def state(self, brief_id):
        return self._state


def marked(letter: str | None) -> dict:
    """A body whose prefix hashes by `letter`, or one with no breakpoint."""
    if letter is None:
        return {
            "model": MODEL,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "plain"}],
        }
    return {
        "model": MODEL,
        "max_tokens": 8,
        "system": [
            {"type": "text", "text": letter, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": "plain"}],
    }


def expected(letters: list[str | None]) -> list[bool]:
    """The plan's rule, written from its own words: of the calls that carry
    breakpoints, the jth is stripped iff j >= 3 and g_j != g_{j-1} and
    g_{j-1} != g_{j-2}."""
    carrying = [x for x in letters if x is not None]
    stripped_by_index = []
    j = 0
    for letter in letters:
        if letter is None:
            stripped_by_index.append(False)
            continue
        stripped_by_index.append(
            j >= 2
            and carrying[j] != carrying[j - 1]
            and carrying[j - 1] != carrying[j - 2]
        )
        j += 1
    return stripped_by_index


@PURE
@given(
    st.lists(st.sampled_from(["A", "B", "C", None]), min_size=0, max_size=12),
)
def test_prefix_rule(letters):
    state = _State()
    gw = _Gateway(state)
    token = type("T", (), {"brief_id": "b"})()

    got = [_decide_cache(gw, token, marked(letter)) for letter in letters]

    assert got == expected(letters)
    assert len(state.prefixes) <= 2
    # A call without breakpoints neither reads nor changes the history.
    assert all(x is not None for x in state.prefixes) or state.prefixes == []


def test_a_change_around_a_call_without_breakpoints_strips_nothing():
    """The sequence the plan names: A, (none), B strips nothing."""
    state = _State()
    gw = _Gateway(state)
    token = type("T", (), {"brief_id": "b"})()
    got = [_decide_cache(gw, token, marked(x)) for x in ("A", None, "B")]
    assert got == [False, False, False]


def test_the_third_consecutive_change_strips():
    state = _State()
    gw = _Gateway(state)
    token = type("T", (), {"brief_id": "b"})()
    got = [_decide_cache(gw, token, marked(x)) for x in ("A", "B", "C", "C", "D")]
    assert got == [False, False, True, False, False]


# ---------------------------------------------------------------------------
# Through the call path


@pytest.fixture
def upstream() -> FakeUpstream:
    fake = FakeUpstream(count=40)
    fake.events = text_stream(MODEL, "hi", output_tokens=5, input_tokens=40)
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


async def seat(gateway, tree, conn, space):
    brief = uuid.uuid4().hex
    token = await gateway.issue_token(
        conn, brief_id=brief, generation=1, model_ref=MODEL, space=space
    )
    await conn.commit()
    tree.add(brief, PLENTY)
    return brief, {"x-api-key": token.get_secret_value()}


async def request_rows(brief):
    async with await connect() as conn:
        return await (
            await conn.execute(
                "SELECT call, cache_state, request_sha256 FROM gateway_log "
                "WHERE brief_id = %s AND event = 'request' ORDER BY call",
                (brief,),
            )
        ).fetchall()


async def stored_body(sha256: str) -> dict:
    async with await connect() as conn:
        row = await (
            await conn.execute(
                "SELECT body FROM request_bodies WHERE sha256 = %s", (sha256,)
            )
        ).fetchone()
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


async def test_a_body_without_breakpoints_forwards_byte_identical(
    gateway, upstream, tree, client, kernel, space
):
    brief, headers = await seat(gateway, tree, kernel, space)
    sent = marked(None)

    await client.post("/v1/messages", json=sent, headers=headers)
    await gateway.drain()

    assert upstream.message_calls == [{**sent, "stream": True}]
    assert [r[1] for r in await request_rows(brief)] == ["none"]


async def test_the_stored_body_keeps_the_breakpoints_the_forwarded_one_lost(
    gateway, upstream, tree, client, kernel, space
):
    brief, headers = await seat(gateway, tree, kernel, space)

    for letter in ("A", "B", "C"):
        await client.post("/v1/messages", json=marked(letter), headers=headers)
        await gateway.drain()

    forwarded = upstream.message_calls[-1]
    assert "cache_control" not in json.dumps(forwarded)
    assert forwarded == {**gwbudget.strip_breakpoints(marked("C")), "stream": True}

    rows = await request_rows(brief)
    # Seams 5.1: a `request` row says `none` or `unstable`, never `write`.
    assert [r[1] for r in rows] == ["none", "none", "unstable"]
    kept = await stored_body(rows[-1][2])
    assert kept == marked("C")
    assert kept["system"][0]["cache_control"] == {"type": "ephemeral"}


async def test_the_response_row_reads_its_cache_state_from_usage(
    gateway, upstream, tree, client, kernel, space
):
    upstream.events = text_stream(
        MODEL, "hi", output_tokens=5, input_tokens=2, cache_read_input_tokens=38
    )
    brief, headers = await seat(gateway, tree, kernel, space)

    await client.post("/v1/messages", json=marked("A"), headers=headers)
    await gateway.drain()

    async with await connect() as conn:
        rows = await (
            await conn.execute(
                "SELECT cache_state FROM gateway_log WHERE brief_id = %s "
                "AND event = 'response'",
                (brief,),
            )
        ).fetchall()
    assert [r[0] for r in rows] == ["read"]
