"""The three judgement sites on real Postgres and real git: the judge runner
through the router, the breadth and governance calls 1.4's runners make and
the verdicts the kernel builds from their rows, the emulator's forced arm,
and calibration tasks.

Every provider call goes to the local judgement upstream
(`tests/judgement_upstream.py`); turns are scripted subprocesses.
Live spend: none.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import (
    budget,
    db,
    guards,
    judgement,
    judgement_sites,
    ledger,
    machine,
    router,
    session,
    tasks,
    verdicts,
)
from core.gateway import Gateway
from core.judgement_tasks import GOVERNANCE, JUDGE
from core.machine import Check, State
from tests import judgement_upstream, scripted
from tests.conftest import TEST_DB
from tests.scripted import commit

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
UP = judgement_upstream.shared()
PRECISE = {
    "request": {"precise": 0.95, "one_line_ask": 0.02, "example_as_spec": 0.02, "existing_ui_unscoped": 0.01}
}
HALF = {"request": {"precise": 0.5, "one_line_ask": 0.5, "example_as_spec": 0.0, "existing_ui_unscoped": 0.0}}
NO = {"adds": {"true": 0.03, "false": 0.97}}
YES = {"adds": {"true": 0.97, "false": 0.03}}
UNSURE = {"adds": {"true": 0.5, "false": 0.5}}
BOOLS = ("gap_state", "gap_enum", "gap_bound")


def run(coro):
    return asyncio.run(coro)


def gaps(*true) -> dict:
    return {q: ({"true": 0.95, "false": 0.05} if q in true else {"true": 0.03, "false": 0.97}) for q in BOOLS}


async def drive(dsn, task, judge_port=None) -> dict:
    runners = dict(scripted.RUNNERS)
    if judge_port is not None:
        runners[State.JUDGE] = judgement_sites.judge_runner(judge_port)
    gateway = Gateway(dsn)
    await gateway.start()
    try:
        return await router.run(gateway, task, runners, dsn=dsn)
    finally:
        await gateway.close()


async def rows(dsn, task, kind=None) -> list[dict]:
    async with await db.connect(dsn) as conn:
        got = await ledger.read(conn, task)
    return [r for r in got if kind is None or r["type"] == kind]


# -- the judge ---------------------------------------------------------------------


def test_a_precise_request_is_judged_before_any_turn_and_goes_to_plan(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(default={"probs": PRECISE})

    async def go():
        task = await scripted.start(dsn, ws, judge=None)
        out = await drive(dsn, task, UP.port(script=sid))
        return out, await rows(dsn, task)

    out, got = run(go())
    kinds = [r["type"] for r in got]
    assert kinds.index("judgement.answered") < kinds.index("judge.decided") < kinds.index("turn.started")
    decided = next(r["payload"] for r in got if r["type"] == "judge.decided")
    answered = next(r["payload"] for r in got if r["type"] == "judgement.answered")
    assert decided["verdict"] == "precise" and decided["leg"] == "judgement" and decided["guard_id"] is None
    assert decided["judgement_id"] == answered["judgement_id"] and decided["p_precise"] == 0.95
    assert scripted.turns(ws)[0]["stage"] == "plan"
    assert out["status"] == "no runner" and out["missing"] == ["critique"]


@pytest.mark.parametrize(
    "replies",
    [
        [
            {
                "probs": {
                    "request": {
                        "precise": 0.05,
                        "one_line_ask": 0.9,
                        "example_as_spec": 0.05,
                        "existing_ui_unscoped": 0.0,
                    }
                }
            }
        ],
        [{"probs": HALF}, {"probs": HALF}],  # both abstain
        [{"status": 500}, {"status": 503}],  # both down
    ],
    ids=["thin", "abstained", "both-down"],
)
def test_a_thin_abstained_or_unanswered_request_goes_to_clarify_under_its_guard(dsn, tmp_path, replies):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(*replies)

    async def go():
        task = await scripted.start(dsn, ws, judge=None)
        out = await drive(dsn, task, UP.port(script=sid))
        return out, await rows(dsn, task, "judge.decided")

    out, decided = run(go())
    assert decided[0]["payload"]["verdict"] == "thin"
    assert decided[0]["payload"]["guard_id"] == machine.GUARD_JUDGE
    assert scripted.turns(ws)[0]["stage"] == "clarify" and out["status"] == "waiting"


def test_an_answer_written_before_a_crash_is_used_without_asking_again(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(default={"probs": PRECISE})

    async def go():
        task = await scripted.start(dsn, ws, judge=None)
        start = (await rows(dsn, task, "task.started"))[0]["payload"]
        p = UP.port(script=sid)
        inputs = {"request": start["instruction"], "thread": "", "project": "ws"}
        await p.judge(JUDGE, inputs, task_id=task, ref={}, dsn=dsn)  # the crash came after this row
        asked = len(UP.seen(sid))
        await drive(dsn, task, p)
        return asked, await rows(dsn, task, "judge.decided")

    asked, decided = run(go())
    assert len(UP.seen(sid)) == asked == 1
    assert decided[0]["payload"]["verdict"] == "precise"


def test_the_judge_verdict_is_read_only_from_this_tasks_own_request_judgement(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, judge=None)
        other = await scripted.start(dsn, ws, judge=None)
        p = UP.port(fixed="precise")
        theirs = await p.judge(
            JUDGE, {"request": "x", "thread": "", "project": "p"}, task_id=other, ref={}, dsn=dsn
        )
        gov = await p.judge(
            GOVERNANCE, {"path": "a", "hunk": "b", "paths": "a"}, task_id=task, ref={}, dsn=dsn
        )
        mine = await p.judge(
            JUDGE, {"request": "x", "thread": "", "project": "p"}, task_id=task, ref={}, dsn=dsn
        )
        async with await db.connect(dsn) as conn:
            for bad in (theirs.judgement_id, gov.judgement_id, "no-such-judgement"):
                with pytest.raises(verdicts.VerdictRefused, match="not a request judgement"):
                    await verdicts.record_judge(conn, task, bad)
            await verdicts.record_judge(conn, task, mine.judgement_id)
            with pytest.raises(verdicts.VerdictRefused, match="not judge"):
                await verdicts.record_judge(conn, task, mine.judgement_id)

    run(go())


# -- breadth ---------------------------------------------------------------------


async def to_checks(dsn, ws, **kw) -> str:
    task = await scripted.start(dsn, ws, budget_usd_micros=100_000, **kw)
    await drive(dsn, task)
    await scripted.critique(dsn, task)
    out = await drive(dsn, task)
    assert out["missing"] == ["test", "review", "docs"], out
    return task


async def record_test(dsn, task, judgement_id, verdict, failures=()):
    async with await db.connect(dsn) as conn:
        return await verdicts.record_check(
            conn,
            task,
            Check.TEST,
            verdict,
            breadth=judgement_id,
            failures=failures,
            command="pytest",
            **scripted.MANUAL,
        )


@pytest.mark.parametrize(
    ("answer", "failures", "verdict", "listed"),
    [
        (gaps(), (), "pass", 0),
        (gaps("gap_enum", "gap_bound"), (), "gaps", 2),
        (gaps(), ("test_x failed",), "red", 0),
    ],
)
def test_the_test_verdict_is_computed_from_the_failures_and_the_breadth_row(
    dsn, tmp_path, answer, failures, verdict, listed
):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(default={"probs": answer})

    async def go():
        task = await to_checks(dsn, ws)
        jid = await judgement_sites.breadth(UP.port(script=sid), dsn, task)
        again = await judgement_sites.breadth(UP.port(script=sid), dsn, task)
        wrong = "pass" if verdict != "pass" else "gaps"
        with pytest.raises(verdicts.VerdictRefused, match=f"the verdict is {verdict}"):
            await record_test(dsn, task, jid, wrong, failures)
        await record_test(dsn, task, jid, verdict, failures)
        return jid, again, (await rows(dsn, task, "test.decided"))[0]["payload"]

    jid, again, decided = run(go())
    assert again == jid and len(UP.seen(sid)) == 1  # the answered row is reused, not asked again
    assert decided["verdict"] == verdict and decided["breadth"]["judgement_id"] == jid
    assert len(decided["behaviors"]) == listed
    if listed:
        assert decided["behaviors"] == [
            "untested: a member of an enumeration the code branches on",
            "untested: existing tests whose bounds encode the old behavior",
        ]
        assert decided["guard_id"] == machine.GUARD_BREADTH


def test_breadth_left_unanswered_reruns_then_lists_breadth_not_judged(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(default={"status": 503})

    async def go():
        task = await to_checks(dsn, ws)
        p = UP.port(script=sid)
        first = await judgement_sites.breadth(p, dsn, task)
        with pytest.raises(verdicts.VerdictRefused, match="breadth unanswered"):
            await record_test(dsn, task, first, "pass")
        state_after_first = (await tasks_status(dsn, task))["checks"]
        second = await judgement_sites.breadth(p, dsn, task)
        third = await judgement_sites.breadth(p, dsn, task)  # reruns spent: no new call
        await record_test(dsn, task, second, "gaps")
        return first, second, third, state_after_first, (await rows(dsn, task, "test.decided"))[0]["payload"]

    first, second, third, checks_after_first, decided = run(go())
    assert checks_after_first == {}  # the branch had no verdict after the first outage
    assert first != second == third and len(UP.seen(sid)) == 4  # two judgements, two legs each
    assert decided["behaviors"][0].startswith("breadth not judged: both judgement legs failed on 2 runs")


async def tasks_status(dsn, task):
    async with await db.connect(dsn) as conn:
        return await tasks.status(conn, task)


def test_a_breadth_row_for_another_candidate_or_site_is_refused(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        jid = await judgement_sites.breadth(UP.port(fixed="false"), dsn, task)
        got = await rows(dsn, task)
        with pytest.raises(judgement_sites.Unusable, match="another candidate"):
            judgement_sites.breadth_outcome(got, jid, machine.Candidate("0" * 40, "t-old"))
        gov = await UP.port(fixed="true").judge(
            GOVERNANCE, {"path": "a", "hunk": "b", "paths": "a"}, task_id=task, ref={}, dsn=dsn
        )
        with pytest.raises(verdicts.VerdictRefused, match="not a breadth judgement"):
            await record_test(dsn, task, gov.judgement_id, "pass")
        async with await db.connect(dsn) as conn:
            with pytest.raises(verdicts.VerdictRefused, match="only the test branch"):
                await verdicts.record_check(conn, task, Check.REVIEW, "pass", breadth=jid, **scripted.MANUAL)

    run(go())


# -- governance ------------------------------------------------------------------

LONG_FUNCTION = (
    "def handler(request):\n" + "".join(f"    step_{i} = {i}\n" for i in range(30)) + "    return step_29\n"
)


async def governance_candidate(dsn, ws, extra=None) -> str:
    """A task whose candidate adds hooks/gate.py, lib/util.py, a change deep
    in lib/handler.py's function, and the scripted plan."""
    commit(ws, "lib/handler.py", LONG_FUNCTION, "a long function at the base")
    task = await scripted.start(dsn, ws, budget_usd_micros=1_000_000)
    await drive(dsn, task)
    await scripted.critique(dsn, task)
    commit(ws, "hooks/gate.py", "def gate(push):\n    if push.unreviewed:\n        raise Refused\n", "a gate")
    commit(ws, "lib/util.py", "def add(a, b):\n    return a + b\n", "a helper")
    commit(ws, "lib/handler.py", LONG_FUNCTION.replace("step_20 = 20", "step_20 = 2000"), "change a step")
    for path, text in (extra or {}).items():
        commit(ws, path, text, "extra")
    scripted.steer(ws, build="reasons")
    await drive(dsn, task)
    return task


async def candidate_range(dsn, task) -> tuple[str, str]:
    async with await db.connect(dsn) as conn:
        b = await tasks.brief(conn, task)
    f = machine.fold(await rows(dsn, task))
    return b.base_sha, f.candidate.sha


async def review(dsn, task, verdict, ids, **kw):
    async with await db.connect(dsn) as conn:
        return await verdicts.record_check(
            conn, task, Check.REVIEW, verdict, governance_from=ids, **scripted.MANUAL, **kw
        )


def test_governance_judges_every_hunk_and_the_kernel_makes_the_instances(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(default={"by_path": {"hooks/gate.py": YES}, "probs": NO})

    async def go():
        task = await governance_candidate(dsn, ws)
        older, newer = await candidate_range(dsn, task)
        p = UP.port(script=sid)
        ids = await judgement_sites.governance(p, dsn, task, older, newer)
        again = await judgement_sites.governance(p, dsn, task, older, newer)
        with pytest.raises(verdicts.VerdictRefused, match="no governance judgement for hunks"):
            await review(dsn, task, "governance_refused", ids[1:])
        with pytest.raises(verdicts.VerdictRefused, match="answered twice"):
            await review(dsn, task, "governance_refused", [*ids, ids[0]])
        stray = await p.judge(
            GOVERNANCE,
            {"path": "x", "hunk": "y", "paths": "x"},
            task_id=task,
            ref={"hunk": {"id": "f" * 16, "path": "x", "start": 1}},
            dsn=dsn,
        )
        with pytest.raises(verdicts.VerdictRefused, match="does not hold"):
            await review(dsn, task, "governance_refused", [*ids, stray.judgement_id])
        with pytest.raises(verdicts.VerdictRefused, match="governance_refused"):
            await review(dsn, task, "pass", ids)
        with pytest.raises(verdicts.VerdictRefused, match="not governance instances"):
            await review(dsn, task, "governance_refused", ids, notes={"nope": {"incident": "x"}})
        gate = next(h for h in judgement_sites.diff_hunks(ws, older, newer) if h.path == "hooks/gate.py")
        await review(
            dsn,
            task,
            "governance_refused",
            ids,
            notes={gate.id: {"summary": "a push gate", "incident": "incident X", "mission_item": "1"}},
        )
        return ids, again, gate, (await rows(dsn, task, "review.decided"))[0]["payload"], older, newer

    ids, again, gate, decided, older, newer = run(go())
    hunks = judgement_sites.diff_hunks(ws, older, newer)
    assert again == ids and len(ids) == len(hunks) == 4  # docs/plan.md and three files; reused on rerun
    assert len([r for r in UP.seen(sid) if r["leg"] == "jev"]) == 4 + 1  # four hunks, one stray
    gov = decided["governance"]
    assert gov["adds"] is True and [i["id"] for i in gov["instances"]] == [gate.id]
    assert gov["instances"][0]["incident"] == "incident X" and gov["judgements"] == ids
    assert gov["abstain_instances"] == 0 and gov["unjudged_hunks"] == []
    from core import git as git_

    assert gate.id == git_.hunk_at(ws, older, newer, "hooks/gate.py", 1).id()


def test_the_hunk_input_carries_its_enclosing_function_but_the_id_does_not(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(default={"probs": NO})

    async def go():
        task = await governance_candidate(dsn, ws)
        older, newer = await candidate_range(dsn, task)
        await judgement_sites.governance(UP.port(script=sid), dsn, task, older, newer)
        return older, newer

    older, newer = run(go())
    sent = [
        r["body"]["state"]
        for r in UP.seen(sid)
        if r["leg"] == "jev" and r["body"]["state"]["path"] == "lib/handler.py"
    ]
    # the whole function, far beyond the three lines of context a plain hunk carries
    assert "def handler(request):" in sent[0]["hunk"] and "    step_0 = 0" in sent[0]["hunk"]
    from core import git as git_

    plain = git_.hunk_at(ws, older, newer, "lib/handler.py", 21)
    assert plain.added == ("    step_20 = 2000",)
    h = next(h for h in judgement_sites.diff_hunks(ws, older, newer) if h.path == "lib/handler.py")
    assert h.id == plain.id()


def test_a_reviewer_adds_caution_an_abstain_counts_and_tom_taps_each(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(default={"by_path": {"hooks/gate.py": UNSURE}, "probs": NO})

    async def go():
        task = await governance_candidate(dsn, ws)
        older, newer = await candidate_range(dsn, task)
        ids = await judgement_sites.governance(UP.port(script=sid), dsn, task, older, newer)
        util = verdicts.InstanceSpec("lib/util.py", 1, "the reviewer's own reading", "incident Y", "1")
        await review(dsn, task, "governance_refused", ids, governance=[util])
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "docs", "no_change")
        return task, (await rows(dsn, task, "review.decided"))[0]["payload"]

    task, decided = run(go())
    gov = decided["governance"]
    assert {i["path"] for i in gov["instances"]} == {"hooks/gate.py", "lib/util.py"}
    assert gov["abstain_instances"] == 1
    f = machine.fold(run(rows(dsn, task)))
    assert f.state is State.MERGE and len(f.ungranted()) == 2 and f.merge_effect is None


def test_governance_left_unanswered_reruns_then_makes_one_diff_level_instance(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(default={"fail_paths": ["lib/util.py", "lib/handler.py"], "probs": NO})

    async def go():
        task = await governance_candidate(dsn, ws)
        older, newer = await candidate_range(dsn, task)
        p = UP.port(script=sid)
        first = await judgement_sites.governance(p, dsn, task, older, newer)
        with pytest.raises(verdicts.VerdictRefused, match="governance unanswered"):
            await review(dsn, task, "pass", first)
        second = await judgement_sites.governance(p, dsn, task, older, newer)
        third = await judgement_sites.governance(p, dsn, task, older, newer)
        await review(dsn, task, "governance_refused", second)
        return first, second, third, (await rows(dsn, task, "review.decided"))[0]["payload"]

    first, second, third, decided = run(go())
    assert second == third and first != second
    gov = decided["governance"]
    assert len(gov["unjudged_hunks"]) == 2
    (instance,) = gov["instances"]  # one tap for the whole diff, not one per hunk
    assert instance["path"] == "(diff)" and instance["id"].startswith("unjudged-")
    assert "lib/handler.py" in instance["summary"] and "lib/util.py" in instance["summary"]


def test_a_hunk_too_large_for_both_legs_is_an_instance_at_once(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    sid = UP.script(default={"probs": NO})
    big = "".join(f"line_{i} = '{'x' * 60}'\n" for i in range(6_000))  # about 450 KB, one hunk

    async def go():
        task = await governance_candidate(dsn, ws, extra={"data/big.py": big})
        older, newer = await candidate_range(dsn, task)
        ids = await judgement_sites.governance(UP.port(script=sid), dsn, task, older, newer)
        await review(dsn, task, "governance_refused", ids)
        return (await rows(dsn, task, "review.decided"))[0]["payload"]

    decided = run(go())
    assert [i["path"] for i in decided["governance"]["instances"]] == ["data/big.py"]
    assert not [r for r in UP.seen(sid) if r["body"].get("state", {}).get("path") == "data/big.py"]


# -- calibration tasks -------------------------------------------------------------


def _cases(tmp_path, n=2) -> Path:
    item = tmp_path / "item.json"
    item.write_text(json.dumps({"request": "- [ ] `ListField(max_length=N)`", "repo": "yudame/popoto"}))
    cases = [
        {
            "id": "one-liner",
            "item": "item.json",
            "label": "thin",
            "sub_label": "one_line_ask",
            "source": "judge",
        },
        {
            "id": "precise",
            "request": "Fix the cache: len() must always execute.",
            "label": "precise",
            "source": "tom",
        },
    ]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"site": "intake.underspecified", "cases": (cases * n)[: max(n, 2)]}))
    return path


def test_a_calibration_task_meters_both_legs_and_takes_nothing_else(dsn, tmp_path):
    sid = UP.script(default={"probs": PRECISE})

    async def go():
        p = UP.port(script=sid)
        first = await judgement_sites.calibrate(p, dsn, _cases(tmp_path), 10_000)
        second = await judgement_sites.calibrate(p, dsn, _cases(tmp_path), 10_000)
        task = first["calibration_task"]
        started = (await rows(dsn, task, "task.started"))[0]["payload"]
        out = await drive(dsn, task)
        async with await db.connect(dsn) as conn:
            for refused in (
                verdicts.record_judge(conn, task, "x"),
                verdicts.record_check(conn, task, Check.TEST, "pass"),
                session.answer(conn, task, "x"),
                session.feedback(conn, task, "x"),
                guards.grant(conn, task, "i", note="x", incident="i", mission_item="1"),
                budget.raise_budget(conn, task, 1_000),
                tasks.stop(conn, task, reason="x"),
            ):
                with pytest.raises(LookupError, match="calibration task"):
                    await refused
            assert await verdicts.ensure_merge(conn, task) is None
            state = await tasks.status(conn, task)
        return first, second, started, out, state

    first, second, started, out, state = run(go())
    assert started["budget_usd_micros"] == 10_000 and started["calibration"] == "intake.underspecified"
    assert second["run"] == first["run"] + 1
    assert out["status"] == "calibration task" and state["calibration"] is True
    assert first["legs"]["jev"]["n"] == 2 and set(first["legs"]) == {"jev", "open_weight"}
    assert first["label_sources"] == {"tom": 1, "role_played": 0, "judge": 1}
    # both legs said precise: the one-liner is wrong on both, so the entry check fails
    assert first["entry_check"] is False and first["legs"]["jev"]["confusion"]["thin"] == {"precise": 1}
    assert state["charged_usd_micros"] > 0 and tasks.audit(state) == []
    assert [c["case"] for c in first["cases"]] == ["one-liner", "precise"]


def test_calibrate_refuses_a_missing_or_large_budget_and_too_many_cases(dsn, tmp_path):
    p = UP.port(fixed="precise")
    for bad in (0, 500_001):
        with pytest.raises(ValueError, match="calibration budget"):
            run(judgement_sites.calibrate(p, dsn, _cases(tmp_path), bad))
    with pytest.raises(ValueError, match="at most 50"):
        run(judgement_sites.calibrate(p, dsn, _cases(tmp_path, n=51), 10_000))
    out = subprocess.run(
        [sys.executable, "-m", "core", "calibrate", str(_cases(tmp_path))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "VALOR_DB": TEST_DB},
        check=False,
    )
    assert out.returncode == 2 and "--budget-usd" in out.stderr


def test_an_old_task_document_carrying_mode_still_loads(dsn):
    async def go():
        b = tasks.Brief(instruction="old", budget_usd_micros=0)
        body = {**tasks.asdict(b), "mode": "bare"}
        async with await db.connect(dsn) as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO documents (kind, id, body) VALUES ('task', %s, %s)",
                (b.id, judgement_upstream_jsonb(body)),
            )
        async with await db.connect(dsn) as conn:
            return await tasks.brief(conn, b.id), b

    loaded, b = run(go())
    assert loaded == b


def judgement_upstream_jsonb(value):
    from psycopg.types.json import Jsonb

    return Jsonb(value)


# -- the emulator's forced arm ------------------------------------------------------


def test_a_forced_arm_judges_through_a_local_upstream_and_its_rows_say_so(dsn, tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.judgement_upstream", "--answer", "thin"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        urls = json.loads(proc.stdout.readline())

        async def start():
            async with await db.connect(dsn) as conn:
                return await tasks.start(conn, tasks.Brief(instruction="x", budget_usd_micros=10_000))

        task = run(start())
        subprocess.run(
            [sys.executable, "-m", "core", "run", task],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "VALOR_DB": TEST_DB, **urls},
            check=False,
        )
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    decided = run(rows(dsn, task, "judge.decided"))[0]["payload"]
    answered = run(rows(dsn, task, "judgement.answered"))[0]["payload"]
    assert decided["verdict"] == "thin"
    assert answered["attempts"][0]["endpoint"] == "127.0.0.1"


def test_every_fold_of_a_calibration_start_is_a_calibration_task():
    start = {
        "id": 1,
        "type": "task.started",
        "payload": {"calibration": "intake.underspecified", "sdlc": 1, "budget_usd_micros": 1},
    }
    f = machine.fold([start, {"id": 2, "type": "judge.decided", "payload": {"verdict": "precise"}}])
    assert f.calibration and not f.legacy and f.state is State.JUDGE
    assert judgement.route(JUDGE)  # the router is unaffected
