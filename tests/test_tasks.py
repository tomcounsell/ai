"""`tasks.index`, the status page's task list, on real Postgres: each task
carries what came after its merges, with no git. No model call."""

import pytest

from core import db, ledger, outcomes, tasks
from tests.test_objective_tree import run

pytestmark = pytest.mark.spend(usd=0)


async def merge(conn, task: str, url: str, head: str, paths: list[str]) -> None:
    effect = ledger.new_id()
    payload = {"url": url, "target_branch": "main", "head_sha": head, "candidate": head}
    await ledger.append(
        conn, task, "effect.held", {"effect_id": effect, "action_type": "merge", "payload": payload}
    )
    landed = {"before": None, "commits": [head], "paths": paths, "why": None}
    await ledger.append(
        conn, task, "effect.intent", {"effect_id": effect, "action_type": "merge", "landed": landed}
    )
    await ledger.append(conn, task, "effect.outcome", {"effect_id": effect, "kind": "done"})


def test_index_counts_merges_feedback_after_used_and_reworked(dsn):
    url = f"/toy/{ledger.new_id()}.git"

    async def go():
        async with await db.connect(dsn) as conn:
            a = await tasks.start(conn, tasks.Brief(instruction="a", origin_url=url, target_branch="main"))
            later = await tasks.start(
                conn, tasks.Brief(instruction="b", origin_url=url, target_branch="main")
            )
            plain = await tasks.start(conn, tasks.Brief(instruction="plain"))
            await ledger.append(
                conn, a, "task.delivered", {"candidate": "a" * 40, "outcome": "merged", "summary": "s"}
            )
            await merge(conn, a, url, "a" * 40, ["a.py"])
            await ledger.append(
                conn, a, "feedback.given",
                {"feedback_id": ledger.new_id(), "on_delivery": "s", "candidate": None, "text": "wrong", "provenance": ledger.provenance("tom", "test", False)},
            )  # fmt: skip
            await outcomes.mark_used(conn, a, by="tom")
            await outcomes.mark_used(conn, a, by="stand-in", role_played=True)
            await merge(conn, later, url, "b" * 40, ["a.py", "b.py"])
            listed = {t["task_id"]: t for t in await tasks.index(conn)}
            return listed[a], listed[later], listed[plain]

    a, later, plain = run(go())
    assert (a["merges"], a["feedback_after"], a["used"], a["reworked"]) == (1, 1, 1, 1)
    assert (later["merges"], later["feedback_after"], later["used"], later["reworked"]) == (1, 0, 0, 0)
    assert (plain["merges"], plain["feedback_after"], plain["used"], plain["reworked"]) == (0, 0, 0, 0)
