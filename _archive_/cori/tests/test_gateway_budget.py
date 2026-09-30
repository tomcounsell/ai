"""Money and the input estimate. Plan 04 task 5.

P6, cost never undercharges: a charge rounded up per usage field is never
less than what the provider's own arithmetic gives, because the ledger that
records less than the invoice is the one that lets a Brief overspend
silently.
"""

import uuid
from decimal import Decimal
from math import ceil

import psycopg
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gateway.budget import (
    appended_estimate,
    canonical,
    cost,
    estimate_input,
    fallback_estimate,
    is_prefix,
    reserve,
)
from gateway.core import ANCHOR_EVERY, BriefState, Gateway
from infra.models import PRICE_OF_FIELD, load
from schemas.gateway import Usage
from tests.conftest import dsn, requires_postgres
from tests.gateway_fakes import FakeUpstream

MODEL = "claude-opus-5"
PRICES = load().model("frontier").usd_micros_per_mtok


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


def body(messages, **rest) -> dict:
    return {"model": MODEL, "max_tokens": 1024, "messages": messages, **rest}


def user(text: str) -> dict:
    return {"role": "user", "content": text}


# ---------------------------------------------------------------------------
# P6, cost never undercharges


@settings(max_examples=2000, deadline=None)
@given(
    counts=st.fixed_dictionaries(
        {field: st.integers(0, 2_000_000) for field in PRICE_OF_FIELD}
    ),
    prices=st.fixed_dictionaries(
        {
            price: st.integers(1, 100_000_000)
            for price in ("input", "output", "cache_write", "cache_read")
        }
    ),
)
def test_cost_never_undercharges(counts, prices):
    exact = sum(
        Decimal(counts[field]) * prices[price] / 1_000_000
        for field, price in PRICE_OF_FIELD.items()
    )
    charged = cost(counts, prices)
    assert charged >= exact
    # And never by more than a rounded-up micro-dollar per field.
    assert charged - exact < len(PRICE_OF_FIELD)


def test_cost_reads_a_usage_the_same_way_as_a_dict():
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        usd_micros=0,
        charged_reserved=False,
    )
    assert cost(usage, PRICES) == PRICES["input"] + PRICES["output"]


def test_reserve_prices_input_at_the_cache_write_rate():
    """The most expensive input rate a call can bill, so a forwarded call is
    never cheaper than its reservation."""
    assert reserve(1_000_000, 1_000_000, PRICES).usd_micros == (
        PRICES["cache_write"] + PRICES["output"]
    )


# ---------------------------------------------------------------------------
# The estimate


def test_a_later_call_that_appends_estimates_as_billed_plus_bytes_over_three():
    first = body([user("one")])
    second = body([user("one"), user("two")])
    appended = len(canonical(user("two")))
    assert estimate_input(second, prev_body=first, prev_billed_input=400) == (
        400 + ceil(appended / 3)
    )
    assert appended_estimate(first, second, 400) == 400 + ceil(appended / 3)


def test_a_changed_system_re_anchors():
    first = body([user("one")], system="a")
    second = body([user("one"), user("two")], system="b")
    assert not is_prefix(first, second)
    assert estimate_input(second, prev_body=first, prev_billed_input=400) is None


def test_a_shortened_messages_list_re_anchors():
    """A compacted or edited history is not an append."""
    first = body([user("one"), user("two")])
    second = body([user("one")])
    assert not is_prefix(first, second)
    assert estimate_input(second, prev_body=first, prev_billed_input=400) is None


def test_an_edited_message_re_anchors():
    first = body([user("one"), user("two")])
    second = body([user("one"), user("edited"), user("three")])
    assert estimate_input(second, prev_body=first, prev_billed_input=400) is None


def test_the_first_call_of_a_brief_has_nothing_to_estimate_from():
    assert (
        estimate_input(body([user("one")]), prev_body=None, prev_billed_input=None)
        is None
    )


def test_key_order_is_not_a_change():
    assert is_prefix(
        {"messages": [{"role": "user", "content": "x"}], "system": "s"},
        {"system": "s", "messages": [{"content": "x", "role": "user"}, user("next")]},
    )


def test_the_fallback_is_the_whole_body_over_three():
    call = body([user("one")])
    assert fallback_estimate(call) == ceil(len(canonical(call)) / 3)


# ---------------------------------------------------------------------------
# Against the counting endpoint

pytestmark = requires_postgres


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream(count=1234)


@pytest.fixture
def gateway(upstream) -> Gateway:
    return Gateway(provider_key="not-a-key", connect=connect, client=upstream.client())


async def token_for(gateway, conn, space, model_ref=MODEL):
    brief = uuid.uuid4().hex
    plaintext = await gateway.issue_token(
        conn, brief_id=brief, generation=1, model_ref=model_ref, space=space
    )
    await conn.commit()
    return await gateway.token(conn, plaintext.get_secret_value())


async def test_a_first_call_is_counted_by_count_tokens(
    gateway, upstream, kernel, space
):
    token = await token_for(gateway, kernel, space)
    state = BriefState()
    estimate, exact = await gateway.estimate_input(
        kernel, token=token, body=body([user("one")]), state=state
    )
    assert (estimate, exact) == (1234, True)
    assert upstream.count_calls[0]["model"] == MODEL
    assert "max_tokens" not in upstream.count_calls[0]


async def test_every_tenth_call_re_anchors(gateway, upstream, kernel, space):
    token = await token_for(gateway, kernel, space)
    first = body([user("one")])
    state = BriefState(prev_body=first, prev_billed_input=400, calls_since_anchor=1)
    second = body([user("one"), user("two")])

    estimate, exact = await gateway.estimate_input(
        kernel, token=token, body=second, state=state
    )
    assert not exact and estimate != 1234 and upstream.count_calls == []

    state.calls_since_anchor = ANCHOR_EVERY
    estimate, exact = await gateway.estimate_input(
        kernel, token=token, body=second, state=state
    )
    assert (estimate, exact) == (1234, True)


async def test_an_anchor_is_forced_before_a_refusal(gateway, upstream, kernel, space):
    token = await token_for(gateway, kernel, space)
    state = BriefState(prev_body=body([user("one")]), prev_billed_input=400)
    estimate, exact = await gateway.estimate_input(
        kernel,
        token=token,
        body=body([user("one"), user("two")]),
        state=state,
        anchor=True,
    )
    assert (estimate, exact) == (1234, True)


async def test_a_count_tokens_failure_logs_upstream_error_and_falls_back(
    gateway, upstream, kernel, space
):
    """The row carries `call` null: no call was made, and the failure is
    still worth seeing (plan 04, step 3)."""
    upstream.count_status = 503
    token = await token_for(gateway, kernel, space)
    call = body([user("one")])

    estimate, exact = await gateway.estimate_input(
        kernel, token=token, body=call, state=BriefState()
    )
    await kernel.commit()

    assert (estimate, exact) == (fallback_estimate(call), False)
    rows = await (
        await kernel.execute(
            "SELECT event, reason, call FROM gateway_log "
            "WHERE brief_id = %s AND event = 'upstream_error'",
            (token.brief_id,),
        )
    ).fetchall()
    assert rows == [("upstream_error", "count_tokens:503", None)]
