"""upcast walks the registry to the current version; read returns the
result. Tech stack §10; plan 01 P5."""

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings, strategies as st

from kernel.events import (
    UPCASTERS,
    StaleSchemaVersion,
    UnknownSchemaVersion,
    append,
    read,
    read_for,
    upcast,
)
from schemas.events import CURRENT_VERSION, Event
from tests.conftest import requires_postgres

TYPE = "message.sent"


def event_at(version: int, payload=None) -> Event:
    return Event(
        id=1,
        space_id="s",
        type=TYPE,
        schema_version=version,
        occurred_at=datetime.now(UTC),
        payload={"steps": []} if payload is None else payload,
    )


def step(n: int):
    return lambda payload: {**payload, "steps": [*payload["steps"], n]}


@contextmanager
def synthetic_chain(length: int, *, register=None):
    """Steps 1..length on TYPE, current version length + 1; undone on exit.
    `register` limits which steps are registered, for the missing-step test."""
    saved = CURRENT_VERSION[TYPE]
    steps = range(1, length + 1) if register is None else register
    try:
        for n in steps:
            UPCASTERS[(TYPE, n)] = step(n)
        CURRENT_VERSION[TYPE] = length + 1
        yield
    finally:
        CURRENT_VERSION[TYPE] = saved
        for n in range(1, length + 1):
            UPCASTERS.pop((TYPE, n), None)


@pytest.fixture
def chain_of_two():
    with synthetic_chain(2):
        yield


def test_upcast_is_identity_at_current_version():
    assert not UPCASTERS
    e = event_at(CURRENT_VERSION[TYPE], {"k": "v"})
    assert upcast(e) is e


@settings(max_examples=50, deadline=None)
@given(st.integers(1, 4).flatmap(lambda n: st.tuples(st.just(n), st.integers(1, n))))
def test_upcast_chains_registered_steps_in_order(case):
    """P5: the payload collects the step numbers from the event's version to
    the end, in order; upcast is idempotent; a future version raises."""
    length, start = case
    with synthetic_chain(length):
        got = upcast(event_at(start))
        assert got.schema_version == CURRENT_VERSION[TYPE] == length + 1
        assert got.payload["steps"] == list(range(start, length + 1))
        assert upcast(got) == got
        with pytest.raises(UnknownSchemaVersion):
            upcast(event_at(length + 2))


def test_upcast_refuses_missing_step():
    with synthetic_chain(2, register=[1]):
        with pytest.raises(UnknownSchemaVersion) as info:
            upcast(event_at(1))
        assert "from 2 to 3" in str(info.value)
        with pytest.raises(UnknownSchemaVersion):
            upcast(event_at(2))


def test_upcast_refuses_future_version():
    with pytest.raises(UnknownSchemaVersion):
        upcast(event_at(CURRENT_VERSION[TYPE] + 1))


@requires_postgres
async def test_read_returns_upcast_events(kernel, space, chain_of_two):
    """A row stored at version 1 while the type is at 3 comes back at 3."""
    await kernel.execute(
        "INSERT INTO events (space_id, type, schema_version, payload) "
        "VALUES (%s, %s, 1, %s)",
        (space, TYPE, '{"steps": [], "objective_id": "o"}'),
    )
    await kernel.commit()
    for events in (
        await read(kernel, space_id=space),
        await read_for(kernel, space_id=space, key="objective_id", value="o"),
    ):
        assert len(events) == 1
        assert events[0].schema_version == 3
        assert events[0].payload["steps"] == [1, 2]
    with pytest.raises(StaleSchemaVersion):
        await append(kernel, space_id=space, type=TYPE, payload={}, schema_version=1)
