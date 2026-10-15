"""The kernel rolling itself forward to its own merges (`core/rollout.py`,
`Kernel.roll`), on real Postgres and temporary checkouts cloned from a
local bare remote: a merge of the kernel's own branch fast-forwards the
checkout and restarts it on the new commit, `recover` ends the rollout
after the restart, a dependency or schema change rolls with its sync or
backup, and `python -m core serve` exits for the restart.

The backup, the sync, the migrate, and the restart are stand-ins; nothing
touches the build's own checkout, the machine's kernel, or the real
ledger. No model call.
"""

import asyncio
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest

from core import db, git, ledger, rollout, serve
from tests import scripted
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


def kernel(
    dsn, repo: Repo, task: str, *, started: str | None = None, migrate=None, sync=None, backup=None
) -> serve.Kernel:
    """A kernel on the test database rolling `repo.co`, the stand-in
    backup, sync, and migrate recording each call, in one list `k.ran` of
    `(step, the checkout's head then)`. `ready` records each task `schedule`
    considers, so an empty list after a wake means no job could have
    started."""
    migrated: list[str] = []
    ran: list[tuple[str, str]] = []

    def stand_in(database: str) -> None:
        migrated.append(database)
        ran.append(("migrate", repo.head()))
        if migrate is not None:
            migrate(database)

    def synced() -> None:
        ran.append(("sync", repo.head()))
        if sync is not None:
            sync()

    def backed_up(database: str) -> None:
        ran.append(("backup", repo.head()))
        if backup is not None:
            backup(database)

    k = serve.Kernel(
        None, {}, None, dsn, checkout=repo.co, started=started or repo.base, migrate=stand_in,
        sync=synced, backup=backed_up,
    )  # fmt: skip
    k.migrated, k.ran = migrated, ran
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


def dbname(dsn: str) -> str:
    return psycopg.conninfo.conninfo_to_dict(dsn)["dbname"]


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


# -- dependencies and schema -------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "ran"),
    [
        ("uv.lock", ["sync", "migrate"]),
        ("pyproject.toml", ["sync", "migrate"]),
        ("core/schema.sql", ["backup", "migrate"]),
    ],
)
def test_a_dependency_or_schema_change_rolls_with_its_sync_or_backup(fresh, tmp_path, path, ran):
    """The backup runs before the checkout moves; the sync after the
    fast-forward, against the merged lock, before the migrate."""
    repo = Repo(tmp_path)
    sha = repo.land(path)

    async def go():
        task = await new_task(fresh)
        effect = await landed(fresh, task, repo.url, sha)
        k = kernel(fresh, repo, task)
        return task, effect, k, await tick(k, fresh)

    task, effect, k, restarted = run(go())
    assert restarted == sha and repo.head() == sha
    heads = {"backup": repo.base, "sync": sha, "migrate": sha}
    assert k.ran == [(step, heads[step]) for step in ran]
    written = run(rows(fresh, task))
    (restarting,) = typed(written, "rollout.restarting")
    named = {"sync": "dependencies", "backup": "backup", "migrate": "migrate"}
    order = ["fetch", "restart class", *(["backup"] if "backup" in ran else []), "fast-forward"]
    order += [named[s] for s in ran if s != "backup"]
    assert restarting["effect_id"] == effect and restarting["steps"] == order
    assert typed(written, "rollout.failed") == [] and typed(written, "notice.requested") == []


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
