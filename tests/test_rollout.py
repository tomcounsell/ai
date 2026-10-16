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
import os
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest

from core import db, git, ledger, rollout, serve
from core.settings import settings
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


# -- the bridges -------------------------------------------------------------------------

# A scratch layout like this repository's: telegram imports core.intake
# inside a function and its own wire relatively; core.intake imports
# core.channels; nothing imports core/serve.py.
LAYOUT = {
    "bridges/__init__.py": "",
    "bridges/telegram/__init__.py": "",
    "bridges/telegram/bridge.py": "from . import wire\n\ndef go():\n    from core import intake\n",
    "bridges/telegram/wire.py": "import json\n",
    "bridges/slack/__init__.py": "from bridges.slack.sub import x\n",
    "bridges/slack/sub/__init__.py": "from core.bridge import Outbox\n",
    "bridges/slack/sub/x.py": "",
    "bridges/local/chat.js": "",
    "bridges/local/__init__.py": "import core.bridge\n",
    "core/__init__.py": "",
    "core/intake.py": "from core.channels import C\n",
    "core/channels.py": "C = 1\n",
    "core/bridge.py": "",
    "core/serve.py": "from core import intake\n",
}


def layout(tmp_path: Path) -> tuple[Path, str]:
    co = tmp_path / "co"
    subprocess.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    for path, text in LAYOUT.items():
        sha = commit(co, path, text, path)
    return co, sha


def test_a_bridges_closure_follows_every_import_it_can_run(tmp_path):
    co, _ = layout(tmp_path)
    assert rollout.closure(co, "telegram") == {
        "bridges/__init__.py", "bridges/telegram/__init__.py", "bridges/telegram/bridge.py",
        "bridges/telegram/wire.py", "core/__init__.py", "core/intake.py", "core/channels.py",
    }  # fmt: skip
    slack = rollout.closure(co, "slack")
    assert {"bridges/slack/sub/__init__.py", "core/bridge.py", "core/__init__.py"} <= slack
    assert "core/intake.py" not in slack and "core/serve.py" not in slack
    # This checkout: the shared code is in every bridge's, the kernel's own in none.
    root = Path(__file__).resolve().parent.parent
    for name in rollout.BRIDGES:
        files = rollout.closure(root, name)
        assert {"core/bridge.py", "core/intake.py", "core/channels.py"} <= files, name
        assert not {"core/serve.py", "core/rollout.py", "core/router.py"} & files, name


def test_what_a_diff_reaches(tmp_path):
    co, _ = layout(tmp_path)
    files = {n: rollout.closure(co, n) for n in ("telegram", "slack", "local")}
    assert rollout.reaches(["core/channels.py"], files["telegram"], "telegram")
    assert not rollout.reaches(["core/channels.py"], files["slack"], "slack")
    assert not rollout.reaches(["bridges/slack/sub/x.py"], files["telegram"], "telegram")
    assert rollout.reaches(["bridges/local/chat.js"], files["local"], "local")
    for name, f in files.items():
        assert rollout.reaches(["uv.lock"], f, name) and rollout.reaches(["pyproject.toml"], f, name)
        quiet = ["docs/a.md", "tests/t.py", "persona/p.md", "skills/s.md", "README.md"]
        assert not rollout.reaches(quiet, f, name)


def test_which_live_bridges_are_stale(tmp_path):
    co, base = layout(tmp_path)
    shared = commit(co, "core/channels.py", "C = 2\n", "shared")
    quiet = commit(co, "core/serve.py", "x = 1\n", "kernel only")
    ahead = commit(co, "bridges/telegram/wire.py", "import os\n", "telegram ahead")
    running = quiet
    # Current; behind with a diff that misses them.
    assert rollout.stale(co, running, [("telegram", running), ("slack", base), ("local", base)]) == set()
    assert rollout.stale(co, running, [("telegram", base)]) == {"telegram"}  # behind, reached
    assert rollout.stale(co, running, [("telegram", shared)]) == set()  # behind, quiet diff
    assert rollout.stale(co, running, [("telegram", ahead)]) == {"telegram"}  # ahead, reached
    assert rollout.stale(co, running, [("telegram", None)]) == {"telegram"}  # unstamped
    assert rollout.stale(co, running, [("telegram", "f" * 40)]) == {"telegram"}  # unknown sha
    assert rollout.stale(co, running, [("telegram", rollout.NO_HEAD)]) == set()


def test_a_kick_is_launchctl_kickstart_and_its_failure_a_reason(monkeypatch):
    import core.bridge
    import core.serve

    for name, label in rollout.BRIDGES.items():
        main = __import__(f"bridges.{name}.__main__", fromlist=["LABEL"])
        assert main.LABEL == label
    assert core.bridge.NO_HEAD == rollout.NO_HEAD
    monkeypatch.setattr(rollout.binaries, "require", lambda p: str(p))
    assert rollout.kick_argv("com.x") == ["/bin/launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.x"]
    ran = []

    def done(code):
        def fake(argv, **kw):
            ran.append(argv)
            return subprocess.CompletedProcess(argv, code, "", "Could not find service\n")

        return fake

    monkeypatch.setattr(rollout.subprocess, "run", done(0))
    assert rollout.kick("com.x") is None and ran[-1][-1].endswith("/com.x")
    monkeypatch.setattr(rollout.subprocess, "run", done(113))
    assert rollout.kick("com.x") == "launchctl kickstart exited with status 113: Could not find service"

    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(rollout.subprocess, "run", slow)
    assert "did not return" in rollout.kick("com.x")


async def holding(dsn, name: str, channel: str | None = None):
    """A connection named `name`, holding `channel`'s bridge lock when given
    (an advisory lock is the database's own: the machine's bridges hold
    theirs in the kernel's)."""
    conn = await db.connect(dsn, application_name=name)
    if channel:
        await conn.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"bridge:{channel}:{settings.machine}",)
        )
    return conn


def test_a_rollouts_end_restarts_the_stale_bridges_holding_their_locks(fresh, tmp_path, monkeypatch):
    """A stale holder is kicked before `rollout.ended`, which names it; a
    current holder, a stale waiter, other connections, and another
    database's bridge are not. (`NO_HEAD`: `test_which_live_bridges_are_stale`.)"""
    co, base = layout(tmp_path)
    commit(co, "core/channels.py", "C = 2\n", "channels")  # reaches telegram
    running = commit(co, "core/bridge.py", "X = 1\n", "bridge")  # reaches slack, local
    kicked: list[str] = []
    other = db.migrate(f"{TEST_DB}_rollout_other", fresh=True)

    async def go():
        task = await new_task(fresh)
        effect = uuid.uuid4().hex
        async with await db.connect(fresh) as conn:
            await ledger.append(conn, task, "rollout.restarting", {"effect_id": effect, "sha": running})
        conns = [
            await holding(fresh, f"valor-telegram@{base}", "telegram"),
            await holding(fresh, f"valor-slack@{running}", "slack"),
            await holding(fresh, "valor-email", "email"),  # unstamped: started before stamps
            await holding(fresh, "valor-email-watch"), await holding(fresh, "valor-kernel"),
            await holding(fresh, f"valor-bogus@{base}"), await holding(fresh, "valor-telegram-perform"),
            await holding(other, f"valor-local@{base}", "local"),  # another database's
        ]  # fmt: skip
        # A second slack, stale, waiting on the lock the current one holds.
        waiter = await holding(fresh, f"valor-slack@{base}")
        conns.insert(0, waiter)  # closed after the holder, so its wait ends
        waiting = asyncio.create_task(
            waiter.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"bridge:slack:{settings.machine}",)
            )
        )
        async with await db.connect(fresh) as conn:
            q = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
            while (await (await conn.execute(q)).fetchone())[0] == 0:
                await asyncio.sleep(0.05)

        def kick(label):
            with psycopg.connect(fresh) as c:  # nothing is ended before the kick
                assert (
                    c.execute("SELECT count(*) FROM events WHERE type = 'rollout.ended'").fetchone()[0] == 0
                )
            kicked.append(label)
            return "no such service" if label == "com.valor.email" else None

        try:
            async with await db.connect(fresh) as conn:
                done = await serve.recover(conn, checkout=co, running=running, kick=kick)
                again = await serve.recover(conn, checkout=co, running=running, kick=kick)
        finally:
            for c in reversed(conns):
                await c.close()
            waiting.cancel()
        return task, effect, done, again

    task, effect, done, again = run(go())
    assert kicked == ["com.valor.kernel.telegram", "com.valor.email"]
    assert done["bridges"] == ["com.valor.kernel.telegram"] and done["rolled"] == [effect]
    assert again["bridges"] == [] and again["rolled"] == []  # nothing to end: no kick
    (ended,) = typed(run(rows(fresh, task)), "rollout.ended")
    assert ended["restarted"] == {"com.valor.kernel.telegram": base}
    assert ended["restart_failed"] == {"com.valor.email": "no such service"}


def test_a_failed_bridge_pass_still_ends_the_rollout_and_the_stamp(fresh, tmp_path, monkeypatch):
    import core.bridge

    co, running = layout(tmp_path)

    async def go():
        task = await new_task(fresh)
        async with await db.connect(fresh) as conn:
            await ledger.append(conn, task, "rollout.restarting", {"effect_id": "e1", "sha": running})

            async def broken(*a, **kw):
                raise psycopg.OperationalError("gone")

            monkeypatch.setattr(serve, "restart_bridges", broken)
            done = await serve.recover(conn, checkout=co, running=running, kick=lambda label: None)
        return task, done

    task, done = run(go())
    assert done["rolled"] == ["e1"] and done["bridges"] == []
    (ended,) = typed(run(rows(fresh, task)), "rollout.ended")
    assert "restarted" not in ended

    # The stamp: the lock connection names the head, or `none` when git fails.
    seen = []

    class Quiet:
        channel = "local"

        def performers(self):
            return {}

        async def reconcile(self, outbox):
            pass

        async def run(self, outbox):
            row = await (
                await outbox.conn.execute(
                    "SELECT application_name FROM pg_stat_activity WHERE pid = pg_backend_pid()"
                )
            ).fetchone()
            seen.append(row[0])

    monkeypatch.setattr(core.bridge, "ROOT", co)

    def untrusted(root):
        raise rollout.binaries.Untrusted("no git")

    for head in (lambda root: running, untrusted):
        monkeypatch.setattr(core.bridge.git, "head", head)
        with pytest.raises(SystemExit):
            run(core.bridge.serve(Quiet(), fresh))
    assert seen == [f"valor-local@{running}", "valor-local@none"]
