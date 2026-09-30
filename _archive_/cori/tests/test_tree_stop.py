"""A stop fences, revokes, and waits for the receipt. Plan 02 task 9."""

import pytest

import kernel.tree as tree
from kernel.events import read_for
from schemas.brief import BriefToken
from schemas.budget import ZERO, Budget
from tests.conftest import requires_postgres
from tests.tree_fakes import (
    approval,
    bind,
    contract,
    make_space,
    receipt,
    request,
    token,
)

pytestmark = requires_postgres


@pytest.fixture
def bound(space, monkeypatch):
    return bind(monkeypatch, {space: make_space(space, max_effect_class="act")})


async def running_with_two(kernel, space, budget=5000):
    oid = await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract(budget=budget)
    )
    await tree.approve(kernel, oid, approval("approved", oid, 1))
    executor = await tree.delegate(
        kernel,
        request(oid, "Executor", space=space, budget=1000),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    scribe = await tree.delegate(
        kernel,
        request(oid, "Scribe", space=space, budget=500),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    return oid, executor, scribe


async def events_of(kernel, space, oid, type):
    return [
        e
        for e in await read_for(kernel, space_id=space, key="objective_id", value=oid)
        if e.type == type
    ]


async def ledger(kernel, space):
    cur = await kernel.execute(
        "SELECT node_id, brief_id, kind, usd_micros FROM budget_ledger "
        "WHERE space_id = %s ORDER BY id",
        (space,),
    )
    return await cur.fetchall()


async def test_stop_fences_both_and_delegate_waits_for_the_receipts(
    kernel, space, bound
):
    issuer, _ = bound
    oid, executor, scribe = await running_with_two(kernel, space)
    stopped = await tree.stop(kernel, oid, "stopped_by_person")
    assert set(stopped) == {executor.id, scribe.id}
    assert issuer.revoked == [(kernel, executor.id), (kernel, scribe.id)]
    node = await tree.project(kernel, oid)
    assert node.generation == 3
    assert node.owner_brief is None
    assert node.state == "RUNNING"  # the state is the caller's to change
    assert [
        e.payload["new_generation"]
        for e in await events_of(kernel, space, oid, "brief.stopped")
    ] == [2, 3]
    for brief in (executor, scribe):
        with pytest.raises(tree.StaleGeneration):
            await tree.check_generation(kernel, token(brief))
    with pytest.raises(tree.StaleGeneration):
        await tree.check_generation(kernel, BriefToken(brief_id="nobody", generation=9))

    fresh = request(oid, "Executor", space=space, budget=1000)
    with pytest.raises(tree.Unconfirmed):
        await tree.delegate(kernel, fresh, issuer=frozenset(), parent_brief="turn-2")
    await tree.confirm_stop(kernel, executor.id, receipt())
    with pytest.raises(tree.Unconfirmed):
        await tree.delegate(kernel, fresh, issuer=frozenset(), parent_brief="turn-2")
    await tree.confirm_stop(kernel, scribe.id, receipt())
    with pytest.raises(tree.NotLive):
        await tree.confirm_stop(kernel, scribe.id, receipt())
    replacement = await tree.delegate(
        kernel, fresh, issuer=frozenset(), parent_brief="turn-2"
    )
    assert replacement.generation == 3
    await tree.check_generation(kernel, token(replacement))
    assert issuer.issued[-1]["generation"] == 3
    confirmed = await events_of(kernel, space, oid, "brief.stop_confirmed")
    assert [e.payload["brief_id"] for e in confirmed] == [executor.id, scribe.id]
    assert confirmed[0].payload["receipt"]["probe"] == "ps"
    await kernel.commit()


async def test_a_stopped_brief_still_records_spend_and_releases_once(
    kernel, space, bound
):
    oid, executor, scribe = await running_with_two(kernel, space)
    await tree.consume(kernel, executor.id, Budget(usd_micros=300), incurred=True)
    await tree.stop(kernel, oid, "stopped_by_person")
    assert (
        await tree.consume(kernel, executor.id, Budget(usd_micros=200), incurred=True)
    ) == Budget(usd_micros=500)
    await tree.confirm_stop(kernel, executor.id, receipt())
    assert await tree.remaining(kernel, oid) == Budget(usd_micros=3500)
    assert await tree.release(kernel, executor.id) == Budget(usd_micros=500)
    assert await tree.remaining(kernel, oid) == Budget(usd_micros=4000)
    assert await tree.release(kernel, executor.id) == ZERO
    assert [r[2] for r in await ledger(kernel, space) if r[1] == executor.id] == [
        "allocate",
        "consume",
        "consume",
        "release",
    ]
    await kernel.commit()


async def test_stop_brief_on_a_root_scribe_and_on_a_turn(kernel, space, bound):
    issuer, _ = bound
    root = await tree.delegate(
        kernel,
        request(None, "Scribe", space=space, budget=300, max_data_class="OPERATOR"),
        issuer=frozenset(),
        parent_brief="turn-9",
    )
    await tree.check_generation(kernel, token(root))
    await tree.stop_brief(kernel, root.id, "stopped_by_person")
    assert issuer.revoked == [(kernel, root.id)]
    with pytest.raises(tree.StaleGeneration):
        await tree.check_generation(kernel, token(root))
    with pytest.raises(tree.NotLive):
        await tree.stop_brief(kernel, root.id, "stopped_by_person")
    assert await tree.release(kernel, root.id) == ZERO
    assert await ledger(kernel, space) == [(root.id, None, "grant", 300)]
    (stopped,) = [
        e
        for e in await read_for(kernel, space_id=space, key="brief_id", value=root.id)
        if e.type == "brief.stopped"
    ]
    assert stopped.payload == {
        "objective_id": None,
        "brief_id": root.id,
        "reason": "stopped_by_person",
        "new_generation": 2,
    }
    with pytest.raises(tree.NotLive):
        await tree.stop_brief(kernel, "turn-9", "stopped_by_person")
    with pytest.raises(tree.NotLive):
        await tree.confirm_stop(kernel, "turn-9", receipt())
    await kernel.commit()


async def test_stop_brief_on_one_brief_of_a_node(kernel, space, bound):
    issuer, _ = bound
    oid, executor, scribe = await running_with_two(kernel, space)
    await tree.stop_brief(kernel, scribe.id, "stopped_by_person")
    assert issuer.revoked == [(kernel, scribe.id)]
    node = await tree.project(kernel, oid)
    assert node.generation == 2
    assert node.owner_brief == executor.id
    with pytest.raises(tree.StaleGeneration):
        await tree.check_generation(kernel, token(executor))
    with pytest.raises(tree.NotLive):
        await tree.stop_brief(kernel, scribe.id, "stopped_by_person")
    await kernel.commit()


async def test_transition_revokes_through_the_bound_default(kernel, space, bound):
    issuer, _ = bound
    oid, executor, scribe = await running_with_two(kernel, space)
    stopped = await tree.transition(kernel, oid, "CANCELLED", "stopped_by_person")
    assert set(stopped) == {executor.id, scribe.id}
    assert {b for _, b in issuer.revoked} == {executor.id, scribe.id}
    node = await tree.project(kernel, oid)
    assert (node.state, node.state_reason, node.generation) == (
        "CANCELLED",
        "stopped_by_person",
        3,
    )
    await kernel.commit()
