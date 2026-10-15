"""The pipeline end to end on real Postgres and real git, with scripted
turns (`tests/scripted.py`): a task built, its checks passed, merged onto
its origin; and a red test sent to patch, the patched candidate merged.

No model call. Live spend: none.
"""

import asyncio

import pytest

from core import (
    broker,
    db,
    ledger,
    machine,
)
from core.gateway import Gateway
from core.machine import State
from tests import scripted
from tests.ports import listen
from tests.scripted import git

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


async def drive(dsn, task, runners=None) -> dict:
    gateway = Gateway(dsn)
    await gateway.start(port=listen())
    try:
        return await scripted.route(gateway, task, runners or scripted.RUNNERS, dsn=dsn)
    finally:
        await gateway.close()


async def to_checks(dsn, ws, **kw) -> str:
    """A task with a candidate waiting on its three checks."""
    task = await scripted.start(dsn, ws, **kw)
    await drive(dsn, task)
    await scripted.critique(dsn, task)
    out = await drive(dsn, task)
    assert out["missing"] == ["test", "review", "docs"], out
    return task


async def rows(dsn, task) -> list[dict]:
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task)


async def fold(dsn, task) -> machine.Fold:
    return machine.fold(await rows(dsn, task))


async def merged(dsn, task) -> broker.Outcome:
    async with await db.connect(dsn) as conn:
        return await scripted.merged(conn, task)


def test_a_passing_candidate_merges_onto_the_recorded_origin(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        return await merged(dsn, task), await fold(dsn, task)

    done, f = run(go())
    assert done.kind == "done" and git(origin, "rev-parse", "main") == f.candidate.sha
    assert f.state is State.MERGED


def test_a_red_test_goes_to_patch_and_the_patched_candidate_merges(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        first = (await fold(dsn, task)).candidate
        await scripted.checks(dsn, task, test="red")  # join row 3: the plan's round
        patching = await fold(dsn, task)
        await drive(dsn, task)
        await scripted.checks(dsn, task)
        return first, patching, await merged(dsn, task), await fold(dsn, task)

    first, patching, done, f = run(go())
    assert patching.state is State.PATCH and patching.join.row == 3
    assert f.candidate != first and f.state is State.MERGED
    assert done.kind == "done" and git(origin, "rev-parse", "main") == f.candidate.sha
