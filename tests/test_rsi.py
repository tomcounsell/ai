"""The `rsi` routine (routines/rsi): what it reads from the ledger, what it
starts, what counts as used, and the lineage in a merge report. Plain SQL, no
turn, no model: a fake `start_project` writes the child task a real start
would."""

import asyncio
import itertools
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core import db, ledger, notices, outcomes, routines, tasks
from core.settings import settings
from routines.rsi import runner
from tests import bridges

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
SPEC = 'name = "{}"\nrepo = "https://github.com/{}.git"\nkind = "plain"\nsuite = "true"\n'
SPECS = {"valor": "tomcounsell/ai", "popoto": "tomcounsell/popoto", "cuttlefish": "cuttlefish-co/app"}
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
numbers = itertools.count(1000)
ids = itertools.count(1)


@pytest.fixture
def home(tmp_path):
    with bridges.operator(tmp_path):
        for name, repo in SPECS.items():
            (Path(settings.projects_dir) / f"{name}.toml").write_text(SPEC.format(name, repo))
        yield


def run(coro):
    return asyncio.run(coro)


class Rsi:
    """One test's database, its fake start, and what it started."""

    def __init__(self, dsn):
        self.dsn, self.started = dsn, []

    async def start_project(self, conn, **kw):
        self.started.append(kw)
        return await tasks.start_child(
            conn,
            kw["parent"],
            ceiling=kw["ceiling"],
            marker=kw["marker"],
            by="routine",
            instruction=kw["instruction"],
            routine=kw["routine"],
        )

    async def fire(self):
        before = len(self.started)
        async with await db.connect(self.dsn) as conn:
            await routines.run(
                conn,
                "rsi",
                {"rsi": runner.run},
                start_project=self.start_project,
                directory=ROOT / "routines",
                dsn=self.dsn,
                now=NOW,
            )
        return self.started[before:]

    async def correction(self, text="stop saying sorry", by="tom"):
        from core import corrections

        async with await db.connect(self.dsn) as conn:
            return await ledger.append(
                conn,
                "corrections",
                "correction.recorded",
                corrections.payload(next(numbers), text, by=by, via="t"),
            )

    async def dm(self, text, *, kind="dm", verified=True, sender=bridges.OPERATOR):
        p = {"verified": verified, "channel": "telegram", "chat_id": sender, "chat_kind": kind,
             "message_id": str(next(ids)), "sender_id": sender, "text": text}  # fmt: skip
        async with await db.connect(self.dsn) as conn:
            return await ledger.append(conn, "telegram", "message.received", p)

    async def task(self, repo, ended=True, retries=5):
        """A task on `repo` whose step failed `retries` times; ended when asked."""
        async with await db.connect(self.dsn) as conn:
            t = await tasks.start(
                conn, tasks.Brief(instruction="build x", project={"repo": f"https://github.com/{repo}.git"})
            )
            for _ in range(retries):
                await ledger.append(
                    conn, t, "step.failed", {"state": "test", "check": "test", "reason": "boom"}
                )
            if ended:
                await ledger.append(
                    conn,
                    t,
                    "task.ended",
                    {
                        "kind": "step",
                        "by": "kernel",
                        "tries": retries,
                        "state": "test",
                        "check": "test",
                        "reason": "boom",
                    },
                )
            return t

    async def used(self, task, by, role_played=False):
        async with await db.connect(self.dsn) as conn:
            await ledger.append(
                conn, task, "task.delivered", {"candidate": "c", "outcome": "done", "summary": "s"}
            )
            await outcomes.mark_used(conn, task, by=by, role_played=role_played)

    async def counts(self):
        async with await db.connect(self.dsn) as conn:
            return await runner.used_over_started(conn)


@pytest.fixture
def rsi(fresh, home):
    r = Rsi(fresh)
    assert run(r.fire()) == []  # registers the routine
    return r


def test_the_routine_loads():
    r = routines.load("rsi", ROOT / "routines")
    assert (r.runner, r.interval, r.project, r.branch, r.ceiling) == ("rsi", 900, "valor", "main", "act")


def test_an_idle_run_starts_no_task(rsi):
    assert run(rsi.fire()) == []
    ran = run(bridges.of_type(rsi.dsn, "routine.ran"))
    assert [(r["run"], r["outcome"]) for r in ran] == [(None, "nothing_due")] * 2


def test_corrections_before_registration_start_nothing(fresh, home):
    r = Rsi(fresh)
    for _ in range(50):
        run(r.correction())
    assert run(r.fire()) == []
    assert run(r.fire()) == []
    run(r.correction("new"))
    (kw,) = run(r.fire())
    assert kw["project"] == "valor" and kw["branch"] == "main"


def test_each_row_starts_one_task_and_is_never_started_again(rsi):
    run(rsi.correction("a"))
    run(rsi.correction("b"))
    first = run(rsi.fire())
    assert [kw["marker"]["finding"]["source"] for kw in first] == ["rsi", "rsi"]
    assert "a" in first[0]["instruction"] and "b" in first[1]["instruction"]
    assert run(rsi.fire()) == []


def test_stopped_and_ended_tasks_do_not_restart_their_row(rsi):
    run(rsi.correction("a"))
    run(rsi.correction("b"))
    run(rsi.fire())

    async def end_them():
        async with await db.connect(rsi.dsn) as conn:
            a, b = await runner.started_tasks(conn)
            await tasks.stop(conn, a, reason="no", by="tom", via="t")
            await ledger.append(conn, b, "task.ended", {"kind": "crash", "by": "kernel"})
            return [await routines.state_of(conn, t) for t in (a, b)]

    assert sorted(run(end_them())) == ["ended", "stopped"]
    assert run(rsi.fire()) == []


def test_five_failed_retries_start_nothing_and_the_ended_task_starts_one(rsi):
    run(rsi.task("tomcounsell/ai", ended=False))
    assert run(rsi.fire()) == []
    run(rsi.task("tomcounsell/ai"))
    (kw,) = run(rsi.fire())
    assert kw["project"] == "valor" and "failed 5 times" in kw["instruction"]
    assert "boom" in kw["instruction"] and "build x" in kw["instruction"]


def test_a_failed_step_on_a_client_task_starts_nothing(rsi):
    run(rsi.task("cuttlefish-co/app"))
    assert run(rsi.fire()) == []


def test_a_failed_step_on_a_task_rsi_started_starts_nothing(rsi):
    run(rsi.correction())
    run(rsi.fire())

    async def fail_it():
        async with await db.connect(rsi.dsn) as conn:
            (t,) = await runner.started_tasks(conn)
            await ledger.append(
                conn, t, "task.ended", {"kind": "step", "by": "kernel", "tries": 5, "check": "x"}
            )

    run(fail_it())
    assert run(rsi.fire()) == []


@pytest.mark.parametrize(
    "text, kw",
    [
        ("look at https://github.com/tomcounsell/ai/issues/9", {}),
        ("no link here", {}),
        ("https://github.com/tomcounsell/ai/pull/3", {"kind": "group"}),
        ("https://github.com/tomcounsell/ai/pull/3", {"verified": False}),
        ("https://github.com/tomcounsell/ai/pull/3", {"sender": "7"}),
        ("https://vm.tiktok.com/abc", {}),
        ("https://claude.ai/code/artifact/abc", {}),
        ("https://github.com/cuttlefish-co/app/pull/1", {}),
    ],
)
def test_only_tom_s_verified_dm_with_an_allowed_repo_link_starts_a_task(rsi, text, kw):
    run(rsi.dm(text, **kw))
    started = run(rsi.fire())
    assert len(started) == (text.startswith("look") and 1 or 0)


def test_a_dm_linking_popoto_starts_on_popoto(rsi):
    run(rsi.dm("fix https://github.com/tomcounsell/popoto/issues/4"))
    (kw,) = run(rsi.fire())
    assert (kw["project"], kw["branch"]) == ("popoto", None)
    assert kw["marker"]["finding"]["project"] == "popoto"
    assert "Evidence" in kw["instruction"] and "popoto/issues/4" in kw["instruction"]


def test_used_over_started_counts_tom_alone(rsi):
    for text in "abcd":
        run(rsi.correction(text))
    run(rsi.fire())
    assert run(rsi.counts()) == (0, 4)

    async def mark():
        async with await db.connect(rsi.dsn) as conn:
            found = await runner.started_tasks(conn)
        for t, (by, role) in zip(found, [("Tom", False), (" tom ", False), ("Tom", True), ("Rain", False)]):
            await rsi.used(t, by, role)

    run(mark())
    assert run(rsi.counts()) == (2, 4)

    async def shown():
        async with await db.connect(rsi.dsn) as conn:
            return await runner.status(conn)

    assert run(shown()).startswith("used 2 of 4 started")


def test_the_first_run_of_each_week_tells_tom_once(rsi):
    run(rsi.fire())
    run(rsi.fire())

    async def keys():
        async with await db.connect(rsi.dsn) as conn:
            rows = await (
                await conn.execute(
                    "SELECT payload->>'about_key' FROM events WHERE type IN ('notice.requested', 'child.reported') "
                    "AND payload->>'about_key' LIKE 'rsi-week:%'"
                )
            ).fetchall()
            return [r[0] for r in rows]

    assert set(run(keys())) == {"rsi-week:2026-W44"}
    assert len(run(keys())) == 1


def test_the_merge_report_carries_the_lineage_line(rsi):
    run(rsi.correction())
    run(rsi.fire())

    async def report():
        async with await db.connect(rsi.dsn) as conn:
            (t,) = await runner.started_tasks(conn)
            other = await tasks.start(conn, tasks.Brief(instruction="plain"))
            out = []
            for task in (t, other):
                effect = {"effect_id": ledger.new_id(), "effect_class": "act", "action_type": "merge",
                          "target": "main", "payload": {"target_branch": "main"}}  # fmt: skip
                await notices.report(conn, task, effect, {})
                rows = await (
                    await conn.execute(
                        "SELECT payload->>'text' FROM events WHERE type IN ('notice.requested', 'child.reported') "
                        "AND %s IN (task_id, payload->>'child_id') AND payload->>'about_key' LIKE 'report:%%'",
                        (task,),
                    )
                ).fetchall()
                out.append(rows[0][0])
            return t, out

    mine, plain = run(report())[1]
    assert "rsi: rsi:" in mine and "rsi:" not in plain


def test_verify_is_unchanged():
    r = routines.load("verify", ROOT / "routines")
    assert (r.runner, r.schedule, r.project) == ("verify", {"hour": 2, "minute": 0}, "valor")
