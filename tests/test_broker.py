"""The broker records what a merge landed on its `effect.intent`
(`outcomes.landed`), on a workspace the kernel provisioned, through the
real pipeline with scripted sessions (`tests/scripted.py`): a released
merge's intent carries it; a failed read or a value Postgres refuses
records nulls and the merge still lands; a reconciled merge keeps the
intent's `landed`.

Live spend: none.
"""

import pytest

from core import broker, db, git, ledger, machine, outcomes
from core.machine import State
from tests import scripted
from tests.test_fresh import drive, planned, rows, run

pytestmark = pytest.mark.spend(usd=0)


def released(dsn, tmp_path):
    """A provisioned task through its checks, approved and released."""
    task, b, ws = planned(dsn, tmp_path)

    async def go():
        await drive(dsn, task, scripted.fresh_runners(ws))
        await scripted.checks(dsn, task)
        effect = machine.fold(await rows(dsn, task)).merge_effect["effect_id"]
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge it")
            done = await scripted.release(conn, effect)
        written = await rows(dsn, task)
        intent = next(
            r["payload"]
            for r in written
            if r["type"] == "effect.intent" and r["payload"]["effect_id"] == effect
        )
        return done, intent, machine.fold(written), b

    return run(go())


@pytest.mark.macos
def test_a_released_merges_intent_carries_what_it_landed(dsn, tmp_path):
    done, intent, f, b = released(dsn, tmp_path)
    landed = intent["landed"]
    assert done.kind == "done" and f.state is State.MERGED
    assert landed["before"] == b.base_sha and landed["why"] is None
    assert landed["commits"] and landed["commits"][-1] == f.candidate.sha
    assert landed["paths"] == sorted(landed["paths"]) and landed["paths"]


@pytest.mark.macos
@pytest.mark.parametrize("error", [git.GitError("no such object"), ValueError("bad")])
def test_a_failed_read_records_nulls_and_the_merge_lands(dsn, tmp_path, monkeypatch, error):
    def fail(*_a, **_kw):
        raise error

    monkeypatch.setattr(outcomes, "own_changes", fail)
    done, intent, f, b = released(dsn, tmp_path)
    assert done.kind == "done" and f.state is State.MERGED
    assert intent["landed"] == {
        "before": b.base_sha,
        "commits": None,
        "paths": None,
        "why": type(error).__name__,
    }


@pytest.mark.macos
def test_an_unstorable_path_still_writes_the_intent_and_the_merge_lands(dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(outcomes, "own_changes", lambda *_a, **_kw: (["0" * 40], ["a\x00b.py"]))
    done, intent, f, b = released(dsn, tmp_path)
    landed = intent["landed"]
    assert done.kind == "done" and f.state is State.MERGED
    assert landed["before"] == b.base_sha and landed["commits"] is None and landed["paths"] is None
    assert landed["why"].startswith(ledger.UNSTORABLE)


class Merged:
    """A merge performer that finds every merge it is asked about."""

    action_type = "merge"
    effect_class = "act"

    async def lookup(self, action, key):
        return {"sha": action.payload["head_sha"]}


def test_a_reconciled_merge_keeps_its_intents_landed(dsn):
    landed = {"before": "0" * 40, "commits": ["1" * 40], "paths": ["a.py"], "why": None}
    effect_id = ledger.new_id()
    task = ledger.new_id()
    described = {
        "action_type": "merge",
        "target": "/toy.git",
        "payload": {"url": "/toy.git", "target_branch": "main", "head_sha": "1" * 40, "candidate": "1" * 40},
        "payload_sha256": "x",
        "effect_class": "act",
        "idempotency_key": ledger.new_id(),
    }

    async def go():
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "effect.held", {"effect_id": effect_id, **described})
            await ledger.append(
                conn,
                task,
                "effect.intent",
                {"effect_id": effect_id, "approval_id": None, **described, "landed": landed},
            )
            out = await broker.reconcile(conn, broker.Performers(Merged()), effect_id)
            return out, outcomes.merges(await ledger.read(conn, task))

    out, found = run(go())
    assert out.kind == "done"
    (m,) = found
    assert m["effect_id"] == effect_id and m["landed"] == landed
