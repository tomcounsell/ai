"""Only a person raises the root. Plan 02 task 7."""

import pytest

import kernel.tree as tree
from schemas.budget import Budget
from schemas.ids import new_id
from tests.conftest import requires_postgres
from tests.tree_fakes import approval, bind, contract, make_space, mint

pytestmark = requires_postgres


@pytest.fixture
def spaces(space, monkeypatch):
    spaces = {space: make_space(space, max_effect_class="act")}
    bind(monkeypatch, spaces)
    return spaces


def micros(n: int) -> Budget:
    return Budget(usd_micros=n)


async def opened(kernel, space, budget=1000):
    return await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract(budget=budget)
    )


async def rows(kernel, oid):
    cur = await kernel.execute(
        "SELECT node_id, brief_id, kind, usd_micros, approval_id FROM budget_ledger "
        "WHERE node_id = %s ORDER BY id",
        (oid,),
    )
    return await cur.fetchall()


async def test_an_approved_record_writes_one_grant_citing_it(kernel, space, spaces):
    oid = await opened(kernel, space)
    aid = await mint(kernel, approval("approved", oid, 1, space=space))
    await tree.raise_budget(kernel, oid, by=micros(250), approval_id=aid)
    assert await rows(kernel, oid) == [(oid, None, "grant", 250, aid)]
    assert await tree.remaining(kernel, oid) == micros(1250)
    await kernel.commit()


@pytest.mark.parametrize("kind", ["self_approved", "rejected"])
async def test_other_kinds_raise_and_write_nothing(kernel, space, spaces, kind):
    oid = await opened(kernel, space)
    aid = await mint(kernel, approval(kind, oid, 1, space=space))
    with pytest.raises(tree.Refused):
        await tree.raise_budget(kernel, oid, by=micros(250), approval_id=aid)
    assert await rows(kernel, oid) == []
    assert await tree.remaining(kernel, oid) == micros(1000)
    await kernel.commit()


async def test_unknown_and_foreign_approvals_are_refused(kernel, space, spaces):
    oid = await opened(kernel, space)
    other = await opened(kernel, space)
    with pytest.raises(tree.Refused):
        await tree.raise_budget(kernel, oid, by=micros(1), approval_id="approval-x")
    aid = await mint(kernel, approval("approved", other, 1, space=space))
    with pytest.raises(tree.Refused):
        await tree.raise_budget(kernel, oid, by=micros(1), approval_id=aid)
    assert await rows(kernel, oid) == []
    await kernel.commit()


async def test_a_raise_lets_the_allocation_that_did_not_fit_fit(kernel, space, spaces):
    oid = await opened(kernel, space)
    await tree._allocate(kernel, oid, new_id(), micros(900))
    with pytest.raises(tree.BudgetExceeded):
        await tree._allocate(kernel, oid, new_id(), micros(200))
    before = await tree.project(kernel, oid)
    aid = await mint(kernel, approval("approved", oid, 1, space=space))
    await tree.raise_budget(kernel, oid, by=micros(100), approval_id=aid)
    after = await tree.project(kernel, oid)
    assert after.budget_allocated == before.budget_allocated == micros(900)
    assert after.budget_consumed == before.budget_consumed
    assert await tree._allocate(kernel, oid, new_id(), micros(200)) == micros(0)
    assert (await tree.project(kernel, oid)).budget_allocated == micros(1100)
    await kernel.commit()
