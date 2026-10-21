"""The kernel rolling itself forward to its own merges (`core/rollout.py`,
`Kernel.roll`) on real Postgres and checkouts cloned from a local bare
remote: fast-forward and restart, `recover` ending the rollout, a dependency
or schema change rolling with its sync or backup, and `serve` exiting for
the restart. Backup, sync, migrate and restart are stand-ins. No model call.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import mock

import psycopg
import pytest

from core import credentials, db, git, intake, ledger, rollout, serve
from core.settings import settings
from tests import bridges, scripted
from tests.bridges import FakeBridge, new_task, of_type, rows, typed
from tests.conftest import TEST_DB
from tests.scripted import commit

pytestmark = pytest.mark.spend(usd=0)


run = asyncio.run


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


async def landed(dsn, task, remote, sha) -> str:
    """A merge effect's rows as the broker writes them: held, intent, outcome."""
    effect = uuid.uuid4().hex
    held = {"effect_id": effect, "action_type": "merge", "target": "main", "payload": {"head_sha": sha}}
    held["idempotency_key"] = effect
    result = {"remote": remote, "branch": "main", "sha": sha}
    async with await db.connect(dsn) as conn, conn.transaction():
        await ledger.append(conn, task, "effect.held", {**held, "effect_class": "act"})
        await ledger.append(conn, task, "effect.intent", {**held, "approval_id": None})
        await ledger.append(
            conn, task, "effect.outcome", {"effect_id": effect, "kind": "done", "result": result}
        )
    return effect


def kernel(dsn, repo: Repo, task: str) -> serve.Kernel:
    """A kernel on the test database rolling `repo.co`, the stand-in
    backup, sync, and migrate recording each call, in one list `k.ran` of
    `(step, the checkout's head then)`. `ready` records each task `schedule`
    considers, so an empty list after a wake means no job could have
    started."""

    def step(name: str, *database: str) -> None:
        k.ran.append((name, repo.head()))
        if name == "migrate":
            k.migrated.extend(database)

    k = serve.Kernel(
        None, {}, None, dsn, checkout=repo.co, started=repo.base, sync=lambda: step("sync"),
        migrate=lambda d: step("migrate", d), backup=lambda d: step("backup", d),
    )  # fmt: skip
    k.ran, k.migrated, k.ready = [], [], []

    async def active(conn):
        return [task]

    async def ready(conn, task_id):
        k.ready.append(task_id)

    k.active, k._ready = active, ready
    return k


async def tick(k: serve.Kernel, dsn: str) -> str | None:
    """One wake: the sha of a `Restart`, or None."""
    k.ready.clear()
    async with await db.connect(dsn) as conn:
        try:
            await k.tick(conn)
        except rollout.Restart as exc:
            return exc.sha
    return None


def dbname(dsn: str) -> str:
    return psycopg.conninfo.conninfo_to_dict(dsn)["dbname"]


async def rolled(dsn, repo: Repo, sha: str) -> tuple:
    """A task whose merge of `sha` landed, and the kernel's tick after it."""
    task = await new_task(dsn)
    effect = await landed(dsn, task, repo.url, sha)
    k = kernel(dsn, repo, task)
    return task, effect, k, await tick(k, dsn)


async def recovered(dsn, repo: Repo, running: str) -> list[str]:
    async with await db.connect(dsn) as conn:
        return (await serve.recover(conn, checkout=repo.co, running=running))["rolled"]


# -- done, as evidence -------------------------------------------------------------------


def test_a_core_merge_restarts_and_recover_ends_it(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")

    task, effect, k, restarted = run(rolled(fresh, repo, sha))
    assert restarted == sha
    (row,) = typed(run(rows(fresh, task)), "rollout.restarting")
    steps = ["fetch", "restart class", "fast-forward", "migrate"]
    assert row == {"effect_id": effect, "sha": sha, "from": repo.base, "steps": steps, "covers": []}
    assert repo.head() == sha
    assert k.migrated == [dbname(fresh)]
    assert k.ready == []  # no job started on that wake

    assert run(recovered(fresh, repo, sha)) == [effect]
    (ended,) = typed(run(rows(fresh, task)), "rollout.ended")
    assert ended == {"effect_id": effect, "outcome": "done", "head": sha}
    assert run(recovered(fresh, repo, sha)) == []


# -- dependencies and schema -------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "first"), [("uv.lock", "sync"), ("pyproject.toml", "sync"), ("core/schema.sql", "backup")]
)
def test_a_dependency_or_schema_change_rolls_with_its_sync_or_backup(fresh, tmp_path, path, first):
    """The backup runs before the checkout moves; the sync after the
    fast-forward, against the merged lock, before the migrate."""
    ran = [first, "migrate"]
    repo = Repo(tmp_path)
    sha = repo.land(path)

    task, effect, k, restarted = run(rolled(fresh, repo, sha))
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
    "bridges/__init__.py": "", "bridges/telegram/__init__.py": "",
    "bridges/telegram/bridge.py": "from . import wire\n\ndef go():\n    from core import intake\n",
    "bridges/telegram/wire.py": "import json\n", "bridges/slack/sub/x.py": "",
    "bridges/slack/__init__.py": "from bridges.slack.sub import x\n",
    "bridges/slack/sub/__init__.py": "from core.bridge import Outbox\n",
    "bridges/local/chat.js": "", "bridges/local/__init__.py": "import core.bridge\n", "core/__init__.py": "",
    "core/intake.py": "from core.channels import C\n", "core/channels.py": "C = 1\n", "core/bridge.py": "",
    "core/serve.py": "from core import intake\n",
}  # fmt: skip


def layout(tmp_path: Path) -> tuple[Path, str]:
    co = tmp_path / "co"
    subprocess.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    for path, text in LAYOUT.items():
        sha = commit(co, path, text, path)
    return co, sha


def test_a_bridges_closure_and_what_a_diff_reaches(tmp_path):
    co, _ = layout(tmp_path)
    files = {n: rollout.closure(co, n) for n in ("telegram", "slack", "local")}
    assert files["telegram"] == {
        "bridges/__init__.py", "bridges/telegram/__init__.py", "bridges/telegram/bridge.py",
        "bridges/telegram/wire.py", "core/__init__.py", "core/intake.py", "core/channels.py",
    }  # fmt: skip
    assert {"bridges/slack/sub/__init__.py", "core/bridge.py", "core/__init__.py"} <= files["slack"]
    assert not {"core/intake.py", "core/serve.py"} & files["slack"]
    # This checkout: the shared code is in every bridge's, the kernel's own in none.
    for name in rollout.BRIDGES:
        mine = rollout.closure(Path(__file__).resolve().parent.parent, name)
        assert {"core/bridge.py", "core/intake.py", "core/channels.py"} <= mine, name
        assert not {"core/serve.py", "core/rollout.py", "core/router.py"} & mine, name
    assert rollout.reaches(["core/channels.py"], files["telegram"], "telegram")
    assert not rollout.reaches(["core/channels.py"], files["slack"], "slack")
    assert not rollout.reaches(["bridges/slack/sub/x.py"], files["telegram"], "telegram")
    assert rollout.reaches(["bridges/local/chat.js"], files["local"], "local")
    quiet = ["docs/a.md", "tests/t.py", "persona/p.md", "skills/s.md", "README.md"]
    for name, f in files.items():
        assert rollout.reaches(["uv.lock"], f, name) and rollout.reaches(["pyproject.toml"], f, name)
        assert not rollout.reaches(quiet, f, name)


def test_which_live_bridges_are_stale(tmp_path):
    co, base = layout(tmp_path)
    shared = commit(co, "core/channels.py", "C = 2\n", "shared")
    running = commit(co, "core/serve.py", "x = 1\n", "kernel only")
    ahead = commit(co, "bridges/telegram/wire.py", "import os\n", "telegram ahead")
    assert rollout.stale(co, running, [("telegram", running), ("slack", base), ("local", base)]) == set()
    # Behind and reached, behind on a quiet diff, ahead, unstamped, an unknown sha, no head.
    for sha, stale in [(base, 1), (shared, 0), (ahead, 1), (None, 1), ("f" * 40, 1), (rollout.NO_HEAD, 0)]:
        assert rollout.stale(co, running, [("telegram", sha)]) == ({"telegram"} if stale else set()), sha


def test_a_kick_is_launchctl_kickstart_and_its_failure_a_reason(monkeypatch):
    import core.bridge

    for name, label in rollout.BRIDGES.items():
        assert __import__(f"bridges.{name}.__main__", fromlist=["LABEL"]).LABEL == label
    assert core.bridge.NO_HEAD == rollout.NO_HEAD
    monkeypatch.setattr(rollout.binaries, "require", lambda p: str(p))
    assert rollout.kick_argv("com.x") == ["/bin/launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.x"]
    outcomes = iter([0, 113, None])  # one per kick below; None: it never returns

    def fake(argv, timeout, **kw):
        assert argv == rollout.kick_argv("com.x")
        if (code := next(outcomes)) is None:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, code, "", "Could not find service\n")

    monkeypatch.setattr(rollout.subprocess, "run", fake)
    assert rollout.kick("com.x") is None
    assert rollout.kick("com.x") == "launchctl kickstart exited with status 113: Could not find service"
    assert "did not return" in rollout.kick("com.x")


async def holding(dsn, name: str, channel: str | None = None, wait: bool = True):
    """A connection named `name`, holding (or with `wait=False` waiting on)
    `channel`'s bridge lock: an advisory lock is the database's own, and the
    machine's bridges hold theirs in the kernel's."""
    conn = await db.connect(dsn, application_name=name)
    lock = ("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"bridge:{channel}:{settings.machine}",))
    if channel:
        conn.waiting = asyncio.create_task(conn.execute(*lock)) if not wait else await conn.execute(*lock)
    return conn


def test_a_rollouts_end_restarts_the_stale_bridges_holding_their_locks(fresh, tmp_path):
    """A stale holder is kicked before `rollout.ended`, which names it; a
    current holder, a stale waiter, other connections, and another
    database's bridge are not. (`NO_HEAD`: `test_which_live_bridges_are_stale`.)"""
    co, base = layout(tmp_path)
    commit(co, "core/channels.py", "C = 2\n", "channels")  # reaches telegram
    running = commit(co, "core/bridge.py", "X = 1\n", "bridge")  # reaches slack, local
    kicked: list[str] = []
    other = db.migrate(f"{TEST_DB}_rollout_other", fresh=True)

    def kick(label):
        with psycopg.connect(fresh) as c:  # nothing is ended before the kick
            assert c.execute("SELECT count(*) FROM events WHERE type = 'rollout.ended'").fetchone()[0] == 0
        kicked.append(label)
        return "no such service" if label == "com.valor.email" else None

    async def go():
        task, effect = await new_task(fresh), uuid.uuid4().hex
        async with await db.connect(fresh) as conn:
            await ledger.append(conn, task, "rollout.restarting", {"effect_id": effect, "sha": running})
        named = [(f"valor-telegram@{base}", "telegram"), (f"valor-slack@{running}", "slack"),
                 ("valor-email", "email"),  # unstamped: started before stamps
                 ("valor-email-watch", None), (f"valor-kernel@{base}", None), (f"valor-bogus@{base}", None),
                 ("valor-telegram-perform", None)]  # fmt: skip
        conns = [await holding(fresh, n, ch) for n, ch in named]
        conns.append(await holding(other, f"valor-local@{base}", "local"))
        # A second slack, stale, waiting on the lock the current one holds; closed last, so its wait ends.
        waiter = await holding(fresh, f"valor-slack@{base}", "slack", wait=False)
        async with await db.connect(fresh) as conn:
            q = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
            while (await (await conn.execute(q)).fetchone())[0] == 0:
                await asyncio.sleep(0.05)
        try:
            async with await db.connect(fresh) as conn:
                done = await serve.recover(conn, checkout=co, running=running, kick=kick)
                again = await serve.recover(conn, checkout=co, running=running, kick=kick)
        finally:
            for c in [*reversed(conns), waiter]:
                await c.close()
            waiter.waiting.cancel()
        return task, effect, done, again

    task, effect, done, again = run(go())
    # The dashboard last, every time: it holds no lock to read.
    assert kicked == ["com.valor.kernel.telegram", "com.valor.email", rollout.UI]
    assert done["bridges"] == ["com.valor.kernel.telegram", rollout.UI] and done["rolled"] == [effect]
    assert again["bridges"] == [] and again["rolled"] == []  # nothing to end: no kick
    (ended,) = typed(run(rows(fresh, task)), "rollout.ended")
    assert ended["restarted"] == {"com.valor.kernel.telegram": base, rollout.UI: None}
    assert ended["restart_failed"] == {"com.valor.email": "no such service"}


def test_a_failed_bridge_pass_still_ends_the_rollout_and_the_stamp(fresh, tmp_path, monkeypatch):
    import core.bridge

    co, running = layout(tmp_path)
    calls = []

    async def broken(*a, **kw):
        calls.append(a)
        raise psycopg.OperationalError("gone")

    async def go():
        task = await new_task(fresh)
        async with await db.connect(fresh) as conn:
            await ledger.append(conn, task, "rollout.restarting", {"effect_id": "e1", "sha": running})
            monkeypatch.setattr(serve, "restart_bridges", broken)
            unread = await serve.recover(conn, checkout=None, running=running, kick=lambda label: None)
            assert calls == []  # no checkout: no bridge is read or kicked
            done = await serve.recover(conn, checkout=co, running=running, kick=lambda label: None)
        return task, unread, done

    task, unread, done = run(go())
    assert unread["rolled"] == [] and unread["bridges"] == []
    assert done["rolled"] == ["e1"] and done["bridges"] == []
    (ended,) = typed(run(rows(fresh, task)), "rollout.ended")
    assert "restarted" not in ended
    # The stamp: the lock connection names the head, or `none` when git fails.
    seen, bridge = [], FakeBridge("local")

    async def named(outbox):
        q = "SELECT application_name FROM pg_stat_activity WHERE pid = pg_backend_pid()"
        seen.append((await (await outbox.conn.execute(q)).fetchone())[0])

    bridge.run = named
    monkeypatch.setattr(core.bridge, "ROOT", co)
    for head in (lambda root: running, mock.Mock(side_effect=rollout.binaries.Untrusted("no git"))):
        monkeypatch.setattr(core.bridge.git, "head", head)
        with pytest.raises(SystemExit):
            run(core.bridge.serve(bridge, fresh))
    assert seen == [f"valor-local@{running}", "valor-local@none"]


# -- updates (docs/plans/update.md) ------------------------------------------------------

ROOM = intake.UPDATE_CHAT.partition(":")[2]


async def asked(dsn, **kw) -> str:
    async with await db.connect(dsn) as conn:
        return await intake.request_update(conn, by="tom", via="test", **kw)


def updates(dsn) -> list[dict]:
    return run(rows(dsn, intake.UPDATES))


def test_an_update_to_a_tip_ahead_restarts_and_recover_ends_it_kicking_the_dashboard(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    request = run(asked(fresh))
    assert run(asked(fresh)) == request  # one open request: a second joins it
    assert run(tick(kernel(fresh, repo, "none"), fresh)) == sha and repo.head() == sha
    (restarting,) = typed(updates(fresh), "rollout.restarting")
    assert (restarting["effect_id"], restarting["sha"], restarting["from"]) == (request, sha, repo.base)
    kicked: list[str] = []

    async def go():
        bridge = await holding(fresh, "valor-telegram", "telegram")  # unstamped: stale
        try:
            async with await db.connect(fresh) as conn:
                return await serve.recover(
                    conn, checkout=repo.co, running=sha, kick=kicked.append, setup=dict
                )
        finally:
            await bridge.close()

    assert run(go())["rolled"] == [request]
    assert kicked == ["com.valor.kernel.telegram", rollout.UI]
    (ended,) = typed(updates(fresh), "rollout.ended")
    assert (ended["outcome"], ended["head"]) == ("done", sha)
    assert "setup" not in ended  # nothing fixed: nothing recorded
    assert run(asked(fresh)) != request  # ended: the next request is a new one


def test_an_update_to_the_started_commit_ends_up_to_date_that_wake(fresh, tmp_path):
    repo = Repo(tmp_path)
    request = run(asked(fresh))
    assert run(tick(kernel(fresh, repo, "none"), fresh)) is None
    (ended,) = typed(updates(fresh), "rollout.ended")
    assert ended == {"effect_id": request, "outcome": "up_to_date", "sha": repo.base}


def test_a_fast_forward_only_update_ends_with_from(fresh, tmp_path):
    repo = Repo(tmp_path)
    sha = repo.land("docs/x.md")
    request = run(asked(fresh))
    assert run(tick(kernel(fresh, repo, "none"), fresh)) is None
    (ended,) = typed(updates(fresh), "rollout.ended")
    assert ended == {
        "effect_id": request,
        "outcome": "done",
        "head": sha,
        "steps": ["fetch", "restart class", "fast-forward"],
        "from": repo.base,
    }


def test_a_merge_due_with_an_update_rolls_once_by_the_request(fresh, tmp_path):
    repo = Repo(tmp_path)
    merged = repo.land("core/b.py")
    task = run(new_task(fresh))
    effect = run(landed(fresh, task, repo.url, merged))
    tip = repo.land("core/c.py")
    request = run(asked(fresh))
    assert run(tick(kernel(fresh, repo, task), fresh)) == tip
    (restarting,) = typed(updates(fresh), "rollout.restarting")
    assert restarting["effect_id"] == request
    assert restarting["covers"] == [{"effect_id": effect, "task_id": task, "sha": merged}]
    assert typed(run(rows(fresh, task)), "rollout.restarting") == []
    assert sorted(run(recovered(fresh, repo, tip))) == sorted([effect, request])
    (ended,) = typed(run(rows(fresh, task)), "rollout.ended")
    assert ended["rolled_by"] == request


def test_a_judge_error_fails_the_merge_and_never_the_update(fresh, tmp_path, monkeypatch):
    repo = Repo(tmp_path)
    task = run(new_task(fresh))
    run(landed(fresh, task, repo.url, repo.land("core/b.py")))
    run(asked(fresh))

    def refused(*a):
        raise git.GitError("config refused")

    monkeypatch.setattr(rollout, "judge", refused)
    assert run(tick(kernel(fresh, repo, task), fresh)) is None
    (failed,) = typed(run(rows(fresh, task)), "rollout.failed")
    assert failed["step"] == "fetch"
    assert typed(updates(fresh), "rollout.failed") == []


@pytest.mark.parametrize("error", [git.GitError("no remote"), credentials.CredentialError("no key")])
def test_a_failed_update_fetch_waits_for_the_tick_and_tells_the_room(fresh, tmp_path, monkeypatch, error):
    repo = Repo(tmp_path)
    request = run(asked(fresh, reply={"channel": "telegram", "chat_id": ROOM, "message_id": "77"}))
    calls = []

    def tip(*a):
        calls.append(a)
        raise error

    monkeypatch.setattr(rollout, "tip", tip)
    k = kernel(fresh, repo, "none")
    run(tick(k, fresh))
    run(tick(k, fresh))
    assert len(calls) == 1  # not again before the next `serve_tick_s` wake
    (failed,) = typed(updates(fresh), "rollout.failed")
    assert (failed["effect_id"], failed["step"], failed["reason"]) == (request, "fetch", str(error))
    for _ in range(rollout.NOTICE_AFTER - 1):
        k.parked_at = float("-inf")
        run(tick(k, fresh))
    assert len(calls) == rollout.NOTICE_AFTER
    (notice,) = typed(updates(fresh), "notice.requested")
    assert notice["text"].startswith("I could not update my own system: ")
    assert (notice["channel"], notice["chat_id"], notice["reply_to"]) == ("telegram", ROOM, "77")
    assert len(typed(updates(fresh), "rollout.failed")) == 1  # the same step and reason: one row


def test_a_failed_update_fetch_stays_open_across_a_start_and_rolls_on_the_next_fetch(
    fresh, tmp_path, monkeypatch
):
    repo = Repo(tmp_path)
    sha = repo.land("core/b.py")
    request = run(asked(fresh, reply={"channel": "telegram", "chat_id": ROOM, "message_id": "77"}))

    def down(*a):
        raise git.GitError("network down")

    with monkeypatch.context() as m:
        m.setattr(rollout, "tip", down)
        run(tick(kernel(fresh, repo, "none"), fresh))
    assert run(recovered(fresh, repo, repo.base)) == []
    assert typed(updates(fresh), "rollout.ended") == []
    assert run(tick(kernel(fresh, repo, "none"), fresh)) == sha and repo.head() == sha
    assert not [n for n in typed(updates(fresh), "notice.requested") if n["text"].startswith("I run")]
    assert run(recovered(fresh, repo, sha)) == [request]
    (ended,) = typed(updates(fresh), "rollout.ended")
    assert (ended["outcome"], ended["head"]) == ("done", sha)


def test_update_wait_past_its_deadline_exits_1_writing_nothing_more(fresh, monkeypatch):
    import argparse

    import core.__main__ as main

    monkeypatch.setattr(main, "UPDATE_WAIT_S", 0.0)

    async def go():
        async with await db.connect(fresh) as conn:
            await main._update(conn, argparse.Namespace(by="tom", wait=True))

    with pytest.raises(SystemExit) as exc:
        run(go())
    assert "still open" in str(exc.value.code)
    assert [r["type"] for r in updates(fresh)] == ["update.requested"]


async def posted(dsn, text: str, chat: str = ROOM) -> dict:
    """A Telegram group message from a member who is not Tom, received
    and bound: its binding."""
    mid = str(uuid.uuid4().int % 10**9)
    inbound = intake.Inbound(
        channel="telegram", chat_id=chat, chat_kind="group", message_id=mid, sender_id="78",
        sender_name="Kim", sent_at="2026-10-20T09:30:00+00:00", text=text, reply_to=None,
    )  # fmt: skip
    async with await db.connect(dsn) as conn:
        got = await intake.receive(conn, inbound)
        await intake.bind(conn)
    (bound,) = await of_type(dsn, "message.bound", received_id=got.received_id)
    return bound | {"message_id": mid}


def test_update_in_the_engineering_room_is_a_request_and_one_reply(fresh, tmp_path):
    with bridges.operator(tmp_path):
        (Path(settings.projects_dir) / "valor.toml").write_text(
            bridges.VALOR_SPEC + f'chats = ["telegram:{ROOM}", "telegram:-1001"]\n'
        )
        first = run(posted(fresh, " /update "))
        second = run(posted(fresh, "/update@valorengels_bot"))
        assert (first["as"], second["as"]) == ("update", "update")
        (request,) = typed(updates(fresh), "update.requested")
        assert (request["by"], request["via"]) == ("Kim", "telegram")
        assert request["reply"] == {"channel": "telegram", "chat_id": ROOM, "message_id": first["message_id"]}
        for text, chat in (("/update please", ROOM), ("/update", "-1001")):
            assert run(posted(fresh, text, chat))["as"] != "update"
        assert len(typed(updates(fresh), "update.requested")) == 1

        repo = Repo(tmp_path)
        for _ in range(2):  # a new kernel each time, as after a restart
            k = kernel(fresh, repo, "none")
            run(tick(k, fresh))
            run(tick(k, fresh))
        (reply,) = typed(updates(fresh), "notice.requested")
        assert (reply["about_key"], reply["kind"]) == (f"update:{request['request_id']}", "update")
        assert (reply["chat_id"], reply["reply_to"]) == (ROOM, first["message_id"])
        assert reply["text"] == f"I am already up to date, at {repo.base[:12]}."


def test_the_update_routine_writes_one_request_and_no_task(fresh):
    import core.__main__ as main
    from core import routines

    async def go():
        async with await db.connect(fresh) as conn:
            await routines.run(conn, "update", main._routine_runners(), directory=serve.ROOT / "routines")

    run(go())
    (request,) = typed(updates(fresh), "update.requested")
    assert (request["by"], request["via"]) == ("routine", "routine")
    (ran,) = run(of_type(fresh, "routine.ran"))
    assert (ran["run"], ran["outcome"]) == (None, "started")
    (objective,) = run(of_type(fresh, "task.started"))  # the routine's own record; no run task
    assert objective["objective"] == "update"


def test_python_m_ui_plist_is_the_dashboards_job():
    import plistlib

    done = subprocess.run([sys.executable, "-m", "ui", "--plist"], capture_output=True, check=True)
    job, kernel_job = plistlib.loads(done.stdout), plistlib.loads(serve.plist())
    assert job["Label"] == rollout.UI == "com.valor.ui"
    assert job["ProgramArguments"] == [*kernel_job["ProgramArguments"][:2], "-m", "ui"]
    assert job["StandardOutPath"].endswith("ui.log") and "ProcessType" not in job


def test_an_update_that_rolls_nothing_still_checks_the_setup(fresh, tmp_path):
    repo = Repo(tmp_path)
    request = run(asked(fresh))
    k = kernel(fresh, repo, "none")
    k.setup = lambda: {"fixed": ["com.valor.voice"]}
    assert run(tick(k, fresh)) is None
    (ended,) = typed(updates(fresh), "rollout.ended")
    assert ended == {
        "effect_id": request,
        "outcome": "up_to_date",
        "sha": repo.base,
        "setup": {"fixed": ["com.valor.voice"]},
    }
