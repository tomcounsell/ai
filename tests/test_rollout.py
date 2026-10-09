"""The kernel rolling itself forward to its own merges (`core/rollout.py`,
`Kernel.roll`), on real Postgres and temporary checkouts cloned from a
local bare remote: which merges are the kernel's, the restart class, the
hold and the background turn's cancel, the dependency and schema stops,
the fast-forward, the migrate and stepping back, the retries, the rows
`recover` ends after a restart, and the exit through `serve` and `python
-m core serve`.

The migrate and the restart are stand-ins; nothing touches the build's own
checkout, the machine's kernel, or the real ledger. No model call.
"""

import asyncio
import contextlib
import subprocess
import threading
import uuid
from pathlib import Path

import psycopg
import pytest

from core import db, git, ledger, rollout, serve
from tests import bridges, scripted
from tests.bridges import new_task, rows
from tests.conftest import TEST_DB
from tests.scripted import commit

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fresh() -> str:
    """A database of this test's own."""
    return db.migrate(f"{TEST_DB}_rollout", fresh=True)


@pytest.fixture
def op(tmp_path):
    with bridges.operator(tmp_path) as s:
        yield s


class Repo:
    """A bare `origin` with `main`, a `dev` clone that lands merges on it,
    and the kernel's checkout `co`, cloned at the base commit."""

    def __init__(self, tmp_path: Path):
        tmp_path = tmp_path.resolve()
        self.origin, self.dev, self.co = tmp_path / "origin.git", tmp_path / "dev", tmp_path / "co"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        scripted.git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.dev)], check=True)
        commit(self.dev, "README.md", "the kernel\n", "base")
        self.base = commit(self.dev, "core/a.py", "A = 1\n", "core")
        scripted.git(self.dev, "remote", "add", "origin", str(self.origin))
        scripted.git(self.dev, "push", "-q", "origin", "main")
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.co)], check=True)
        self.url = git.push_url(self.co)

    def land(self, path: str, text: str = "x\n", branch: str = "main") -> str:
        sha = commit(self.dev, path, text, f"change {path}")
        scripted.git(self.dev, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")
        return sha

    def head(self) -> str:
        return scripted.git(self.co, "rev-parse", "HEAD")


async def landed(dsn, task, remote, sha, branch="main", kind="done", **extra) -> str:
    """A merge effect's rows as the broker writes them: held, intent, outcome."""
    effect = uuid.uuid4().hex
    described = {
        "action_type": "merge",
        "target": branch,
        "payload": {"head_sha": sha},
        "idempotency_key": effect,
    }
    async with await db.connect(dsn) as conn, conn.transaction():
        await ledger.append(
            conn, task, "effect.held", {"effect_id": effect, **described, "effect_class": "act"}
        )
        await ledger.append(
            conn, task, "effect.intent", {"effect_id": effect, **described, "approval_id": None}
        )
        await ledger.append(
            conn,
            task,
            "effect.outcome",
            {
                "effect_id": effect,
                "kind": kind,
                "result": {"remote": remote, "branch": branch, "sha": sha},
                **extra,
            },
        )
    return effect


def kernel(dsn, repo: Repo, task: str, *, started: str | None = None, migrate=None) -> serve.Kernel:
    """A kernel on the test database rolling `repo.co`, the stand-in migrate
    recording each call. `ready` records each task `schedule` considers, so
    an empty list after a wake means no job could have started."""
    migrated: list[str] = []

    def stand_in(database: str) -> None:
        migrated.append(database)
        if migrate is not None:
            migrate(database)

    k = serve.Kernel(None, {}, None, dsn, checkout=repo.co, started=started or repo.base, migrate=stand_in)
    k.migrated = migrated
    k.ready = []

    async def active(conn):
        return [task]

    async def ready(conn, task_id):
        k.ready.append(task_id)

    k.active, k._ready = active, ready
    return k


async def tick(k: serve.Kernel, dsn: str, *, timer: bool = False) -> str | None:
    """One wake; with `timer`, the `serve_tick_s` wake. The sha of a
    `Restart`, or None."""
    if timer:
        k.parked_at = float("-inf")
    k.ready.clear()
    async with await db.connect(dsn) as conn:
        try:
            await k.tick(conn)
        except rollout.Restart as exc:
            return exc.sha
    return None


def typed(written, type_, **match) -> list[dict]:
    return [
        r["payload"]
        for r in written
        if r["type"] == type_ and all(r["payload"].get(k) == v for k, v in match.items())
    ]


def rolls(written) -> list[dict]:
    return [r for r in written if r["type"].startswith("rollout.")]


def dbname(dsn: str) -> str:
    return psycopg.conninfo.conninfo_to_dict(dsn)["dbname"]


@contextlib.contextmanager
def counting_git(monkeypatch):
    """Every git process run while held."""
    calls: list[tuple] = []
    real = git.run

    def counted(workspace, *args, **kw):
        calls.append(args)
        return real(workspace, *args, **kw)

    monkeypatch.setattr(git, "run", counted)
    yield calls
    monkeypatch.setattr(git, "run", real)


async def recovered(dsn, repo: Repo, running: str) -> list[str]:
    async with await db.connect(dsn) as conn:
        return (await serve.recover(conn, checkout=repo.co, running=running))["rolled"]


# -- done, as evidence -------------------------------------------------------------------


def test_a_core_merge_restarts_and_recover_ends_it(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        restarted = await tick(k, fresh)
        return task, effect, k, restarted

    task, effect, k, restarted = run(go())
    assert restarted == sha
    (row,) = typed(run(rows(fresh, task)), "rollout.restarting")
    assert row == {
        "effect_id": effect,
        "sha": sha,
        "from": repo.base,
        "steps": ["fetch", "restart class", "fast-forward", "migrate"],
        "covers": [],
    }
    assert repo.head() == sha
    assert k.migrated == [dbname(fresh)]
    assert k.ready == []  # no job started on that wake

    assert run(recovered(fresh, repo, sha)) == [effect]
    (ended,) = typed(run(rows(fresh, task)), "rollout.ended")
    assert ended == {"effect_id": effect, "outcome": "done", "head": sha}
    assert run(recovered(fresh, repo, sha)) == []


def test_a_skills_and_docs_merge_rolls_with_no_restart(fresh, tmp_path):
    repo = Repo(tmp_path)
    repo.land("skills/build/SKILL.md")
    sha = repo.land("docs/x.md")

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        return task, effect, k, await tick(k, fresh)

    task, effect, k, restarted = run(go())
    assert restarted is None
    assert rolls(run(rows(fresh, task)))[0]["payload"] == {
        "effect_id": effect,
        "outcome": "done",
        "head": sha,
        "steps": ["fetch", "restart class", "fast-forward"],
    }
    assert repo.head() == sha and k.migrated == [] and k.restart_due is None
    assert k.ready == [task]  # scheduling never held


# -- which merges ------------------------------------------------------------------------


def test_a_merge_to_another_remote_or_branch_is_not_the_kernels(fresh, tmp_path, monkeypatch):
    repo = Repo(tmp_path)
    other = repo.land("core/b.py", branch="dev")
    scripted.git(repo.dev, "reset", "-q", "--hard", repo.base)
    elsewhere = str(tmp_path / "elsewhere.git")

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, other, branch="dev")
        await landed(fresh, task, elsewhere, other)
        k = kernel(fresh, repo, task)
        first = await tick(k, fresh)
        with counting_git(monkeypatch) as calls:
            await tick(k, fresh, timer=True)
        return task, first, calls

    task, restarted, calls = run(go())
    assert restarted is None and calls == []
    assert rolls(run(rows(fresh, task))) == []
    assert repo.head() == repo.base


def test_a_merge_contained_in_started_writes_nothing_and_is_judged_once(fresh, tmp_path, monkeypatch):
    repo = Repo(tmp_path)

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, repo.base)
        k = kernel(fresh, repo, task)
        await tick(k, fresh)
        with counting_git(monkeypatch) as calls:
            await tick(k, fresh, timer=True)
        return task, calls

    task, calls = run(go())
    assert calls == []
    assert rolls(run(rows(fresh, task))) == []


def test_a_checkout_moved_past_started_still_restarts(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    scripted.git(repo.co, "pull", "-q", "--ff-only")  # the lead rolled it by hand

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        return task, await tick(k, fresh)

    task, restarted = run(go())
    assert restarted == sha and repo.head() == sha
    (row,) = typed(run(rows(fresh, task)), "rollout.restarting")
    assert row["steps"] == ["fetch", "restart class", "fast-forward", "migrate"]


def test_two_merges_roll_once_and_recover_ends_both(fresh, tmp_path):
    repo = Repo(tmp_path)
    older = repo.land("core/b.py")
    newer = repo.land("core/c.py")

    async def go():
        task = await new_task(fresh)
        first = await landed(fresh, task, repo.url, older)
        second = await landed(fresh, task, repo.url, newer)
        k = kernel(fresh, repo, task)
        return task, first, second, await tick(k, fresh)

    task, first, second, restarted = run(go())
    assert restarted == newer
    (row,) = typed(run(rows(fresh, task)), "rollout.restarting")
    assert row["effect_id"] == second
    assert row["covers"] == [{"effect_id": first, "task_id": task, "sha": older}]

    assert sorted(run(recovered(fresh, repo, newer))) == sorted([first, second])
    written = run(rows(fresh, task))
    assert typed(written, "rollout.ended", effect_id=first) == [
        {"effect_id": first, "outcome": "done", "head": newer, "rolled_by": second}
    ]
    assert typed(written, "rollout.ended", effect_id=second) == [
        {"effect_id": second, "outcome": "done", "head": newer}
    ]


def test_a_merge_not_on_the_branch_is_superseded(fresh, tmp_path):
    repo = Repo(tmp_path)
    gone = repo.land("core/b.py")
    scripted.git(repo.dev, "reset", "-q", "--hard", repo.base)
    repo.land("docs/y.md")  # the branch was rewritten past it

    async def go():
        task = await new_task(fresh)
        first = await landed(fresh, task, repo.url, gone)
        k = kernel(fresh, repo, task)
        restarted = await tick(k, fresh)  # the newest due merge is the one superseded
        newer = repo.land("core/c.py")
        second = await landed(fresh, task, repo.url, newer)
        return task, first, second, newer, restarted, await tick(k, fresh)

    task, first, second, newer, before, restarted = run(go())
    assert before is None
    written = run(rows(fresh, task))
    (ended,) = typed(written, "rollout.ended", effect_id=first)
    assert ended["outcome"] == "superseded" and ended["sha"] == gone
    assert restarted == newer
    (row,) = typed(written, "rollout.restarting")
    assert row["effect_id"] == second and row["covers"] == []


def test_of_two_merges_recorded_newer_first_the_descendant_rolls(fresh, tmp_path):
    repo = Repo(tmp_path)
    older = repo.land("core/b.py")
    newer = repo.land("core/c.py")

    async def go():
        task = await new_task(fresh)
        second = await landed(fresh, task, repo.url, newer)
        first = await landed(fresh, task, repo.url, older)
        k = kernel(fresh, repo, task)
        return task, first, second, await tick(k, fresh)

    task, first, second, restarted = run(go())
    assert restarted == newer
    (row,) = typed(run(rows(fresh, task)), "rollout.restarting")
    assert row["effect_id"] == second and [c["effect_id"] for c in row["covers"]] == [first]


# -- after the restart -------------------------------------------------------------------


def test_recover_ends_a_failed_merge_the_lead_rolled_by_hand(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("uv.lock")

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        await tick(kernel(fresh, repo, task), fresh)
        return task, effect

    task, effect = run(go())
    assert typed(run(rows(fresh, task)), "rollout.failed")[0]["step"] == "dependencies"
    scripted.git(repo.co, "pull", "-q", "--ff-only")
    assert run(recovered(fresh, repo, sha)) == [effect]
    assert typed(run(rows(fresh, task)), "rollout.ended") == [
        {"effect_id": effect, "outcome": "done", "head": sha, "by": "started"}
    ]


def test_recover_leaves_a_restart_the_running_commit_lacks(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        assert await tick(kernel(fresh, repo, task), fresh) == sha
        scripted.git(repo.co, "reset", "-q", "--hard", repo.base)  # the restart ran the old commit
        rolled = await recovered(fresh, repo, repo.base)
        return task, rolled, await tick(kernel(fresh, repo, task), fresh)

    task, rolled, again = run(go())
    assert rolled == [] and typed(run(rows(fresh, task)), "rollout.ended") == []
    assert again == sha  # due again


# -- the hold ----------------------------------------------------------------------------


def _job(k: serve.Kernel, task_id: str, *, background: bool = False) -> asyncio.Event:
    """A stand-in job for `task_id` that runs until the event is set."""
    done = asyncio.Event()
    if background:
        k.background = task_id
    else:
        k.harness = task_id
    k._start(task_id, done.wait(), 0)
    return done


def test_a_restart_waits_for_a_running_job(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        ended = _job(k, "running")
        held = [await tick(k, fresh), repo.head(), list(k.ready), k.migrated[:], k.restart_due.sha]
        held.append(await tick(k, fresh))  # a wake while the job runs moves nothing
        ended.set()
        while k.jobs:
            await asyncio.sleep(0)
        return task, held, await tick(k, fresh), k

    task, held, restarted, k = run(go())
    assert held == [None, repo.base, [], [], sha, None]
    assert rolls(run(rows(fresh, task)))[0]["type"] == "rollout.restarting"
    assert restarted == sha and repo.head() == sha and k.migrated == [dbname(fresh)]


def test_apply_cancels_the_background_turn_before_the_fast_forward(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    seen = []

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)

        async def turn():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                seen.append(repo.head())
                raise

        k.background = "turn"
        k._start("turn", turn(), 0)
        await asyncio.sleep(0)
        return await tick(k, fresh)

    assert run(go()) == sha
    assert seen == [repo.base]


def test_a_merge_landed_during_the_wait_is_the_one_applied(fresh, tmp_path):
    repo = Repo(tmp_path)
    older = repo.land("core/b.py")

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, older)
        k = kernel(fresh, repo, task)
        ended = _job(k, "running")
        assert await tick(k, fresh) is None
        newer = repo.land("core/c.py")
        await landed(fresh, task, repo.url, newer)
        ended.set()
        while k.jobs:
            await asyncio.sleep(0)
        return newer, await tick(k, fresh)

    newer, restarted = run(go())
    assert restarted == newer and repo.head() == newer


@pytest.mark.parametrize("then", ["superseded", "reverted"])
def test_a_re_prepare_with_no_restart_due_lifts_the_hold(fresh, tmp_path, then):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        ended = _job(k, "running")
        assert await tick(k, fresh) is None and k.restart_due is not None
        if then == "superseded":
            scripted.git(repo.dev, "reset", "-q", "--hard", repo.base)
            repo.land("docs/z.md")
        else:
            scripted.git(repo.dev, "rm", "-q", "core/b.py")
            newer = commit(repo.dev, "docs/z.md", "z\n", "revert core/b.py, add docs")
            scripted.git(repo.dev, "push", "-q", "origin", "main")
            await landed(fresh, task, repo.url, newer)
        ended.set()
        while k.jobs:
            await asyncio.sleep(0)
        return task, k, await tick(k, fresh)

    task, k, restarted = run(go())
    assert restarted is None and k.restart_due is None and k.ready == [task]
    outcomes = sorted(p["outcome"] for p in typed(run(rows(fresh, task)), "rollout.ended"))
    assert outcomes == (["superseded"] if then == "superseded" else ["done", "done"])
    assert k.migrated == []


# -- the stops and the retries -----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "step"),
    [("uv.lock", "dependencies"), ("pyproject.toml", "dependencies"), ("core/schema.sql", "schema")],
)
def test_a_dependency_or_schema_change_stops_and_is_not_retried(fresh, tmp_path, monkeypatch, path, step):
    repo = Repo(tmp_path)
    sha = repo.land(path)

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        first = [await tick(k, fresh), list(k.ready), k.restart_due]
        with counting_git(monkeypatch) as calls:
            again = await tick(k, fresh, timer=True)
        return task, effect, first, again, calls, k

    task, effect, first, again, calls, k = run(go())
    assert first == [None, [task], None]
    assert again is None and calls == []
    written = run(rows(fresh, task))
    (failed,) = typed(written, "rollout.failed")
    assert failed["step"] == step and failed["effect_id"] == effect
    assert failed["steps"] == ["fetch", "restart class"]
    (notice,) = typed(written, "notice.requested", about_key=f"rollout:{effect}")
    assert notice["kind"] == "rollout"
    assert repo.head() == repo.base and k.migrated == []


@pytest.mark.parametrize(("path", "step"), [("core/schema.sql", "schema"), ("uv.lock", "dependencies")])
def test_a_change_beyond_the_sha_in_the_checkout_stops_the_restart(fresh, tmp_path, path, step):
    """The checkout already holds a commit past the merged sha: the restart
    would run that commit, so its diff from `started` is the one classified."""
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    beyond = repo.land(path)
    scripted.git(repo.co, "pull", "-q", "--ff-only")

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        return task, effect, k, await tick(k, fresh)

    task, effect, k, restarted = run(go())
    assert restarted is None and k.migrated == [] and repo.head() == beyond
    (failed,) = typed(run(rows(fresh, task)), "rollout.failed")
    assert failed["step"] == step and failed["effect_id"] == effect
    assert beyond in failed["reason"] and path in failed["reason"]


@pytest.mark.parametrize(
    ("path", "step"), [("core/schema.sql", "schema"), ("pyproject.toml", "dependencies")]
)
def test_an_uncommitted_schema_or_dependency_file_stops_the_restart(fresh, tmp_path, path, step):
    """The restart imports, and migrate reads, the working tree."""
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    (repo.co / path).write_text("the lead's edit\n")

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        return task, effect, k, await tick(k, fresh)

    task, effect, k, restarted = run(go())
    assert restarted is None and k.migrated == [] and repo.head() == repo.base
    (failed,) = typed(run(rows(fresh, task)), "rollout.failed")
    assert (
        failed["effect_id"] == effect and failed["step"] == step and f"uncommitted {path}" in failed["reason"]
    )
    assert (repo.co / path).read_text() == "the lead's edit\n"


def test_a_dirty_schema_file_does_not_stop_a_rollout_with_no_restart(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("docs/z.md")
    (repo.co / "core" / "schema.sql").write_text("the lead's edit\n")

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        return task, await tick(k, fresh)

    task, restarted = run(go())
    assert restarted is None and repo.head() == sha
    assert [p["outcome"] for p in typed(run(rows(fresh, task)), "rollout.ended")] == ["done"]


def test_a_diverged_checkout_is_retried_on_the_timer_and_rolls_once_rebased(fresh, tmp_path, monkeypatch):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    commit(repo.co, "docs/plan.md", "the lead's plan\n", "plan")

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        seen = []
        for _ in range(3):
            seen.append((await tick(k, fresh, timer=True), list(k.ready), k.restart_due))
            with counting_git(monkeypatch) as calls:
                await tick(k, fresh)  # a wake from a ledger row
            seen.append(calls)
        scripted.git(repo.co, "-c", "user.name=t", "-c", "user.email=t@example.com", "pull", "-q", "--rebase")
        return task, effect, seen, k, await tick(k, fresh, timer=True)

    task, effect, seen, k, restarted = run(go())
    assert seen == [(None, [task], None), []] * 3
    written = run(rows(fresh, task))
    (failed,) = typed(written, "rollout.failed")
    assert failed["step"] == "fast-forward" and "diverged" in failed["reason"]
    assert len(typed(written, "notice.requested", about_key=f"rollout:{effect}")) == 1
    assert k.migrated == [dbname(fresh)]  # only once rebased
    assert restarted == sha
    assert scripted.git(repo.co, "merge-base", "--is-ancestor", sha, "HEAD") == ""


def test_a_dirty_file_the_merge_changes_refuses_and_one_it_does_not_is_kept(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py", "B = 2\n")
    (repo.co / "core" / "b.py").write_text("mine\n")
    (repo.co / "README.md").write_text("the lead's edit\n")

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        refused = await tick(k, fresh)
        (repo.co / "core" / "b.py").unlink()
        return task, refused, await tick(k, fresh, timer=True)

    task, refused, restarted = run(go())
    assert refused is None
    (failed,) = typed(run(rows(fresh, task)), "rollout.failed")
    assert failed["step"] == "fast-forward" and "core/b.py" in failed["reason"]
    assert restarted == sha and repo.head() == sha
    assert (repo.co / "README.md").read_text() == "the lead's edit\n"


# -- the migrate -------------------------------------------------------------------------


def test_a_failed_migrate_steps_back_and_is_not_retried(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")

    def broken(database):
        raise RuntimeError("the merged migrate broke")

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task, migrate=broken)
        return task, effect, k, [await tick(k, fresh), await tick(k, fresh, timer=True)]

    task, effect, k, restarted = run(go())
    assert restarted == [None, None]
    assert repo.head() == repo.base and k.migrated == [dbname(fresh)]
    written = run(rows(fresh, task))
    (failed,) = typed(written, "rollout.failed")
    assert failed["step"] == "migrate" and "broke" in failed["reason"] and "mixed" not in failed
    assert failed["steps"] == ["fetch", "restart class", "fast-forward", "migrate"]
    assert typed(written, "notice.requested", about_key=f"rollout:{effect}:mixed") == []


def test_a_migrate_failure_that_cannot_step_back_says_mixed(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py", "B = 2\n")
    (repo.co / "core").mkdir(exist_ok=True)
    (repo.co / "core" / "b.py").write_text("mine\n")  # an earlier failure first

    def edits_then_breaks(database):
        (repo.co / "core" / "b.py").write_text("the lead's edit\n")
        raise RuntimeError("the merged migrate broke")

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task, migrate=edits_then_breaks)
        await tick(k, fresh)
        (repo.co / "core" / "b.py").unlink()
        return task, effect, await tick(k, fresh, timer=True)

    task, effect, restarted = run(go())
    assert restarted is None and repo.head() == sha
    written = run(rows(fresh, task))
    assert [f["step"] for f in typed(written, "rollout.failed")] == ["fast-forward", "migrate"]
    assert typed(written, "rollout.failed")[-1]["mixed"] is True
    assert len(typed(written, "notice.requested", about_key=f"rollout:{effect}")) == 1
    assert len(typed(written, "notice.requested", about_key=f"rollout:{effect}:mixed")) == 1


def test_a_commit_after_the_fast_forward_is_not_reset(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")

    def commits_then_breaks(database):
        commit(repo.co, "docs/plan.md", "the lead's plan\n", "plan")
        raise RuntimeError("the merged migrate broke")

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        return task, await tick(kernel(fresh, repo, task, migrate=commits_then_breaks), fresh)

    task, restarted = run(go())
    assert restarted is None
    assert scripted.git(repo.co, "rev-parse", "HEAD~1") == sha
    (failed,) = typed(run(rows(fresh, task)), "rollout.failed")
    assert failed["mixed"] is True


def test_the_default_migrate_runs_db_migrate_in_the_checkout(tmp_path, monkeypatch):
    started = []

    def stand_in(argv, **kw):
        started.append((argv, kw["cwd"]))
        return subprocess.Popen(["/usr/bin/true"], stdout=kw["stdout"], stderr=kw["stderr"])

    monkeypatch.setattr(git, "start", stand_in)
    run(git.threaded(rollout.migrate, tmp_path, "valor_somewhere"))
    assert started == [
        (
            [
                str(tmp_path / ".venv" / "bin" / "python"),
                "-c",
                "import sys; from core import db; db.migrate(sys.argv[1])",
                "valor_somewhere",
            ],
            tmp_path,
        )
    ]


# -- off the loop, the sources of a merge, the checkout's config, the credential ---------


def test_every_step_runs_off_the_loop(fresh, tmp_path, monkeypatch):
    repo = Repo(tmp_path)
    sha = repo.land("docs/x.md")
    entered, release = threading.Event(), threading.Event()
    real = rollout.fetch

    def blocking(*args):
        entered.set()
        release.wait(30)
        real(*args)

    monkeypatch.setattr(rollout, "fetch", blocking)

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        woke = asyncio.create_task(tick(k, fresh))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        beats = 0
        for _ in range(5):
            await asyncio.sleep(0.01)
            beats += 1
        release.set()
        await woke
        return beats

    assert run(go()) == 5
    assert repo.head() == sha


@pytest.mark.macos
def test_a_reconciled_merge_rolls_out_and_a_failed_one_does_not(fresh, op, tmp_path):
    from core.__main__ import _performers
    from tests.test_pipeline import _dangling

    ws, origin = scripted.workspace(tmp_path)
    co = tmp_path / "co"
    subprocess.run(["git", "clone", "-q", str(origin), str(co)], check=True)

    async def go():
        task, effect, sha = await _dangling(fresh, ws)
        scripted.git(ws, "push", "-q", str(origin), f"{sha}:refs/heads/main")
        async with await db.connect(fresh) as conn:
            assert (await serve.recover(conn, _performers))["reconciled"] == [effect]
        failed = await landed(fresh, task, git.push_url(co), sha, kind="failed")
        repo = Repo.__new__(Repo)
        repo.co, repo.base = co, scripted.git(co, "rev-parse", "HEAD")
        k = kernel(fresh, repo, task)
        restarted = await tick(k, fresh)
        return task, effect, failed, sha, restarted

    task, effect, failed, sha, restarted = run(go())
    assert scripted.git(co, "rev-parse", "HEAD") == sha
    written = run(rows(fresh, task))
    named = {r["payload"]["effect_id"] for r in rolls(written)}
    assert named == {effect} and failed not in named
    assert restarted == sha or typed(written, "rollout.ended", effect_id=effect)


def test_a_hostile_key_in_the_checkouts_config_fails_at_fetch(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    scripted.git(repo.co, "config", "core.hooksPath", str(tmp_path / "hooks"))

    async def go():
        task = await new_task(fresh)
        await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        return task, k, await tick(k, fresh)

    task, k, restarted = run(go())
    assert restarted is None and k.migrated == [] and repo.head() == repo.base
    (failed,) = typed(run(rows(fresh, task)), "rollout.failed")
    assert failed["step"] == "fetch" and "core.hookspath" in failed["reason"].lower()


def test_a_refused_config_fails_on_the_kernels_merge_not_the_newest_of_any_project(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    scripted.git(repo.co, "config", "core.hooksPath", str(tmp_path / "hooks"))

    async def go():
        ours, theirs = await new_task(fresh), await new_task(fresh)
        effect = await landed(fresh, ours, repo.url, sha)
        await landed(fresh, theirs, "/somewhere/else.git", sha, branch="feature")
        k = kernel(fresh, repo, ours)
        await tick(k, fresh)
        return ours, theirs, effect

    ours, theirs, effect = run(go())
    (failed,) = typed(run(rows(fresh, ours)), "rollout.failed")
    assert failed["effect_id"] == effect and failed["step"] == "fetch"
    assert len(typed(run(rows(fresh, ours)), "notice.requested", about_key=f"rollout:{effect}")) == 1
    other = run(rows(fresh, theirs))
    assert rolls(other) == [] and typed(other, "notice.requested", kind="rollout") == []


def test_a_refused_config_with_no_merge_of_its_own_writes_no_row(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    scripted.git(repo.co, "config", "core.hooksPath", str(tmp_path / "hooks"))

    async def go():
        theirs = await new_task(fresh)
        await landed(fresh, theirs, "/somewhere/else.git", sha, branch="feature")
        k = kernel(fresh, repo, theirs)
        await tick(k, fresh)
        return theirs

    other = run(rows(fresh, run(go())))
    assert rolls(other) == [] and typed(other, "notice.requested", kind="rollout") == []


def test_the_fetch_carries_the_credential_only_to_a_remote_off_the_machine(tmp_path, monkeypatch):
    used, ran = [], []

    @contextlib.contextmanager
    def header_file(keyfile, url):
        used.append((keyfile, url))
        yield tmp_path / "header"

    def out(workspace, *args, credential=None, url=None):
        ran.append((args[-2], credential, url))
        return ""

    monkeypatch.setattr(rollout.credentials, "header_file", header_file)
    monkeypatch.setattr(git, "out", out)
    remote = "https://github.com/example/kernel.git"
    rollout.fetch(tmp_path, remote, "main", "keyfile")
    rollout.fetch(tmp_path, str(tmp_path / "origin.git"), "main", "keyfile")
    rollout.fetch(tmp_path, remote, "main", None)
    assert used == [("keyfile", remote)]
    assert ran == [
        (remote, tmp_path / "header", remote),
        (str(tmp_path / "origin.git"), None, None),
        (remote, None, None),
    ]


# -- the exit ----------------------------------------------------------------------------


def test_python_m_core_serve_exits_with_status_1_on_a_restart(monkeypatch):
    import core.__main__ as main

    async def restarting(*args, **kw):
        raise rollout.Restart("a" * 40)

    monkeypatch.setattr(main, "port", lambda: None)
    monkeypatch.setattr(main, "runners", lambda port: {})
    monkeypatch.setattr(serve, "serve", restarting)
    with pytest.raises(SystemExit) as exc:
        run(main._serve())
    assert exc.value.code == f"kernel restarting for {'a' * 40}"  # a message: status 1
