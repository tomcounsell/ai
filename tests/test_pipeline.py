"""The pipeline end to end on real Postgres and real git, with scripted
turns (`tests/scripted.py`): a task built, its checks passed, merged onto
its origin; and a red test sent to patch, the patched candidate merged.

No model call. Live spend: none.
"""

import asyncio
import json
import pathlib
import uuid

import pytest

from core import (
    broker,
    db,
    intake,
    judgement,
    judgement_sites,
    ledger,
    machine,
    notices,
    router,
    session,
    tasks,
)
from core import git as gitlib
from core.machine import State
from core.settings import settings
from tests import bridges, judgement_upstream, scripted
from tests.bridges import rows
from tests.ports import listen
from tests.scripted import git
from tools.jev import Jev
from tools.open_weight import OpenWeight

pytestmark = pytest.mark.spend(usd=0)


run = asyncio.run


async def drive(dsn, task, runners=None) -> dict:
    gateway = await bridges.gateway(dsn)
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


def test_a_moved_target_sends_the_work_to_rebase_however_often_it_moves(dsn, tmp_path):
    """Each rejection means another merge landed, so passing work rebases again."""
    ws, origin = scripted.workspace(tmp_path)
    git(tmp_path, "clone", "-q", str(origin), str(other := tmp_path / "other"))

    async def go():
        task = await to_checks(dsn, ws)
        states = []
        for n in range(3):
            scripted.commit(other, "greeting.txt", f"landed first {n}\n")
            git(other, "push", "-q", "origin", "HEAD:main")
            await scripted.checks(dsn, task)  # the push is rejected: main moved
            async with await db.connect(dsn) as conn:
                await scripted.ensure_merge(conn, task)  # the next step acts on it
            states.append((await fold(dsn, task)).state)
            await drive(dsn, task)
        return states, await fold(dsn, task)

    states, f = run(go())
    assert states == [State.PATCH] * 3 and f.counts["rebases"] == 3 and f.state is not State.ENDED
    assert f.rebase_tip == git(other, "rev-parse", "HEAD")


def test_after_a_rebase_a_candidates_diff_holds_its_own_commits_only(tmp_path):
    """The checks diff a rebased candidate from where it leaves the moved
    target, so another task's merged change is not read as this one's."""
    ws, origin = scripted.workspace(tmp_path)
    base = git(ws, "rev-parse", "HEAD")
    git(tmp_path, "clone", "-q", str(origin), str(other := tmp_path / "other"))
    scripted.commit(other, "theirs.txt", "landed first\n")
    git(other, "push", "-q", "origin", "HEAD:main")
    git(ws, "pull", "-q", "--rebase", "origin", "main")
    tip = git(ws, "rev-parse", "HEAD")
    scripted.commit(ws, "mine.txt", "this task's work\n")
    head = git(ws, "rev-parse", "HEAD")
    fork = gitlib.fork_point(ws, base, tip, head)
    assert fork == tip and gitlib.fork_point(ws, base, None, head) == base
    assert gitlib.diff_paths(ws, fork, head) == ["mine.txt"]


# -- end to end: Tom's DM, its session, a task, the merge, the news -------------------


def test_toms_dm_session_starts_the_work_and_tells_him_of_its_merge(dsn, tmp_path):
    """Tom's DM message reaches his DM's chat session, whose turn starts a
    task (`task.start`); the task builds, passes its checks and review, and
    merges; its report reaches the session, never a chat, and the
    session's next turn tells Tom's DM."""
    ws, origin = scripted.workspace(tmp_path)
    chat_ws, _ = scripted.workspace(tmp_path / "chat")
    tom = str(uuid.uuid4().int % 10**9)  # Tom's Telegram id: a DM no other test's session holds
    start = {"action_type": "task.start", "target": "work",
             "payload": {"instruction": "Write Tom a greeting.", "by": "tom"}}  # fmt: skip
    tell = {"action_type": "telegram.send_message", "target": tom,
            "payload": {"text": "Your greeting is merged.", "reply_to": None, "topic_id": None, "files": []}}  # fmt: skip
    scripted.steer(chat_ws, chat=[{"start": start}, {"tell": tell}])

    async def provisioned(task, fields):
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "workspace.provisioned", {"fields": fields})

    async def go():
        said = intake.Inbound(
            channel="telegram", chat_id=tom, chat_kind="dm", message_id="1", sender_id=tom,
            sender_name="Tom", sent_at="2026-10-17T09:00:00+00:00", text="Write me a greeting.",
        )  # fmt: skip
        async with await db.connect(dsn) as conn:
            got = await intake.receive(conn, said)
            await intake.bind(conn)
        (bound,) = await bridges.of_type(dsn, "message.bound", received_id=got.received_id)
        chat = bound["task_id"]
        await provisioned(chat, {"workspace": str(chat_ws)})
        first = await drive(dsn, chat)
        (made,) = [r["result"] for r in await bridges.of_type(dsn, "effect.outcome") if r["task_id"] == chat]
        task = made["task_id"]
        await provisioned(task, {"workspace": str(ws), **tasks.resolve_workspace(str(ws))})
        await scripted.run_judge(dsn, task, "precise")
        await drive(dsn, task)
        await scripted.critique(dsn, task)
        await drive(dsn, task)
        await scripted.checks(dsn, task)
        done = await merged(dsn, task)
        second = await drive(dsn, chat)
        (held,) = [r for r in await bridges.of_type(dsn, "effect.held") if r["task_id"] == chat]
        async with bridges.outbox(dsn, bridges.FakeBridge()) as box:
            (item,) = [i for i in await box.due() if getattr(i, "effect_id", None) == held["effect_id"]]
            sent = await box.perform(item)
        async with await db.connect(dsn) as conn:
            briefs = [await tasks.brief(conn, t) for t in (chat, task)]
        return bound, first, second, done, sent, briefs, await rows(dsn, chat), await rows(dsn, task)

    with (
        bridges.operator(tmp_path),
        bridges.configure(operator_telegram_id=tom, database=settings.test_database),
    ):
        (pathlib.Path(settings.projects_dir) / "valor.toml").write_text(bridges.VALOR_SPEC)
        bound, first, second, done, sent, (cb, tb), chat_rows, task_rows = run(go())
    assert bound["as"] == "steer" and cb.chat == {"channel": "telegram", "chat_id": tom}
    assert (first["status"], second["status"]) == ("listening", "listening")
    assert tb.parent_id == cb.id and tb.project == {"name": "valor"}
    assert done.kind == "done" and git(origin, "rev-parse", "main") == machine.fold(task_rows).candidate.sha
    assert not bridges.typed(task_rows, "notice.requested")
    reported = [p for p in bridges.typed(chat_rows, "child.reported") if p["child_id"] == tb.id]
    assert len(reported) == 1 and reported[0]["kind"] == "report"
    turns = [json.loads(x) for x in (chat_ws / ".git" / "valor-turns.jsonl").read_text().splitlines()]
    assert len(turns) == 2 and f"Your task {tb.id} reports (report):" in turns[1]["prompt"]
    assert turns[0]["prompt"].startswith("You are in Tom's own chat on Telegram")
    assert "Write me a greeting." in turns[0]["prompt"] and "# A chat session" in turns[0]["brief"]
    (held,) = bridges.typed(chat_rows, "effect.held")
    assert (held["action_type"], held["target"]) == ("telegram.send_message", tom)
    assert held["payload"]["text"] == "Your greeting is merged."
    # The session's send to Tom's DM is itself the message: it owes no report.
    assert sent.kind == "done" and not bridges.typed(chat_rows, "notice.requested")


# -- an idle turn ends its step; stop.md hands in from any working state -------------


async def at(dsn, ws, stage: str) -> str:
    task = await scripted.start(dsn, ws, judge="thin" if stage == "clarify" else "precise")
    if stage == "build":
        scripted.steer(ws)
        await drive(dsn, task)
        await scripted.critique(dsn, task)
    assert (await fold(dsn, task)).state.value == stage
    return task


@pytest.mark.parametrize("stage", ["clarify", "plan", "build"])
def test_stop_ends_the_work_from_clarify_plan_and_build_and_done_says_so(dsn, tmp_path, stage):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await at(dsn, ws, stage)
        scripted.steer(ws, **{stage: "stop"}, note="Done: sent the greeting.")
        out = await drive(dsn, task)
        return out, await fold(dsn, task)

    out, f = run(go())
    assert out["status"] == "ended" and f.state is State.ENDED
    assert f.entry["payload"] == {**f.entry["payload"], "kind": "decided", "by": "valor"}
    assert notices.ended_text(f, None).startswith("This is done; nothing needed merging.\n")
    f.entry["payload"]["text"] = "Not worth it."
    assert notices.ended_text(f, None).startswith("I stopped this work without merging it.")


def test_a_stop_beside_a_question_lets_the_question_count(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await at(dsn, ws, "clarify")
        scripted.steer(ws, clarify="stop", beside="question.md")
        await drive(dsn, task)
        return await rows(dsn, task)

    written = run(go())
    (collected,) = [r["payload"] for r in written if r["type"] == "turn.collected"]
    assert collected["verdict"] == "asked" and session.STOP_BESIDE in collected["errors"]
    assert machine.fold(written).state is State.WAITING


def test_an_idle_turn_ends_its_step_and_the_next_step_continues(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await at(dsn, ws, "plan")
        scripted.steer(ws, plan="nothing", turns=1)
        gateway = await bridges.gateway(dsn)
        try:
            one = await router.step(gateway, task, scripted.RUNNERS, dsn, scripted.performers)
        finally:
            await gateway.close()
        rest = await drive(dsn, task)  # `python -m core run` steps on after an idle one
        return one, rest

    one, rest = run(go())
    turns = [json.loads(x) for x in (ws / ".git" / "valor-turns.jsonl").read_text().splitlines()]
    assert one["status"] == "idle" and one["state"]["state"] == "plan"
    assert rest["missing"] == ["critique"] and [t["prompt"] for t in turns][-1] == "Continue."


def test_an_idle_turn_that_asked_the_advisor_goes_on_in_the_same_run(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    asked = []

    async def advise(gateway, task, dsn_, alive, question):
        asked.append(question["advice"])
        async with await db.connect(dsn_) as conn:
            await ledger.append(conn, task, session.ADVICE_GIVEN,
                                {"asked_turn_id": question["turn_id"], "answer": "Yes.", "seat": "s"})  # fmt: skip
        return {"status": "advised"}

    async def working(ctx):
        return await session.run(ctx.gateway, ctx.task_id, scripted.turn_for, dsn=ctx.dsn,
                                 alive=ctx.alive, performers=ctx.performers, advise=advise)  # fmt: skip

    async def go():
        task = await at(dsn, ws, "plan")
        scripted.steer(ws, advise=[{"text": "Should I?", "act": "nothing"}])
        return await drive(dsn, task, {**scripted.RUNNERS, State.PLAN: working})

    out = run(go())
    assert asked == ["Should I?"] and out["missing"] == ["critique"]


# -- the reconciler, in shadow (docs/plans/ask-reconciliation.md) ------------------


async def ask(conn, project: str, instruction: str = "fix it", **marker) -> str:
    b = tasks.Brief(instruction=instruction, origin_url=f"/repos/{project}.git")
    return await tasks.start(conn, b, marker={"sdlc": 1, **marker} if marker else None)


def kinds(found: list[dict], *types: str) -> list[dict]:
    return [r for r in found if r["type"] in types]


def test_an_ask_is_read_against_its_projects_open_tasks_and_the_judge_runs_as_without_it(dsn, monkeypatch):
    async def go():
        async with await db.connect(dsn) as conn:
            older = await ask(conn, "alpha", "fix the valor spec's branch in the tests")
            newer = await ask(conn, "alpha")
            ended = await ask(conn, "alpha")
            await ledger.append(conn, ended, "task.ended", {"outcome": "ended"})
            stopped = await ask(conn, "alpha")
            await tasks.stop(conn, stopped, reason="test")
            other = await ask(conn, "beta")
            legacy = await tasks.start(conn, tasks.Brief("x", origin_url="/repos/alpha.git"), marker={})
            listed, left_out = await judgement_sites.open_tasks(conn, "alpha", besides="none")
            monkeypatch.setattr(judgement_sites, "OPEN_LISTED", 1)
            cut = await judgement_sites.open_tasks(conn, "alpha", besides="none")
            monkeypatch.undo()
            task = await ask(conn, "alpha", "the tests still assert valor-cori-rebuild")
        await scripted.run_judge(dsn, task, "precise,covered,2,after")
        await scripted.run_judge(dsn, task, "precise")  # moved on: asks nothing again
        return older, newer, (ended, stopped, other, legacy), listed, left_out, cut, await rows(dsn, task)

    older, newer, closed, listed, left_out, cut, found = run(go())
    assert [t["id"] for t in listed] == [newer, older] and left_out == 0
    assert not {t["id"] for t in listed} & set(closed)
    assert [t["id"] for t in cut[0]] == [newer] and cut[1] == 1
    decided, judged = kinds(found, "reconcile.decided", "judge.decided")
    assert decided["id"] < judged["id"]
    p = decided["payload"]
    assert (p["decision"], p["named"], p["placement"], p["shadow"]) == ("covered", older, "after", True)
    # Shadow holds nothing: the judge decides as it would have, and no notice is owed.
    assert judged["payload"]["verdict"] == "precise" and machine.fold(found).state is State.PLAN
    assert not kinds(found, "notice.requested", "question.asked")
    overlap = [r for r in kinds(found, "judgement.answered") if r["payload"]["site"] == "intake.overlap"]
    assert len(overlap) == 1


def test_a_retried_judge_step_asks_no_second_overlap_and_a_failure_or_a_stop_holds_nothing(dsn):
    jev = Jev(f"http://127.0.0.1:{listen()}/jev", judgement.LOOPBACK_KEY, timeout_s=2)
    ow = OpenWeight(f"http://127.0.0.1:{listen()}/ow", judgement.LOOPBACK_KEY, timeout_s=2)
    dead = judgement.JudgementPort({"jev": jev, "open_weight": ow})

    async def go():
        async with await db.connect(dsn) as conn:
            retried = await ask(conn, "alpha")
            failed = await ask(conn, "alpha")
            stopped = await ask(conn, "alpha")
            await tasks.stop(conn, stopped, reason="test")
        up = judgement_upstream.shared().port(fixed="precise,covered,1")
        # The overlap recorded, then the step lost before the judge's verdict.
        ctx = router.Context(None, retried, dsn, scripted._always)
        async with await db.connect(dsn) as conn:
            b, before = await tasks.brief(conn, retried), await ledger.read(conn, retried)
        await judgement_sites._reconcile(up, ctx, before, b)
        await scripted.run_judge(dsn, retried, "precise")
        out = await judgement_sites.judge_runner(dead)(router.Context(None, failed, dsn, scripted._always))
        gone = await judgement_sites.judge_runner(up)(router.Context(None, stopped, dsn, scripted._always))
        return out, gone, *[await rows(dsn, t) for t in (retried, failed, stopped)]

    out, gone, retried, failed, stopped = run(go())
    assert len(kinds(retried, "reconcile.decided")) == 1 and machine.fold(retried).state is State.PLAN
    sites = [r["payload"]["site"] for r in kinds(retried, "judgement.answered")]
    assert sites.count("intake.overlap") == 1
    p = kinds(failed, "reconcile.decided")[0]["payload"]
    assert (p["decision"], p["note"], p["answered"]) == ("distinct", "both legs failed", False)
    assert out["status"] == "moved" and machine.fold(failed).state is State.CLARIFY  # the judge's own caution
    assert gone["status"] == "stopped" and not kinds(stopped, "reconcile.decided")


def test_the_reconcilers_table_never_stands_on_a_guess():
    def row(picked, kind="judgement.answered"):
        return {"type": kind, "payload": {"picked": picked, "ref": {"listed": ["t1", "t2"]}}}

    decide = judgement_sites.reconcile_decision
    assert decide(row({"task": "1"}))["note"] == "the decision abstained"
    assert decide(row({}, "judgement.failed"))["decision"] == "distinct"
    assert decide(row({"decision": "covered", "task": "7", "placement": "after"})) == {
        "decision": "covered", "named": None, "placement": None, "note": "no listed task named",
    }  # fmt: skip
    assert decide(row({"decision": "distinct", "task": "2", "placement": "before"}))["named"] is None
    assert decide(row({"decision": "overlaps", "task": "2", "placement": "none"}))["named"] == "t2"
