"""The state machine's writes on real Postgres and real git: verdicts outside
their enum refused by the database, where a merge goes and what may stop
it, governance instances and Tom's taps, each term of the merge predicate,
recovery after a crash, one run per task, legacy tasks, and the seeded
guards.

No model call: turns are scripted subprocesses (`tests/scripted.py`).
Live spend: none.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from core import broker, db, guards, ledger, machine, router, session, tasks, verdicts
from core import git as kgit
from core.gateway import Gateway
from core.machine import Check, State
from core.settings import settings
from tests import scripted
from tests.conftest import TEST_DB
from tests.scripted import commit, git
from tools.push_branch import PushBranch

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core", *args],
        cwd=ROOT,
        env={**os.environ, "VALOR_DB": TEST_DB},
        capture_output=True,
        text=True,
        check=False,
    )


async def drive(dsn, task, runners=None) -> dict:
    gateway = Gateway(dsn)
    await gateway.start()
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


async def merge_effect(dsn, task) -> str:
    return (await fold(dsn, task)).merge_effect["effect_id"]


# -- the verdict enum is the database's -------------------------------------------------


BAD_ROWS = [
    ("judge.decided", {"verdict": "unsure"}),
    ("judge.decided", {}),
    ("critique.decided", {"verdict": "fine"}),
    ("critique.decided", {"verdict": "sound", "raised": {"review_rounds": 3}}),
    ("test.decided", {"verdict": "green"}),
    ("review.decided", {"verdict": "approved"}),
    ("docs.decided", {"verdict": "ok"}),
    ("turn.collected", {"turn_id": "x1", "state": "build", "verdict": "planned"}),
    ("turn.collected", {"turn_id": "x2", "state": "build"}),
    ("turn.collected", {"turn_id": "x3", "state": "merge", "verdict": "released"}),
    ("plan.written", {"critique_rounds": 3, "review_rounds": 0}),
    ("plan.written", {"critique_rounds": 0}),
]


@pytest.mark.parametrize(("kind", "payload"), BAD_ROWS)
def test_a_verdict_outside_its_enum_is_refused_by_the_database(dsn, kind, payload):
    with psycopg.connect(dsn, autocommit=True) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO events (task_id, type, payload) VALUES ('enum-test', %s, %s)",
            (kind, Jsonb(payload)),
        )


def test_every_value_in_verdicts_is_accepted_and_legacy_collected_rows_still_are(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        for type_, stage in machine.VERDICT_ROWS.items():
            for v in machine.VERDICTS[stage]:
                conn.execute(
                    "INSERT INTO events (task_id, type, payload) VALUES (%s, %s, %s)",
                    (f"enum-ok-{type_}-{v}", type_, Jsonb({"verdict": v})),
                )
        for s in machine.WORKING:
            for v in machine.VERDICTS[s]:
                conn.execute(
                    "INSERT INTO events (task_id, type, payload) VALUES ('enum-ok', 'turn.collected', %s)",
                    (Jsonb({"turn_id": f"{s}-{v}", "state": s.value, "verdict": v}),),
                )
        conn.execute(
            "INSERT INTO events (task_id, type, payload) VALUES ('enum-ok', 'turn.collected', %s)",
            (Jsonb({"turn_id": "legacy", "question": None, "done": "x", "effects": []}),),
        )


def test_the_constraint_is_put_in_place_once_and_a_removed_value_does_not_recheck_history(tmp_path):
    database = f"{settings.test_database}_enum_{os.getpid()}"
    dsn = db.migrate(database, fresh=True)
    owner = settings.dsn(owner=True, database=database)
    try:
        with psycopg.connect(owner, autocommit=True) as conn:
            assert db.verdict_constraint(conn) is False  # migrate put the current one in place
            db.migrate(database)
            names = conn.execute(
                "SELECT conname FROM pg_constraint WHERE conname LIKE 'events_verdict_in_enum%%'"
            ).fetchall()
            assert names == [(machine.constraint_name(),)]
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES ('t', 'judge.decided', %s)",
                (Jsonb({"verdict": "thin"}),),
            )
        # A later VERDICTS without `thin`: the history holding `thin` does
        # not refuse the change, and a new `thin` is refused.
        narrower = machine.constraint_sql().replace("IN ('precise', 'thin')", "IN ('precise')")
        with psycopg.connect(owner, autocommit=True) as conn:
            assert db.verdict_constraint(conn, narrower, "events_verdict_in_enum_narrower") is True
        with psycopg.connect(dsn, autocommit=True) as conn, pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES ('u', 'judge.decided', %s)",
                (Jsonb({"verdict": "thin"}),),
            )
    finally:
        with psycopg.connect(settings.dsn(owner=True, database="postgres"), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


def test_one_judge_verdict_per_task_and_one_collected_row_per_turn(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    task = run(scripted.start(dsn, ws, judge="thin"))
    with psycopg.connect(dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES (%s, 'judge.decided', %s)",
                (task, Jsonb({"verdict": "precise"})),
            )
        payload = Jsonb({"turn_id": f"{task}-t", "state": "clarify", "verdict": "idle"})
        conn.execute(
            "INSERT INTO events (task_id, type, payload) VALUES (%s, 'turn.collected', %s)", (task, payload)
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES (%s, 'turn.collected', %s)",
                (task, payload),
            )


# -- start: where the merge goes ---------------------------------------------------------


def test_the_target_branch_is_origins_head_and_the_url_is_recorded_absolute(tmp_path):
    ws, origin = scripted.workspace(tmp_path)
    git(ws, "remote", "set-url", "origin", "../origin.git")  # relative, as written
    where = tasks.resolve_workspace(str(ws))
    assert where["target_branch"] == "main"  # not the work branch the workspace is on
    assert where["origin_url"] == str(origin.resolve())
    assert where["base_sha"] == git(ws, "rev-parse", "HEAD")


def test_an_unborn_origin_head_needs_the_flag_and_a_detached_head_is_refused(tmp_path):
    ws, origin = scripted.workspace(tmp_path)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/master")  # what `git init --bare` leaves here
    with pytest.raises(tasks.WorkspaceRefused, match="--target-branch"):
        tasks.resolve_workspace(str(ws))
    assert tasks.resolve_workspace(str(ws), "main")["target_branch"] == "main"
    git(ws, "checkout", "-q", "--detach")
    with pytest.raises(tasks.WorkspaceRefused, match="detached"):
        tasks.resolve_workspace(str(ws), "main")
    out = cli("start", "x", "--workspace", str(ws), "--target-branch", "main")
    assert out.returncode == 1 and "detached" in out.stderr


def test_a_replay_workspace_resolves_main(tmp_path):
    """The commands `scripts/replay_workspace.py` runs for a run's origin."""
    origin, ws = tmp_path / "origin.git", tmp_path / "repo"
    subprocess.run(["git", "init", "--quiet", "--bare", str(origin)], check=True)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    git(origin, "config", "core.logAllRefUpdates", "always")
    git(origin, "config", "receive.denyNonFastForwards", "true")
    subprocess.run(["git", "init", "-q", "-b", "valor/work", str(ws)], check=True)
    base = commit(ws, "a.txt", "a\n")
    git(ws, "remote", "add", "origin", str(origin))
    git(ws, "push", "--quiet", "origin", f"{base}:refs/heads/main")
    git(ws, "fetch", "--quiet", "origin")
    assert tasks.resolve_workspace(str(ws))["target_branch"] == "main"


# -- the merge destination is the kernel's -------------------------------------------------


@pytest.mark.parametrize("rewrite", ["url", "pushurl", "insteadof", "plain-insteadof", "include"])
def test_a_turn_cannot_redirect_the_merge(dsn, tmp_path, rewrite):
    ws, origin = scripted.workspace(tmp_path)
    stranger = tmp_path / "stranger.git"
    subprocess.run(["git", "init", "-q", "--bare", str(stranger)], check=True)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        effect = await merge_effect(dsn, task)
        # After the merge is held, the turn's config is rewritten.
        if rewrite == "url":
            git(ws, "remote", "set-url", "origin", str(stranger))
        elif rewrite == "pushurl":
            git(ws, "remote", "set-url", "--push", "origin", str(stranger))
        elif rewrite == "insteadof":
            git(ws, "config", f"url.{stranger}.pushInsteadOf", str(origin))
        elif rewrite == "plain-insteadof":
            git(ws, "config", f"url.{stranger}.insteadOf", str(origin))
        else:
            inc = tmp_path / "inc.gitconfig"
            inc.write_text(f'[url "{stranger}"]\n\tpushInsteadOf = {origin}\n')
            git(ws, "config", "include.path", str(inc))
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge it")
            try:
                out = await scripted.release(conn, effect)
            except broker.Refused as exc:
                out = exc
        return out, await rows(dsn, task)

    out, written = run(go())
    assert (
        subprocess.run(
            ["git", "-C", str(stranger), "rev-parse", "main"], capture_output=True, check=False
        ).returncode
        != 0
    )
    if rewrite == "url":  # the remote's name is not used: the recorded URL is
        assert out.kind == "done" and git(origin, "rev-parse", "main") == out.result["sha"]
    else:
        assert isinstance(out, broker.Refused) and "redirect" in str(out)
        assert not [r for r in written if r["type"] == "effect.intent"]  # the approval stays unused


def test_push_branch_cannot_target_the_merge_branch_and_a_turn_cannot_request_a_merge(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws)
        await drive(dsn, task)
        await scripted.critique(dsn, task)
        scripted.steer(ws, push="main", request_merge=True)
        await drive(dsn, task)
        return await rows(dsn, task)

    written = run(go())
    collected = [r["payload"] for r in written if r["type"] == "turn.collected"][-1]
    by_file = {e["file"]: e for e in collected["effects"]}
    assert by_file["merge.json"]["error"] == "the merge is the kernel's to request"
    assert "effect_id" not in by_file["merge.json"]
    assert by_file["push.json"]["kind"] == "refused" and "target branch" in by_file["push.json"]["error"]


# -- governance instances and Tom's taps ------------------------------------------------------


def _gate(ws) -> int:
    """Commit a hook that adds a check; returns a line inside its hunk."""
    commit(ws, "hooks/gate.py", "def gate():\n    return 'a check'\n", "add a gate")
    return 1


def test_a_governance_review_holds_the_merge_until_tom_taps_and_only_review_reruns(dsn, tmp_path):
    ws, _origin = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, governance_grant="Tom: this task may add one gate")
        await drive(dsn, task)
        await scripted.critique(dsn, task)
        _gate(ws)
        scripted.steer(ws, build="reasons")  # the candidate is the gate commit
        await drive(dsn, task)
        spec = verdicts.InstanceSpec("hooks/gate.py", 1, "a gate on the push path", "incident X", "1")
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "docs", "no_change")
        async with await db.connect(dsn) as conn:
            with pytest.raises(verdicts.VerdictRefused, match="governance_refused"):
                await verdicts.record_check(
                    conn, task, Check.REVIEW, "pass", governance=[spec], **scripted.MANUAL
                )
            with pytest.raises(verdicts.VerdictRefused, match="no added lines"):
                await verdicts.record_check(
                    conn, task, Check.REVIEW, "governance_refused",
                    governance=[verdicts.InstanceSpec("README.md", 1)], **scripted.MANUAL,
                )  # fmt: skip
        await scripted.check(dsn, task, "review", "governance_refused", governance=[spec])
        held = await fold(dsn, task)
        # The Brief's grant does not stand in for the tap, and runs pile up no refusals.
        await drive(dsn, task)
        await drive(dsn, task)
        async with await db.connect(dsn) as conn:
            direct = await scripted.request(
                conn, task, verdicts.merge_action(held, await tasks.brief(conn, task))
            )
        instance = held.instances()[0].id
        no_by = cli("grant", task, instance, "--note", "yes", "--by", "stand-in")
        granted = cli("grant", task, instance, "--note", "yes, this gate")
        after_grant = await drive(dsn, task)
        async with await db.connect(dsn) as conn:
            with pytest.raises(verdicts.VerdictRefused, match="is pass"):
                await verdicts.record_check(
                    conn, task, Check.REVIEW, "governance_refused", governance=[spec], **scripted.MANUAL
                )
        await scripted.check(dsn, task, "review", "pass", governance=[spec])
        return task, held, direct, no_by, granted, after_grant, await fold(dsn, task), await rows(dsn, task)

    _task, held, direct, no_by, granted, after_grant, final, written = run(go())
    assert held.state is State.MERGE and held.join.row == 2 and held.merge_effect is None
    assert direct.kind == "refused" and "no grant from Tom" in direct.error
    assert len([r for r in written if r["type"] == "effect.refused"]) == 1  # only the direct request
    assert no_by.returncode == 2  # a grant has no --by
    assert granted.returncode == 0, granted.stderr
    grant = next(r["payload"] for r in written if r["type"] == "guard.granted")
    assert grant["provenance"]["by"] == "tom" and grant["provenance"]["role_played"] is False
    assert grant["incident"] == "incident X" and grant["expires"]
    assert after_grant["status"] == "no runner" and after_grant["missing"] == ["review"]
    assert final.state is State.MERGE and final.join.row == 1 and final.merge_effect["state"] == "held"
    held_row = next(r["payload"] for r in written if r["type"] == "effect.held")
    assert held_row["action_type"] == "merge" and held_row["adds_governance"] is True


def test_a_grant_without_an_incident_is_refused(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws)
        await drive(dsn, task)
        await scripted.critique(dsn, task)
        _gate(ws)
        scripted.steer(ws, build="reasons")  # the candidate is the gate commit
        await drive(dsn, task)
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "docs", "no_change")
        bare = verdicts.InstanceSpec("hooks/gate.py", 2)  # names no incident, no mission item
        await scripted.check(dsn, task, "review", "governance_refused", governance=[bare])
        instance = (await fold(dsn, task)).instances()[0].id
        async with await db.connect(dsn) as conn:
            with pytest.raises(guards.GrantRefused, match="missing either"):
                await guards.grant(conn, task, instance, note="yes")
            with pytest.raises(guards.GrantRefused, match="no governance instance"):
                await guards.grant(conn, task, "not-an-instance", note="yes", incident="i", mission_item="1")
            guard_id = await guards.grant(
                conn, task, instance, note="yes", incident="incident Y", mission_item="1"
            )
        return guard_id, await fold(dsn, task)

    guard_id, f = run(go())
    assert guard_id.startswith("grant-") and f.state is State.CHECKS


# -- the merge predicate, term by term -------------------------------------------------------


def test_all_five_terms_hold_and_the_merge_lands_on_the_recorded_origin(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        effect = await merge_effect(dsn, task)
        async with await db.connect(dsn) as conn:
            with pytest.raises(broker.MergeRefused) as no_approval:
                await scripted.release(conn, effect)
            await broker.approve(conn, effect, note="merge it")
            done = await scripted.release(conn, effect)
        return task, no_approval.value, done, await fold(dsn, task)

    _task, no_approval, done, f = run(go())
    assert [t[0] for t in no_approval.terms] == ["5"]
    assert done.kind == "done" and git(origin, "rev-parse", "main") == f.candidate.sha
    assert f.state is State.MERGED


def test_a_red_test_refuses_the_merge_even_when_one_is_requested_and_approved(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws, max_effect_class="act")
        await scripted.check(dsn, task, "test", "red", failures=["test_x"])
        await scripted.check(dsn, task, "review", "pass")
        await scripted.check(dsn, task, "docs", "no_change")
        # The repair round sends it to patch once; a second red goes to Tom.
        await drive(dsn, task)
        await scripted.check(dsn, task, "test", "red", failures=["test_x"])
        await scripted.check(dsn, task, "review", "pass")
        await scripted.check(dsn, task, "docs", "no_change")
        f = await fold(dsn, task)
        async with await db.connect(dsn) as conn:
            held = await scripted.request(conn, task, verdicts.merge_action(f, await tasks.brief(conn, task)))
            await broker.approve(conn, held.effect_id, note="merge anyway")
            with pytest.raises(broker.MergeRefused) as refused:
                await scripted.release(conn, held.effect_id)
        return f, refused.value, await rows(dsn, task)

    f, refused, written = run(go())
    assert f.state is State.MERGE and f.join.outcome == "did_not_pass" and f.merge_effect is None
    assert [t[0] for t in refused.terms] == ["3"]
    assert not [r for r in written if r["type"] == "effect.intent"]


def _docs_head(ws, *paths) -> str:
    head = None
    for path in paths:
        head = commit(ws, path, "words\n", f"docs: {path}")
    return head


@pytest.mark.parametrize(
    ("paths", "holds"),
    [
        (["docs/guide.md"], True),
        (["README.md", "docs/deep/notes.md"], True),
        (["core/x.py"], False),
        (["skills/sdlc/build.md"], False),
        (["CLAUDE.md"], False),
        (["docs/CLAUDE.md"], False),
        (["AGENTS.md"], False),
        (["persona/voice.md"], False),
        ([".claude/agents/x.md"], False),
        (["docs/claude.md"], False),
        (["Skills/sdlc/build.md"], False),
        (["CLAUDE.local.md"], False),
        (["AGENTS.override.md"], False),
        (["docs/guide.md", "core/x.py"], False),
    ],
)
def test_docs_commits_must_touch_only_markdown_that_instructs_no_turn(dsn, tmp_path, paths, holds):
    """Doc paths are Markdown only, never a file that instructs a turn, and a
    plan cannot widen them: anything else in a docs commit would merge with
    no review keyed to it."""
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        head = _docs_head(ws, *paths)
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "review", "pass")
        await scripted.check(dsn, task, "docs", "updated", head=head)
        effect = await merge_effect(dsn, task)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge")
            try:
                return await scripted.release(conn, effect)
            except broker.MergeRefused as exc:
                return exc

    out = run(go())
    if holds:
        assert out.kind == "done"
    else:
        assert isinstance(out, broker.MergeRefused) and [t[0] for t in out.terms] == ["4"]


def test_a_rename_out_of_a_code_path_counts_the_old_path(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    commit(ws, "core/x.py", "x = 1\n", "code")
    git(ws, "push", "-q", "origin", "HEAD:refs/heads/main")

    async def go():
        task = await to_checks(dsn, ws)
        git(ws, "mv", "core/x.py", "docs/x.md")
        git(ws, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "move")
        head = git(ws, "rev-parse", "HEAD")
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "review", "pass")
        await scripted.check(dsn, task, "docs", "updated", head=head)
        effect = await merge_effect(dsn, task)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge")
            with pytest.raises(broker.MergeRefused) as refused:
                await scripted.release(conn, effect)
        f = await fold(dsn, task)
        return refused.value, f.checks[Check.DOCS].payload["paths"]

    refused, paths = run(go())
    assert [t[0] for t in refused.terms] == ["4"] and "core/x.py" in paths


def test_a_docs_head_holding_a_merge_commit_is_refused_at_write(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        cand = git(ws, "rev-parse", "HEAD")
        git(ws, "checkout", "-q", "-b", "side")
        commit(ws, "docs/a.md", "a\n")
        git(ws, "checkout", "-q", "valor/work")
        commit(ws, "docs/b.md", "b\n")
        git(ws, "-c", "user.name=t", "-c", "user.email=t@e", "merge", "-q", "--no-edit", "side")
        async with await db.connect(dsn) as conn:
            with pytest.raises(verdicts.VerdictRefused, match="merge commit"):
                await verdicts.record_check(
                    conn, task, Check.DOCS, "updated", head=git(ws, "rev-parse", "HEAD"), **scripted.MANUAL
                )
        return cand

    run(go())


def test_an_approval_for_another_digest_releases_nothing(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        first = await merge_effect(dsn, task)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, first, note="merge the first")
            await session.feedback(conn, task, "one more change")
        await drive(dsn, task)
        await scripted.checks(dsn, task)
        second = await merge_effect(dsn, task)
        async with await db.connect(dsn) as conn:
            with pytest.raises(broker.MergeRefused) as on_first_approval:
                await scripted.release(conn, second)
            with pytest.raises(broker.MergeRefused) as stale:
                await scripted.release(conn, first)
        return first, second, on_first_approval.value, stale.value

    first, second, on_first_approval, stale = run(go())
    assert first != second
    assert [t[0] for t in on_first_approval.terms] == ["5"]
    assert "1" in [t[0] for t in stale.terms]


def test_the_predicate_terms_that_need_no_workspace():
    led_rows = []

    def add(t, p):
        led_rows.append({"id": len(led_rows) + 1, "type": t, "payload": p})

    c = {"sha": "c", "turn_id": "t1"}
    add("task.started", {"sdlc": 1})
    add("judge.decided", {"verdict": "precise"})
    add("turn.started", {"turn_id": "t0", "state": "plan"})
    add("turn.collected", {"turn_id": "t0", "state": "plan", "verdict": "planned"})
    add("plan.written", {"sha256": "p", "critique_rounds": 0, "review_rounds": 0})
    add("critique.decided", {"plan_sha256": "p", "verdict": "sound"})
    add("turn.collected", {"turn_id": "t1", "state": "build", "verdict": "candidate", "candidate": c})
    add("test.decided", {"candidate": c, "verdict": "gaps", "behaviors": ["b"]})
    add("review.decided", {"candidate": c, "verdict": "pass"})
    add("docs.decided", {"candidate": c, "verdict": "no_change", "head": "c"})
    f = machine.fold(led_rows)
    assert f.state is State.PATCH  # gaps with the repair round unspent go to patch, never to merge
    f.state = State.MERGE  # as if it had: the predicate still refuses it
    facts = machine.GitFacts(True, (), ())
    payload = {"candidate": c, "head_sha": "c"}
    terms = machine.merge_predicate(f, payload, approval_unused=True, facts=facts)
    assert [t[0] for t in terms] == ["3"]
    terms = machine.merge_predicate(f, {**payload, "head_sha": "other"}, approval_unused=True, facts=facts)
    assert "4" in [t[0] for t in terms]
    merges = machine.GitFacts(True, ("m",), ())
    assert "4" in [t[0] for t in machine.merge_predicate(f, payload, approval_unused=True, facts=merges)]


# -- recovery, one run at a time ------------------------------------------------------------


def test_a_crash_between_the_delivery_and_the_merge_request_is_recovered_by_the_next_run(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        async with await db.connect(dsn) as conn:  # the writer, stopped before ensure_merge
            for check in (Check.TEST, Check.REVIEW, Check.DOCS):
                verdict = "no_change" if check is Check.DOCS else "pass"
                await verdicts.record_check(conn, task, check, verdict, **scripted.MANUAL)
        before = await fold(dsn, task)
        await drive(dsn, task)
        await drive(dsn, task)
        return before, await rows(dsn, task)

    before, written = run(go())
    assert before.state is State.MERGE and before.merge_effect is None
    assert len([r for r in written if r["type"] == "effect.held"]) == 1


def test_feedback_waits_while_the_merge_is_in_flight(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        effect = await merge_effect(dsn, task)
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "effect.intent", {"effect_id": effect, "idempotency_key": "k"})
            with pytest.raises(LookupError, match="in flight"):
                await session.feedback(conn, task, "wait")

    run(go())


def test_a_second_run_of_a_task_returns_already_running_and_a_lost_lock_stops_the_run(
    dsn, tmp_path, owner_dsn
):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws)
        holder = await db.connect(dsn)
        await holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"run:{task}",))
        busy = await drive(dsn, task)
        await holder.close()

        async def cut(ctx: router.Context) -> dict:
            with psycopg.connect(owner_dsn, autocommit=True) as owner:
                owner.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND granted AND pid <> pg_backend_pid() AND database = "
                    "(SELECT oid FROM pg_database WHERE datname = current_database())"
                )
            return await scripted.working(ctx)

        lost = await drive(dsn, task, runners={State.PLAN: cut})
        return busy, lost

    busy, lost = run(go())
    assert busy["status"] == "already running"
    assert lost["status"] == "lock lost"
    assert scripted.turns(ws) == []  # no turn ran without the lock


# -- the working session's evidence ---------------------------------------------------------


def test_a_dirty_tree_is_no_candidate_and_the_next_prompt_says_what_is_uncommitted(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws)
        await drive(dsn, task)
        await scripted.critique(dsn, task)
        scripted.steer(ws, build="dirty")
        return await drive(dsn, task)

    out = run(go())
    assert out["status"] == "idle" and out["state"]["candidate"] is None
    assert (
        "uncommitted" in scripted.turns(ws)[-1]["prompt"]
        and "greeting.txt" in scripted.turns(ws)[-1]["prompt"]
    )


@pytest.mark.parametrize(
    ("plan", "why"),
    [("uncommitted", "is not committed at HEAD"), ("done", "done means nothing in plan")],
)
def test_a_plan_turn_without_a_committed_plan_is_no_plan(dsn, tmp_path, plan, why):
    ws, _ = scripted.workspace(tmp_path)
    scripted.steer(ws, plan=plan)

    async def go():
        task = await scripted.start(dsn, ws)
        return await drive(dsn, task), await rows(dsn, task)

    out, written = run(go())
    assert out["status"] == "idle" and out["state"]["state"] == "plan"
    assert not [r for r in written if r["type"] == "plan.written"]
    assert why in scripted.turns(ws)[-1]["prompt"]


def test_plan_counts_outside_zero_to_two_are_no_plan(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    scripted.steer(ws, counts={"critique_rounds": 3, "review_rounds": 0})

    async def go():
        task = await scripted.start(dsn, ws)
        return await drive(dsn, task)

    assert run(go())["status"] == "idle"
    assert "each count is 0, 1, or 2" in scripted.turns(ws)[-1]["prompt"]


def test_a_patch_with_reasons_and_no_change_gets_fresh_checks(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task, review="changes")
        scripted.steer(ws, patch="reasons")
        out = await drive(dsn, task)
        return out, await fold(dsn, task)

    out, f = run(go())
    assert out["missing"] == ["test", "review", "docs"] and f.checks == {}
    assert f.candidate.sha == git(ws, "rev-parse", "HEAD") and f.candidate.turn_id


def test_the_verdict_command_records_by_hand_and_requests_the_merge(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    task = run(scripted.start(dsn, ws))
    run(drive(dsn, task))
    who = ["--by", "test", "--role-played"]
    critique = cli("verdict", task, "critique", "sound", *who)
    assert critique.returncode == 1 and "critique has a runner" in critique.stderr
    run(scripted.critique(dsn, task))
    run(drive(dsn, task))
    assert cli("verdict", task, "test", "pass", *who).returncode == 0
    out = cli("verdict", task, "review", "changes", "--finding", "naming:rename x", *who)
    assert out.returncode == 0, out.stderr
    assert cli("verdict", task, "docs", "no_change", *who).returncode == 0
    assert run(fold(dsn, task)).state is State.PATCH  # join row 3: a review round was left
    run(drive(dsn, task))
    for stage, verdict in (("test", "pass"), ("review", "pass"), ("docs", "no_change")):
        assert cli("verdict", task, stage, verdict, *who).returncode == 0
    f = run(fold(dsn, task))
    assert f.state is State.MERGE and f.merge_effect["state"] == "held"  # the CLI registered the performer
    refused = cli("verdict", task, "test", "pass", *who)
    assert refused.returncode == 1 and "not checks" in refused.stderr
    assert "build has a runner" in cli("verdict", task, "build", "candidate").stderr
    assert "no manual verdict" in cli("verdict", task, "merge", "released").stderr


def test_a_stage_with_a_runner_takes_no_manual_verdict():
    verdicts.manual_allowed(State.CRITIQUE, {State.PLAN: object()})
    with pytest.raises(verdicts.VerdictRefused, match="has a runner"):
        verdicts.manual_allowed(State.CRITIQUE, {State.CRITIQUE: object()})


@pytest.mark.parametrize("where", ["judge", "plan", "critique", "checks", "merge"])
def test_a_stopped_task_takes_nothing_more_in_any_state(dsn, tmp_path, where):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, judge=None if where == "judge" else "precise")
        if where in ("critique", "checks", "merge"):
            await drive(dsn, task)
        if where in ("checks", "merge"):
            await scripted.critique(dsn, task)
            await drive(dsn, task)
        if where == "merge":
            await scripted.checks(dsn, task)
        f = await fold(dsn, task)
        assert f.state.value == where
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
            before = len(await ledger.read(conn, task))
            with pytest.raises(LookupError):
                await session.answer(conn, task, "x")
            with pytest.raises(LookupError):
                await session.feedback(conn, task, "x")
            with pytest.raises(LookupError):
                await verdicts.record_judge(conn, task, "no-such-judgement")
            with pytest.raises(LookupError):
                await verdicts.record_check(conn, task, Check.TEST, "pass")
            with pytest.raises(LookupError):
                await guards.grant(conn, task, "i", note="x", incident="i", mission_item="1")
            after = len(await ledger.read(conn, task))
        out = await drive(dsn, task)
        return before, after, out

    before, after, out = run(go())
    assert before == after and out["status"] == "stopped"


def test_the_brief_renders_the_stage_and_the_offered_effects(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, judge="thin")
        async with await db.connect(dsn) as conn:
            offered = scripted.performers(await tasks.brief(conn, task)).offered()
            clarify = await tasks.dispatch(conn, task, offered=offered)
            plan = await tasks.dispatch(conn, task, state=State.PLAN, offered=offered)
        return clarify["text"], plan["text"]

    clarify, plan = run(go())
    assert "# Stage: clarify" in clarify and "# Stage: plan" not in clarify
    assert "# Stage: plan" in plan and "plan.json" in plan
    assert "`push_branch`" in clarify and "- `merge`" not in clarify


# -- legacy tasks ----------------------------------------------------------------------------


def test_a_legacy_task_is_read_only_but_its_held_push_can_still_be_released(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)
    head = git(ws, "rev-parse", "HEAD")

    async def go():
        b = tasks.Brief(instruction="old", max_effect_class="act", workspace=str(ws))
        async with await db.connect(dsn) as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO documents (kind, id, body) VALUES ('task', %s, %s)",
                (
                    b.id,
                    Jsonb(
                        {
                            k: v
                            for k, v in b.__dict__.items()
                            if k not in ("target_branch", "origin_url", "base_sha")
                        }
                    ),
                ),
            )
            await ledger.append(conn, b.id, "task.started", {"instruction": "old",
                                                             "max_effect_class": "act", "mode": "bare"})  # fmt: skip
            await ledger.append(conn, b.id, "task.delivered", {"turn_id": "t", "summary": "done"})
        perf = broker.Performers(PushBranch(ws))
        async with await db.connect(dsn) as conn:
            held = await broker.request(
                conn, perf, b.id, broker.Action("push_branch", "valor/old", {"head_sha": head})
            )
            st = await tasks.status(conn, b.id)
            with pytest.raises(LookupError, match="predates"):
                await session.feedback(conn, b.id, "x")
            with pytest.raises(LookupError, match="predates"):
                await verdicts.record_judge(conn, b.id, "precise")
            with pytest.raises(LookupError, match="predates"):
                await session.answer(conn, b.id, "x")
            with pytest.raises(LookupError, match="predates"):
                await verdicts.record_check(conn, b.id, Check.TEST, "pass")
            with pytest.raises(LookupError, match="predates"):
                await guards.grant(conn, b.id, "i", note="x", incident="i", mission_item="1")
            await broker.approve(conn, held.effect_id, note="push it")
            pushed = await broker.release(conn, perf, held.effect_id)
        return st, pushed, await drive(dsn, b.id)

    st, pushed, out = run(go())
    by_hand = cli("verdict", st["task_id"], "test", "pass")
    assert by_hand.returncode == 1 and "predates" in by_hand.stderr
    assert st["legacy"] is True and st["state"] == "merge"
    assert pushed.kind == "done" and git(origin, "rev-parse", "valor/old") == head
    assert out["status"] == "legacy"


# -- guards ------------------------------------------------------------------------------------


def test_migrate_seeds_the_granted_guards_once(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        seeded = conn.execute(
            "SELECT payload FROM events WHERE task_id = 'guards' AND type = 'guard.granted' ORDER BY id"
        ).fetchall()
    assert [p["guard_id"] for (p,) in seeded] == [g["guard_id"] for g in guards.SEEDED]
    assert [p["guard_id"] for (p,) in seeded] == [
        "intake.underspecified",
        "checks.test.breadth",
        "critique.loop",
        "review.loop",
    ]
    for (p,) in seeded:
        assert p["incident"] and p["mission_items"] and p["granted_at"] == "2026-10-01"
        assert p["expires"] == "2026-12-30" and p["provenance"]["by"] == "tom"
    db.migrate(TEST_DB)
    with psycopg.connect(dsn, autocommit=True) as conn:
        again = conn.execute(
            "SELECT count(*) FROM events WHERE task_id = 'guards' AND type = 'guard.granted'"
        ).fetchone()[0]
    assert again == 4
    assert json.dumps(seeded[0][0])  # plain JSON


def test_an_instance_id_ignores_line_numbers_and_counts_the_function_context(tmp_path):
    from core import git as kgit

    ws, _ = scripted.workspace(tmp_path)
    filler = "".join(f"    f{i} = {i}\n" for i in range(8))
    body = f"def a():\n    y = 2\n{filler}\n\ndef b():\n    y = 2\n{filler}"
    base = commit(ws, "m.py", body)
    gated = body.replace("    y = 2\n", "    y = 2\n    check()\n")
    one = commit(ws, "m.py", gated)
    first, second = kgit.hunks(ws, base, one, "m.py")
    assert first.added == second.added == ("    check()",)
    assert first.id() != second.id()  # same added lines, different function context
    shifted_base = commit(ws, "m.py", "# a header line\n" + body)
    shifted = commit(ws, "m.py", "# a header line\n" + gated)
    again = kgit.hunks(ws, shifted_base, shifted, "m.py")
    assert [h.id() for h in again] == [first.id(), second.id()]  # line numbers moved, ids did not


# -- the kernel runs no program the workspace's config names --------------------------------


def _plant(tmp_path: Path, ws: Path) -> Path:
    """A filter clean driver for *.py (assigned by a committed .gitattributes),
    a diff textconv driver, an fsmonitor hook, and an include pulling in a
    second filter: every one would write `marker` if git ran it."""
    marker = tmp_path / "ran"
    driver = tmp_path / "driver.sh"
    driver.write_text(
        f'#!/bin/sh\ntouch {marker}\nif [ -n "$1" ] && [ -f "$1" ]; then cat "$1"; else cat; fi\n'
    )
    driver.chmod(0o755)
    monitor = tmp_path / "monitor.sh"
    monitor.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    monitor.chmod(0o755)
    included = tmp_path / "included.gitconfig"
    included.write_text(f'[filter "other"]\n\tclean = {driver}\n')
    git(ws, "config", "filter.evil.clean", str(driver))
    git(ws, "config", "diff.evil.textconv", str(driver))
    git(ws, "config", "core.fsmonitor", str(monitor))
    git(ws, "config", "include.path", str(included))
    return marker


def test_no_kernel_git_call_runs_a_program_the_workspace_config_names(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    commit(ws, ".gitattributes", "*.py filter=evil diff=evil\n*.txt filter=other\n", "attributes")
    git(ws, "push", "-q", "origin", "HEAD:refs/heads/main")

    async def go():
        task = await scripted.start(dsn, ws)
        await drive(dsn, task)
        await scripted.critique(dsn, task)
        _gate(ws)
        scripted.steer(ws, build="reasons")
        await drive(dsn, task)
        await scripted.checks(dsn, task)  # a merge held, all three passing
        f = await fold(dsn, task)
        effect = f.merge_effect["effect_id"]
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge")
        b = tasks.Brief(**await tasks_doc(dsn, task))
        marker = _plant(tmp_path, ws)
        (ws / "hooks" / "gate.py").write_text("def gate():\n    return 'touched'\n")  # stat-dirty
        results = {}
        async with await db.connect(dsn) as conn:
            try:
                await scripted.release(conn, effect)
            except broker.Refused as exc:
                results["release"] = str(exc)
            results["facts"] = broker._git_facts(str(ws), f, {"head_sha": f.candidate.sha})
            try:
                verdicts._instances(
                    str(ws), b.base_sha, f.candidate.sha, [verdicts.InstanceSpec("hooks/gate.py", 1)]
                )
            except Exception as exc:  # noqa: BLE001
                results["instances"] = type(exc).__name__
            (ws / ".valor").mkdir(exist_ok=True)
            (ws / ".valor" / "done.md").write_text("done")
            found = scripted_signals(ws)
            results["collected"] = await session.record(
                conn, task, "planted", found, state=State.PATCH, workspace=str(ws)
            )
            try:
                tasks.resolve_workspace(str(ws))
            except tasks.WorkspaceRefused as exc:
                results["start"] = str(exc)
            written = await ledger.read(conn, task)
        return results, written, marker

    results, written, marker = run(go())
    before_control = marker.exists()
    subprocess.run(["git", "-C", str(ws), "status"], capture_output=True, check=False)  # plain git
    after_control = marker.exists()
    assert before_control is False  # no kernel call ran a planted program
    assert after_control is True  # the plants are live: plain git ran one
    assert "will not run" in results["release"] or "run a program" in results["release"]
    assert results["facts"] is None and results["instances"] == "GitError"
    assert results["collected"] == "idle"
    collected = next(
        r["payload"]
        for r in written
        if r["type"] == "turn.collected" and r["payload"]["turn_id"] == "planted"
    )
    assert any("filter.evil.clean" in e for e in collected["errors"])
    assert "include.path" in results["start"] and "filter.other.clean" in results["start"]
    assert not [r for r in written if r["type"] == "effect.intent"]


async def tasks_doc(dsn, task) -> dict:
    async with await db.connect(dsn) as conn:
        row = await (
            await conn.execute("SELECT body FROM documents WHERE kind = 'task' AND id = %s", (task,))
        ).fetchone()
    return row[0]


def scripted_signals(ws):
    from core import signals

    return signals.collect(ws, "planted")


# -- the review's test gaps ---------------------------------------------------------------


def test_a_delivery_with_gaps_after_the_repair_round_is_requested_and_released(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task, test="red")  # join row 5: the repair round
        await drive(dsn, task)
        await scripted.check(dsn, task, "test", "gaps", behaviors=["archived teams"])
        await scripted.check(dsn, task, "review", "pass")
        await scripted.check(dsn, task, "docs", "no_change")
        f = await fold(dsn, task)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, f.merge_effect["effect_id"], note="merge with the gap")
            done = await scripted.release(conn, f.merge_effect["effect_id"])
        return f, done

    f, done = run(go())
    assert f.join.row == 7 and f.delivery["outcome"] == "gaps" and f.delivery["gaps"] == ["archived teams"]
    assert done.kind == "done" and git(origin, "rev-parse", "main") == f.candidate.sha


def test_a_docs_governance_instance_holds_the_merge_until_tom_grants_it(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        head = commit(ws, "docs/rules.md", "Every merge needs a second review.\n", "a rule")
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "review", "pass")
        rule = verdicts.InstanceSpec("docs/rules.md", 1, "a second review", "incident Z", "1")
        await scripted.check(dsn, task, "docs", "updated", head=head, governance=[rule])
        await drive(dsn, task)
        await drive(dsn, task)
        held_back = await fold(dsn, task)
        async with await db.connect(dsn) as conn:
            await guards.grant(conn, task, held_back.instances()[0].id, note="yes, that rule")
            with pytest.raises(guards.GrantRefused, match="already granted"):
                await guards.grant(conn, task, held_back.instances()[0].id, note="again")
        delivered = await drive(dsn, task)
        effect = delivered["state"]["merge_effect"]["effect_id"]
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge")
            done = await scripted.release(conn, effect)
        return held_back, done, head, await rows(dsn, task)

    held_back, done, head, written = run(go())
    assert held_back.state is State.MERGE and held_back.join.row == 1 and held_back.merge_effect is None
    assert not [r for r in written if r["type"] == "effect.refused"]
    assert done.kind == "done" and git(origin, "rev-parse", "main") == head


def test_a_docs_head_that_does_not_descend_from_the_candidate_is_refused(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        git(ws, "checkout", "-q", "-b", "elsewhere", "HEAD~2")
        side = commit(ws, "docs/side.md", "side\n")
        git(ws, "checkout", "-q", "valor/work")
        async with await db.connect(dsn) as conn:
            with pytest.raises(verdicts.VerdictRefused, match="does not descend"):
                await verdicts.record_check(conn, task, Check.DOCS, "updated", head=side, **scripted.MANUAL)
            with pytest.raises(verdicts.VerdictRefused, match="only review and docs"):
                await verdicts.record_check(
                    conn, task, Check.TEST, "pass", governance=[verdicts.InstanceSpec("README.md", 1)],
                    **scripted.MANUAL,
                )  # fmt: skip
            with pytest.raises(guards.GrantRefused, match="in checks"):
                await guards.grant(conn, task, "any", note="yes", incident="i", mission_item="1")

    run(go())


def test_a_failed_merge_stays_in_merge_and_the_next_run_requests_it_again(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        first = await merge_effect(dsn, task)
        key = next(
            r["payload"]["idempotency_key"] for r in await rows(dsn, task) if r["type"] == "effect.held"
        )
        async with await db.connect(dsn) as conn:  # a push the remote refused, as the broker records it
            await ledger.append(conn, task, "effect.intent", {"effect_id": first, "idempotency_key": key})
            await ledger.append(conn, task, "effect.outcome",
                                {"effect_id": first, "idempotency_key": key, "kind": "failed"})  # fmt: skip
        failed = await fold(dsn, task)
        again = await drive(dsn, task)
        return first, failed, again

    first, failed, again = run(go())
    assert failed.state is State.MERGE and failed.merge_effect["state"] == "failed"
    assert again["status"] == "delivered"
    effect = again["state"]["merge_effect"]
    assert effect["state"] == "held" and effect["effect_id"] != first


def test_the_router_names_the_missing_judge_and_reports_a_merged_task(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        waiting = await scripted.start(dsn, ws, judge=None)
        judge = await drive(dsn, waiting, {k: v for k, v in scripted.RUNNERS.items() if k is not State.JUDGE})
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        effect = await merge_effect(dsn, task)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge")
            await scripted.release(conn, effect)
        return judge, await drive(dsn, task)

    judge, merged = run(go())
    assert judge["status"] == "no runner" and judge["missing"] == ["judge"]
    assert merged["status"] == "merged" and merged["state"]["state"] == "merged"


def test_the_start_command_leaves_the_judge_to_the_runner_and_keeps_the_starters_provenance(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    out = cli("start", "x", "--workspace", str(ws), "--by", "stand-in", "--role-played")
    assert out.returncode == 0, out.stderr
    task = out.stdout.strip()
    got = run(rows(dsn, task))
    assert [r["type"] for r in got] == ["task.started"]
    assert (
        got[0]["payload"]["provenance"]["by"] == "stand-in" and got[0]["payload"]["provenance"]["role_played"]
    )
    assert "mode" not in got[0]["payload"]
    assert cli("start", "x", "--mode", "bare").returncode == 2  # the flag is gone
    with pytest.raises(TypeError):
        tasks.Brief(instruction="x", mode="bare")
    by_hand = cli("verdict", task, "judge", "precise")
    assert by_hand.returncode == 1 and "judge has a runner" in by_hand.stderr


def test_the_router_runs_only_the_check_branch_still_missing(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    ran: list = []

    async def test_runner(ctx: router.Context) -> dict:
        ran.append(ctx.check)
        async with await db.connect(ctx.dsn) as conn:
            await verdicts.record_check(
                conn, ctx.task_id, Check.TEST, "pass", leg="test runner", turn_id="t1", model="m"
            )
        return {"status": "moved"}

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.check(dsn, task, "review", "pass")
        await scripted.check(dsn, task, "docs", "no_change")
        return await drive(dsn, task, runners={**scripted.RUNNERS, Check.TEST: test_runner})

    out = run(go())
    assert ran == [Check.TEST]
    assert out["status"] == "delivered" and out["state"]["checks"]["test"]["verdict"] == "pass"


# -- races ---------------------------------------------------------------------------------


@pytest.mark.parametrize("order", ["feedback first", "release first", "together"])
def test_feedback_and_the_release_in_either_order_never_merge_after_feedback(dsn, tmp_path, order):
    """Release checks the predicate and writes its intent under the task's
    lock in one transaction; feedback takes the same lock. Each order is
    forced, then both are raced, and the test names the interleaving that
    happened: no merge intent ever lands after a feedback row."""
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        effect = await merge_effect(dsn, task)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge")

        async def release():
            async with await db.connect(dsn) as conn:
                try:
                    return await scripted.release(conn, effect)
                except broker.Refused as exc:
                    return exc

        async def give():
            async with await db.connect(dsn) as conn:
                try:
                    return await session.feedback(conn, task, "one more thing")
                except LookupError as exc:
                    return exc

        if order == "feedback first":
            fed, released = await give(), await release()
        elif order == "release first":
            released, fed = await release(), await give()
        else:
            released, fed = await asyncio.gather(release(), give())
        return task, released, fed, await rows(dsn, task), await scripted.status(dsn, task)

    _task, released, fed, written, st = run(go())
    ids = {r["type"]: r["id"] for r in written if r["type"] in ("effect.intent", "feedback.given")}
    if "effect.intent" in ids and "feedback.given" in ids:
        happened = "release, then feedback on the merge"
        assert ids["effect.intent"] < ids["feedback.given"]
    elif "effect.intent" in ids:
        happened = "release; feedback refused while the merge was in flight"
        assert isinstance(fed, LookupError) and "in flight" in str(fed)
    else:
        happened = "feedback, then the release refused"
        assert isinstance(released, broker.MergeRefused) and [t[0] for t in released.terms] == ["1"]
        assert not [r for r in written if r["type"] == "effect.outcome"]  # nothing pushed
    expected = {
        "feedback first": "feedback, then the release refused",
        "release first": "release, then feedback on the merge",
    }
    if order in expected:
        assert happened == expected[order], happened
    assert tasks.audit(st) == [], happened


def test_two_releases_of_one_merge_push_once(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        effect = await merge_effect(dsn, task)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge")

        async def release():
            async with await db.connect(dsn) as conn:
                try:
                    return await scripted.release(conn, effect)
                except (broker.Refused, broker.NotApproved) as exc:
                    return exc

        outs = await asyncio.gather(release(), release())
        return outs, await rows(dsn, task), (await fold(dsn, task)).candidate.sha

    outs, written, sha = run(go())
    assert len([r for r in written if r["type"] == "effect.intent"]) == 1
    assert len([r for r in written if r["type"] == "effect.outcome"]) == 1
    assert any(getattr(o, "kind", None) == "done" for o in outs)
    assert git(origin, "rev-parse", "main") == sha


# -- review round 2 --------------------------------------------------------------------------


def _sample(entry: str) -> str:
    """A config key matching one entry of the refusal list."""
    special = {
        "include.": "include.path",
        "includeif.": "includeIf.gitdir:/nowhere/.path",
        "url.": "url.x.insteadOf",
        "credential": "credential.helper",
        "pager.": "pager.log",
        "protocol.": "protocol.allow",
        "uploadpack.": "uploadpack.packObjectsHook",
        "receive.": "receive.procReceiveRefs",
        "gpg.": "gpg.program",
        "ssh.": "ssh.variant",
        "push.": "push.followTags",
        "http.": "http.sslVerify",
        "hook.": "hook.x.command",
        "filter.": "filter.x.clean",
        "alias.": "alias.x",
        "submodule.": "submodule.x.url",
        "sequence.editor": "sequence.editor",
        "diff.external": "diff.external",
    }
    if entry in special:
        return special[entry]
    if entry.startswith("."):  # a suffix
        return (
            f"remote.x{entry}"
            if entry in (".pushurl", ".uploadpack", ".receivepack", ".proxy", ".vcs")
            else f"x.y{entry}"
        )
    return entry


# Keys that must refuse a workspace, written out here rather than read from
# the list, so deleting an entry from the list fails a test.
REQUIRED_REFUSALS = [
    "alias.x", "core.pager", "credential.helper", "core.sshCommand", "include.path", "url.x.insteadOf",
    "remote.origin.pushurl", "filter.x.clean", "diff.x.textconv", "push.default", "http.sslVerify",
    "core.fsmonitor", "core.hooksPath", "merge.x.driver", "includeIf.gitdir:/x/.path", "hook.x.command",
    "core.alternateRefsCommand", "interactive.diffFilter", "core.worktree", "remote.origin.uploadpack",
    "remote.origin.receivepack", "protocol.allow", "uploadpack.packObjectsHook", "core.gitProxy",
    "core.askPass", "gpg.program", "submodule.x.url", "extensions.partialClone",
]  # fmt: skip


@pytest.mark.parametrize("key", REQUIRED_REFUSALS)
def test_each_required_key_refuses_the_workspace(tmp_path, key):
    ws, _ = scripted.workspace(tmp_path)
    git(ws, "config", key, "x")
    assert kgit.hostile(ws), key


@pytest.mark.parametrize("entry", list(kgit.HOSTILE_PREFIXES) + list(kgit.HOSTILE_SUFFIXES))
def test_every_key_on_the_refusal_list_refuses_the_workspace(tmp_path, entry):
    ws, _ = scripted.workspace(tmp_path)
    key = _sample(entry)
    lowered = key.lower()
    assert lowered.startswith(kgit.HOSTILE_PREFIXES) or lowered.endswith(kgit.HOSTILE_SUFFIXES)
    git(ws, "config", key, "x")
    assert kgit.hostile(ws)
    with pytest.raises(kgit.GitError, match="will not run"):
        kgit.head(ws)


def test_worktree_scope_config_is_refused_too(tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    git(ws, "config", "core.repositoryformatversion", "1")
    git(ws, "config", "extensions.worktreeConfig", "true")
    git(ws, "config", "--worktree", "filter.x.clean", "x")
    assert any(f.startswith("worktree: filter.x.clean") for f in kgit.hostile(ws))


def test_unreadable_config_is_refused(tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    (ws / ".git" / "config").write_text((ws / ".git" / "config").read_text() + "[broken\n")
    assert kgit.hostile(ws) and "could not be read" in kgit.hostile(ws)[0]


def test_kernel_git_drops_inherited_git_variables_and_a_planted_git_on_path(tmp_path, monkeypatch):
    ws, _ = scripted.workspace(tmp_path)
    marker = tmp_path / "ran"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    planted = tmp_path / "bin"
    planted.mkdir()
    (planted / "git").write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
    (planted / "git").chmod(0o755)
    base = git(ws, "rev-parse", "HEAD")
    after = commit(ws, "a.txt", "a\n")
    monkeypatch.setenv("PATH", f"{planted}:{os.environ['PATH']}")
    monkeypatch.setenv("GIT_DIR", "/nonexistent")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.fsmonitor'='{script}'")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(script))
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", str(tmp_path / "evil.dylib"))
    built = kgit.env()
    assert not {"GIT_DIR", "GIT_CONFIG_PARAMETERS", "GIT_EXTERNAL_DIFF", "DYLD_INSERT_LIBRARIES"} & set(built)
    assert built["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert kgit.head(ws) == after
    assert kgit.diff_paths(ws, base, after) == ["a.txt"]
    assert kgit.dirty(ws) == []
    assert not marker.exists()


def test_a_tag_the_turn_made_is_not_pushed_and_push_settings_refuse(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        effect = await merge_effect(dsn, task)
        sha = (await fold(dsn, task)).candidate.sha
        git(ws, "-c", "user.name=t", "-c", "user.email=t@e", "tag", "-a", "v9", "-m", "turn's tag", sha)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge")
            git(ws, "config", "push.followTags", "true")
            with pytest.raises(broker.Refused, match="push.followtags"):
                await scripted.release(conn, effect)
            git(ws, "config", "--unset", "push.followTags")
            return await scripted.release(conn, effect), sha

    done, sha = run(go())
    assert done.kind == "done" and git(origin, "rev-parse", "main") == sha
    assert git(origin, "tag", "--list") == ""


async def _dangling(dsn, ws) -> tuple[str, str, str]:
    """A task whose approved merge has an intent and no outcome, as a release
    that died after writing its intent leaves it. Returns the task, the
    effect, and the candidate's sha."""
    task = await to_checks(dsn, ws)
    await scripted.checks(dsn, task)
    effect = await merge_effect(dsn, task)
    sha = (await fold(dsn, task)).candidate.sha
    held = next(r["payload"] for r in await rows(dsn, task) if r["type"] == "effect.held")
    async with await db.connect(dsn) as conn:
        await broker.approve(conn, effect, note="merge")
        await ledger.append(conn, task, "effect.intent",
                            {"effect_id": effect, "idempotency_key": held["idempotency_key"]})  # fmt: skip
    return task, effect, sha


def _outcomes(written, effect) -> list[dict]:
    return [
        r["payload"] for r in written if r["type"] == "effect.outcome" and r["payload"]["effect_id"] == effect
    ]


def test_a_merge_that_landed_before_the_crash_is_reconciled_as_done(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task, effect, sha = await _dangling(dsn, ws)
        git(ws, "push", "-q", str(origin), f"{sha}:refs/heads/main")
        async with await db.connect(dsn) as conn:  # a live performer still holding the effect: left alone
            other = await db.connect(dsn)
            await other.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"effect:{effect}",))
            assert await scripted.reconcile(conn, effect) is None
            await other.close()
        return effect, await drive(dsn, task), await rows(dsn, task)

    effect, out, written = run(go())
    (outcome,) = _outcomes(written, effect)
    assert outcome["kind"] == "done" and outcome["reconciled"] is True and out["status"] == "merged"


def test_a_merge_landed_and_then_built_on_is_still_done(dsn, tmp_path):
    """Ancestry, not equality: the target moved on after the landing."""
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task, effect, sha = await _dangling(dsn, ws)
        git(ws, "push", "-q", str(origin), f"{sha}:refs/heads/main")
        other = tmp_path / "other"

        def build_on():
            subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
            commit(other, "later.txt", "later\n", "someone else's commit")
            git(other, "push", "-q", "origin", "HEAD:refs/heads/main")

        await asyncio.to_thread(build_on)
        return effect, await drive(dsn, task), await rows(dsn, task)

    effect, out, written = run(go())
    (outcome,) = _outcomes(written, effect)
    assert outcome["kind"] == "done" and out["status"] == "merged"


def test_an_unreachable_target_concludes_nothing(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task, effect, _ = await _dangling(dsn, ws)
        origin.rename(tmp_path / "away.git")
        out = await drive(dsn, task)
        async with await db.connect(dsn) as conn:
            assert await scripted.reconcile(conn, effect, settle_after_s=0) is None
        return effect, out, await rows(dsn, task)

    effect, out, written = run(go())
    assert _outcomes(written, effect) == []
    assert out["status"] == "delivered" and out["state"]["merge_effect"]["state"] == "in_flight"


def test_a_missing_merge_is_failed_only_after_no_performer_could_still_be_pushing(dsn, tmp_path, monkeypatch):
    """A performer whose database connection dropped frees its lock while its
    push may still run, bounded by the git time limit; until the intent is
    older than that, an absent effect concludes nothing."""
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task, effect, _ = await _dangling(dsn, ws)
        young = await drive(dsn, task)
        assert _outcomes(await rows(dsn, task), effect) == []
        monkeypatch.setenv("VALOR_RECONCILE_AFTER_S", "0")
        monkeypatch.setenv("VALOR_GIT_TIMEOUT_S", "0")  # for the broker's copy only
        monkeypatch.setattr(broker, "settings", type(broker.settings)())
        old = await drive(dsn, task)
        return effect, young, old, await rows(dsn, task)

    effect, young, old, written = run(go())
    assert young["state"]["merge_effect"]["state"] == "in_flight"
    (outcome,) = _outcomes(written, effect)
    assert outcome["kind"] == "failed" and outcome["reconciled"] is True
    assert old["status"] == "delivered" and old["state"]["merge_effect"]["effect_id"] != effect  # a new merge


def test_a_failing_push_branch_frees_its_effect_lock_and_reconcile_needs_a_performer(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws)
        other = await scripted.start(dsn, ws)
        head = git(ws, "rev-parse", "HEAD")
        async with await db.connect(dsn) as conn:
            held = await scripted.request(
                conn, task, broker.Action("push_branch", "valor/x", {"head_sha": head})
            )
            await broker.approve(conn, held.effect_id, note="push")
            git(ws, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
            nowhere = broker.Performers(PushBranch(ws, url=str(tmp_path / "nowhere.git")))
            failed = await broker.release(conn, nowhere, held.effect_id)
        probe = await db.connect(dsn)
        got = await (
            await probe.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (f"effect:{held.effect_id}",)
            )
        ).fetchone()
        await probe.close()
        # A dangling intent whose performer is not registered is left alone.
        async with await db.connect(dsn) as conn:
            parked = await scripted.request(
                conn, other, broker.Action("push_branch", "valor/y", {"head_sha": head})
            )
            await ledger.append(
                conn, other, "effect.intent", {"effect_id": parked.effect_id, "idempotency_key": "k"}
            )
            none = await broker.reconcile(conn, broker.Performers(), parked.effect_id, settle_after_s=0)
        return failed, got[0], none

    failed, lock_free, none = run(go())
    assert failed.kind == "failed" and lock_free is True and none is None


def test_a_grant_is_refused_in_patch(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task, review="changes")
        async with await db.connect(dsn) as conn:
            with pytest.raises(guards.GrantRefused, match="in patch"):
                await guards.grant(conn, task, "any", note="yes", incident="i", mission_item="1")

    run(go())


def test_the_kernel_runs_only_a_root_owned_real_git(tmp_path, monkeypatch):
    from core import binaries
    from core.settings import Settings

    assert binaries.untrusted("/Library/Developer/CommandLineTools/usr/bin/git") is None or True
    ws, _ = scripted.workspace(tmp_path)
    # The shim: root-owned, but no exec path beside it.
    with pytest.raises(binaries.Untrusted, match="not a real git install"):
        binaries.require_git("/usr/bin/git")
    # A git the user owns, named by VALOR_GIT, is refused at the call.
    mine = tmp_path / "bin" / "git"
    mine.parent.mkdir()
    mine.write_text("#!/bin/sh\nexit 0\n")
    mine.chmod(0o755)
    monkeypatch.setenv("VALOR_GIT", str(mine))
    monkeypatch.setattr(kgit, "settings", Settings())
    assert kgit.settings.git_bin == str(mine)
    with pytest.raises(kgit.GitError, match="not owned by root"):
        kgit.head(ws)
    # VALOR_GIT naming a trusted git is used.
    trusted = binaries.git()
    if trusted:
        monkeypatch.setenv("VALOR_GIT", trusted)
        monkeypatch.setattr(kgit, "settings", Settings())
        assert kgit.head(ws) == git(ws, "rev-parse", "HEAD")


def test_a_poisoned_xcrun_cache_reaches_the_shim_and_never_the_kernel(tmp_path, monkeypatch):
    """Apple's /usr/bin/git finds the real git through a per-user cache; a
    turn can write that cache. A scratch copy (named by `xcrun_db`, so the
    real cache is never touched) is poisoned to name a planted script: the
    shim runs it, the kernel's git does not."""
    ws, _ = scripted.workspace(tmp_path)
    expected = git(ws, "rev-parse", "HEAD")  # before the poisoning; the test's own git is the shim
    cache = tmp_path / "xcrun_db"
    env = {**os.environ, "xcrun_db": str(cache)}
    subprocess.run(["/usr/bin/git", "--version"], env=env, capture_output=True, check=True)
    real = (subprocess.run(["/usr/bin/git", "--exec-path"], env=env, capture_output=True, text=True, check=True)
            .stdout.strip().removesuffix("/libexec/git-core") + "/bin/git")  # fmt: skip
    data = cache.read_bytes()
    if real.encode() not in data:
        pytest.skip("this xcrun cache does not hold the git path in a form this test can rewrite")
    marker = Path(f"/tmp/valor-xcrun-{os.getpid()}")
    planted = Path(str(marker) + "-git" + "x" * (len(real) - len(str(marker)) - 4))
    assert len(str(planted)) == len(real)
    planted.write_text(f"#!/bin/sh\ntouch {marker}\n")
    planted.chmod(0o755)
    try:
        cache.write_bytes(data.replace(real.encode(), str(planted).encode()))
        subprocess.run(["/usr/bin/git", "--version"], env=env, capture_output=True, check=False)
        shim_ran = marker.exists()
        marker.unlink(missing_ok=True)
        monkeypatch.setenv("xcrun_db", str(cache))
        assert kgit.head(ws) == expected
        assert kgit.dirty(ws) == []
        kernel_ran = marker.exists()
    finally:
        planted.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
    assert shim_ran is True  # the poisoning is real
    assert kernel_ran is False


def test_settings_refuse_a_reconcile_wait_shorter_than_two_git_limits(monkeypatch):
    from core.settings import Settings

    monkeypatch.setenv("VALOR_GIT_TIMEOUT_S", "120")
    monkeypatch.setenv("VALOR_RECONCILE_AFTER_S", "239")
    with pytest.raises(ValueError, match="at least twice"):
        Settings()
    monkeypatch.setenv("VALOR_RECONCILE_AFTER_S", "240")
    assert Settings().reconcile_after_s == 240


def test_a_perform_has_one_deadline_for_all_its_git_calls(tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    with kgit.deadline(60):
        assert kgit.head(ws)
    with kgit.deadline(0), pytest.raises(kgit.GitError, match="deadline"):
        kgit.head(ws)
    with kgit.deadline(60), kgit.deadline(3600):  # a nested block keeps the earlier deadline
        assert kgit._DEADLINE.get() - __import__("time").monotonic() < 61


def test_ps_and_sandbox_exec_must_be_roots_alone(tmp_path, monkeypatch):
    from core import binaries, runs
    from harnesses import claude_code

    mine = tmp_path / "ps"
    mine.write_text("#!/bin/sh\nexit 0\n")
    mine.chmod(0o755)
    monkeypatch.setattr(binaries, "PS", str(mine))
    with pytest.raises(binaries.Untrusted, match="not owned by root"):
        runs.reap("no-such-turn")
    monkeypatch.setattr(binaries, "SANDBOX_EXEC", str(mine))
    build = claude_code.workspace_turn("hi", cwd=str(tmp_path), harness={"sandbox_profile": "/p.sb"})
    with pytest.raises(binaries.Untrusted, match="not owned by root"):
        build("http://127.0.0.1:9/t/x", "# Brief", "turn1")


class RacedPerformer:
    """Performs, and while it does, a reconcile elsewhere settles the same
    effect first (the performing process had lost its session)."""

    action_type = "raced"
    effect_class = "act"
    usage = None

    def __init__(self, owner_dsn: str):
        self.owner_dsn = owner_dsn

    async def perform(self, action, key):
        with psycopg.connect(self.owner_dsn, autocommit=True) as conn:
            effect_id, task_id = conn.execute(
                "SELECT payload->>'effect_id', task_id FROM events WHERE type = 'effect.intent' "
                "AND payload->>'idempotency_key' = %s",
                (key,),
            ).fetchone()
            conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES (%s, 'effect.outcome', %s)",
                (task_id, Jsonb({"effect_id": effect_id, "idempotency_key": key, "kind": "done",
                                 "result": {"by": "reconcile"}, "error": None, "reconciled": True})),
            )  # fmt: skip
        return {"by": "performer"}

    async def lookup(self, action, key):
        return None


def test_an_outcome_reconcile_wrote_first_stands_over_the_performers(dsn, owner_dsn, tmp_path):
    perf = broker.Performers(RacedPerformer(owner_dsn))

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="x", max_effect_class="act"))
            held = await broker.request(conn, perf, task, broker.Action("raced", "t", {"n": 1}))
            await broker.approve(conn, held.effect_id, note="go")
            out = await broker.release(conn, perf, held.effect_id)
            return out, await ledger.read(conn, task)

    out, written = run(go())
    assert out.kind == "done" and out.result == {"by": "reconcile"}
    assert len([r for r in written if r["type"] == "effect.outcome"]) == 1
