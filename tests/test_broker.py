"""The broker performs a merge only when the merge predicate holds, through
the real pipeline with scripted sessions (`tests/scripted.py`): a passing
candidate's merge lands, with one outcome on its effect; a merge
requested for a candidate whose test is red is refused, naming the terms
that do not hold, and writes no intent.

Live spend: none.
"""

import pytest

from core import db, machine, tasks, verdicts
from core.machine import State
from tests import scripted
from tests.scripted import git
from tests.test_pipeline import fold, merged, rows, run, to_checks

pytestmark = pytest.mark.spend(usd=0)

PREDICATE = "the merge predicate does not hold: "


def test_a_merge_the_predicate_allows_is_performed_once(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        effect = (await fold(dsn, task)).merge_effect["effect_id"]
        return effect, await merged(dsn, task), await rows(dsn, task)

    effect, done, written = run(go())
    f = machine.fold(written)
    assert done.kind == "done" and done.effect_id == effect and f.state is State.MERGED
    assert git(origin, "rev-parse", "main") == f.candidate.sha
    outcomes = [r["payload"] for r in written if r["type"] == "effect.outcome"]
    assert [o["kind"] for o in outcomes if o["effect_id"] == effect] == ["done"]


def test_a_merge_the_predicate_refuses_is_not_performed(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)
    before = git(origin, "rev-parse", "main")

    async def go():
        task = await to_checks(dsn, ws, max_effect_class="act")
        await scripted.checks(dsn, task, test="red")
        f = await fold(dsn, task)
        async with await db.connect(dsn) as conn:
            refused = await scripted.request(
                conn, task, verdicts.merge_action(f, await tasks.brief(conn, task))
            )
        return f, refused, await rows(dsn, task)

    f, refused, written = run(go())
    assert f.state is State.PATCH and f.merge_effect is None
    assert refused.kind == "refused" and refused.error.startswith(PREDICATE), refused
    assert [t[0] for t in refused.error.removeprefix(PREDICATE).split("; ")] == ["1", "3"]
    assert not [r for r in written if r["type"] == "effect.intent"]
    assert git(origin, "rev-parse", "main") == before
