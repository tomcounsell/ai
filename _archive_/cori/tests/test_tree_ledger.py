"""The ledger's writes, one row of a fixed shape each. Plan 02 task 6."""

import pytest

import kernel.tree as tree
from kernel.events import read_for
from schemas.budget import ZERO, Budget
from schemas.ids import new_id
from tests.conftest import requires_postgres
from tests.tree_fakes import bind, contract, make_space

pytestmark = requires_postgres


@pytest.fixture
def spaces(space, monkeypatch):
    spaces = {space: make_space(space, max_effect_class="act")}
    bind(monkeypatch, spaces)
    return spaces


async def opened(kernel, space, budget=1_000_000):
    return await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract(budget=budget)
    )


async def rows(kernel, space):
    cur = await kernel.execute(
        "SELECT node_id, brief_id, kind, usd_micros, approval_id FROM budget_ledger "
        "WHERE space_id = %s ORDER BY id",
        (space,),
    )
    return await cur.fetchall()


async def overruns(kernel, space, brief_id):
    events = await read_for(kernel, space_id=space, key="brief_id", value=brief_id)
    return [e for e in events if e.type == "budget.overrun"]


def micros(n: int) -> Budget:
    return Budget(usd_micros=n)


async def test_every_write_has_the_row_shape(kernel, space, spaces):
    oid = await opened(kernel, space)
    brief = new_id()
    assert await tree._allocate(kernel, oid, brief, micros(300)) == micros(999_700)
    assert await tree.consume(kernel, brief, micros(100)) == micros(200)
    assert await tree.release(kernel, brief) == micros(200)
    scribe = new_id()
    await tree._grant(kernel, space=space, node_id=scribe, amount=micros(50))
    assert await tree.consume(kernel, scribe, micros(20)) == micros(30)
    assert await rows(kernel, space) == [
        (oid, brief, "allocate", 300, None),
        (oid, brief, "consume", 100, None),
        (oid, brief, "release", 200, None),
        (scribe, None, "grant", 50, None),
        (scribe, scribe, "consume", 20, None),
    ]
    await kernel.commit()


async def test_allocate_refuses_past_remaining_and_writes_nothing(
    kernel, space, spaces
):
    oid = await opened(kernel, space, budget=1000)
    await tree._allocate(kernel, oid, new_id(), micros(600))
    with pytest.raises(tree.BudgetExceeded):
        await tree._allocate(kernel, oid, new_id(), micros(401))
    assert await tree._allocate(kernel, oid, new_id(), micros(400)) == ZERO
    assert len(await rows(kernel, space)) == 2
    with pytest.raises(tree.NotLive):
        await tree._allocate(kernel, "nobody", new_id(), micros(1))
    await kernel.commit()


async def test_consume_not_incurred_past_zero_raises_and_writes_nothing(
    kernel, space, spaces
):
    oid = await opened(kernel, space)
    brief = new_id()
    await tree._allocate(kernel, oid, brief, micros(100))
    await tree.consume(kernel, brief, micros(60))
    with pytest.raises(tree.BudgetExceeded):
        await tree.consume(kernel, brief, micros(41))
    assert [r[2] for r in await rows(kernel, space)] == ["allocate", "consume"]
    assert await tree.remaining(kernel, brief) == micros(40)
    assert await overruns(kernel, space, brief) == []
    await kernel.commit()


async def test_consume_incurred_past_zero_records_the_overrun(kernel, space, spaces):
    oid = await opened(kernel, space)
    brief = new_id()
    await tree._allocate(kernel, oid, brief, micros(100))
    assert await tree.consume(kernel, brief, micros(70), incurred=True) == micros(30)
    assert await tree.consume(kernel, brief, micros(50), incurred=True) == ZERO
    assert [r[2:4] for r in await rows(kernel, space)] == [
        ("allocate", 100),
        ("consume", 70),
        ("consume", 50),
    ]
    (overrun,) = await overruns(kernel, space, brief)
    assert overrun.payload == {
        "objective_id": oid,
        "brief_id": brief,
        "amount": {"usd_micros": 50},
        "shortfall": {"usd_micros": 20},
    }
    # money in an event is a Budget, never a bare integer (seams 1.8)
    assert Budget.model_validate(overrun.payload["shortfall"]) == micros(20)
    assert await tree.remaining(kernel, brief) == ZERO
    with pytest.raises(tree.BudgetExceeded):
        await tree.consume(kernel, brief, micros(1))
    # incurred spend past an exhausted Brief is still recorded, in full
    assert await tree.consume(kernel, brief, micros(5), incurred=True) == ZERO
    assert (await overruns(kernel, space, brief))[-1].payload["shortfall"] == {
        "usd_micros": 5
    }
    assert (await tree.project(kernel, oid)).budget_consumed == micros(125)
    await kernel.commit()


async def test_remaining_is_the_contract_budget_minus_live_allocations(
    kernel, space, spaces
):
    oid = await opened(kernel, space, budget=1000)
    assert await tree.remaining(kernel, oid) == micros(1000)
    a, b = new_id(), new_id()
    await tree._allocate(kernel, oid, a, micros(300))
    await tree._allocate(kernel, oid, b, micros(200))
    assert await tree.remaining(kernel, oid) == micros(500)
    await tree.consume(kernel, a, micros(120))
    assert await tree.remaining(kernel, oid) == micros(500)
    assert await tree.release(kernel, a) == micros(180)
    assert await tree.remaining(kernel, oid) == micros(680)
    node = await tree.project(kernel, oid)
    assert node.budget_allocated == micros(320)
    assert node.budget_consumed == micros(120)
    with pytest.raises(tree.NotLive):
        await tree.remaining(kernel, "nobody")
    await kernel.commit()


async def test_release_twice_root_scribe_and_objective(kernel, space, spaces):
    oid = await opened(kernel, space)
    brief = new_id()
    await tree._allocate(kernel, oid, brief, micros(100))
    assert await tree.release(kernel, brief) == micros(100)
    assert await tree.release(kernel, brief) == ZERO
    assert await tree.remaining(kernel, brief) == ZERO
    with pytest.raises(tree.BudgetExceeded):
        await tree.consume(kernel, brief, micros(1))
    scribe = new_id()
    await tree._grant(kernel, space=space, node_id=scribe, amount=micros(50))
    assert await tree.release(kernel, scribe) == ZERO
    assert await tree.remaining(kernel, scribe) == micros(50)
    with pytest.raises(tree.Refused):
        await tree.release(kernel, oid)
    with pytest.raises(tree.NotLive):
        await tree.consume(kernel, oid, micros(1))
    assert [r[2] for r in await rows(kernel, space)] == [
        "allocate",
        "release",
        "grant",
    ]
    await kernel.commit()
