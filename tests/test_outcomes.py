"""What came after a task's merges (`core.outcomes`), on real git
repositories in `tmp_path` and the test database: what a merge landed,
recorded from the kernel mirror; rework as a fold over every task's merges;
revert and on-branch from the kernel's cache of the target; feedback after
a merge; and the `delivery.used` mark. Merges here are written as the
broker writes them (`effect.held`, `effect.intent` with `landed`,
`effect.outcome`); `tests/test_broker.py` releases real ones.

Live spend: none.
"""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core import db, git, ledger, machine, outcomes, tasks
from core import workspace as kws
from tests.conftest import TEST_DB

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def sh(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


def commit(ws: Path, path: str, text: str, message: str | None = None) -> str:
    p = ws / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    sh(ws, "add", path)
    sh(ws, "commit", "-qm", message or path)
    return sh(ws, "rev-parse", "HEAD")


class World:
    """A target repository (`up.git`), a clone to write commits in, the
    kernel's work directory with its cache of the target, and one kernel
    mirror per task, as `core.workspace` lays them out."""

    def __init__(self, tmp_path: Path):
        self.up = tmp_path / "up.git"
        sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(self.up))
        self.ws = tmp_path / "ws"
        sh(tmp_path, "clone", "-q", str(self.up), str(self.ws))
        sh(self.ws, "checkout", "-q", "-b", "main")
        self.base = commit(self.ws, "README.md", "toy\n")
        sh(self.ws, "push", "-q", "origin", "main")
        self.work = tmp_path / "work"
        self.cache = kws.cache_path(str(self.up), self.work)

    def fetch(self) -> None:
        """The kernel's cache of the target, fetched as `_cache` fetches it."""
        if not self.cache.exists():
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            sh(self.cache.parent, "init", "-q", "--bare", str(self.cache))
        sh(self.cache, "fetch", "-q", "--no-tags", str(self.up), "+refs/heads/*:refs/heads/*")

    def brief(self, *, branch: str = "main", private: bool = False, mirror: bool = True) -> tasks.Brief:
        task_id = ledger.new_id()
        root = self.work / task_id
        kernel = root / "kernel.git"
        if mirror:
            root.mkdir(parents=True)
            sh(root, "init", "-q", "--bare", str(kernel))
        return tasks.Brief(
            instruction="toy",
            target_branch=branch,
            origin_url=str(root / "origin.git") if private else str(self.up),
            base_sha=self.base,
            mirror=str(kernel) if mirror else None,
            push_url=str(root / "origin.git") if mirror else None,
            workspace=None if mirror else str(self.ws),
            id=task_id,
        )

    def branch(self, name: str, at: str | None = None) -> None:
        sh(self.ws, "checkout", "-q", "-B", name, at or self.base)

    def land(self, b: tasks.Brief, head: str) -> None:
        """The candidate in the task's mirror, then on the target, as a
        merge pushes it."""
        if b.mirror:
            sh(self.ws, "push", "-q", "-f", b.mirror, f"{head}:refs/heads/session")
        sh(self.ws, "push", "-q", "-f", str(self.up), f"{head}:refs/heads/{b.target_branch}")


async def start(conn, b: tasks.Brief, marker: dict | None = None) -> str:
    return await tasks.start(conn, b, marker=marker)


async def deliver(conn, task: str, candidate: str | None = None, summary: str = "done") -> int:
    return await ledger.append(
        conn, task, "task.delivered", {"candidate": candidate, "outcome": "merged", "summary": summary}
    )


async def feedback(conn, task: str, text: str) -> int:
    return await ledger.append(
        conn,
        task,
        "feedback.given",
        {
            "feedback_id": ledger.new_id(),
            "on_delivery": "done",
            "candidate": None,
            "text": text,
            "provenance": ledger.provenance("tom", "test", False),
        },
    )


async def merge(
    conn, b: tasks.Brief, head: str, *, kind: str = "done", landed: dict | None = None, read: bool = True
) -> str:
    """A merge as the broker records it: held, intent with `landed` (read
    by `outcomes.landed` unless given, absent when `read` is False), and
    outcome."""
    effect_id = ledger.new_id()
    payload = {"url": b.origin_url, "target_branch": b.target_branch, "head_sha": head, "candidate": head}
    rows = await ledger.read(conn, b.id)
    if landed is None and read:
        landed = await outcomes.landed(conn, b, rows, payload)
    await ledger.append(
        conn, b.id, "effect.held", {"effect_id": effect_id, "action_type": "merge", "payload": payload}
    )
    intent = {"effect_id": effect_id, "action_type": "merge"}
    await ledger.append(conn, b.id, "effect.intent", {**intent, **({"landed": landed} if read else {})})
    await ledger.append(conn, b.id, "effect.outcome", {"effect_id": effect_id, "kind": kind})
    return effect_id


async def after(conn, b: tasks.Brief) -> dict:
    return await outcomes.after_merge(conn, b, await ledger.read(conn, b.id))


def recorded(paths: list[str] | None) -> dict:
    return {"before": None, "commits": [] if paths is not None else None, "paths": paths, "why": None}


# -- what a merge landed ---------------------------------------------------------------


def test_two_merges_of_one_task_each_land_their_own_and_feedback_sits_on_the_first(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            first = commit(w.ws, "a.py", "a = 1\n")
            w.land(b, first)
            await deliver(conn, b.id, first)
            await merge(conn, b, first)
            await feedback(conn, b.id, "a is wrong")
            second = commit(w.ws, "a.py", "a = 2\n")
            w.land(b, second)
            await deliver(conn, b.id, second)
            await merge(conn, b, second)
            return first, second, await after(conn, b)

    first, second, out = run(go())
    one, two = out["after_merge"]
    assert one["landed"] == {"before": w.base, "commits": [first], "paths": ["a.py"], "why": None}
    assert two["landed"]["before"] == first and two["landed"]["commits"] == [second]
    assert [f["text"] for f in one["feedback"]] == ["a is wrong"] and two["feedback"] == []
    assert one["feedback"][0]["provenance"]["by"] == "tom"


def test_a_failed_merge_is_not_one(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            head = commit(w.ws, "a.py", "a = 1\n")
            w.land(b, head)
            await deliver(conn, b.id, head)
            failed = await merge(conn, b, head, kind="failed")
            done = await merge(conn, b, head)
            return failed, done, await after(conn, b)

    failed, done, out = run(go())
    assert [m["effect_id"] for m in out["after_merge"]] == [done] and failed != done


def test_feedback_before_a_merge_is_no_merges(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            await deliver(conn, b.id, "x")
            await feedback(conn, b.id, "patch this first")
            head = commit(w.ws, "a.py", "a = 1\n")
            w.land(b, head)
            await deliver(conn, b.id, head)
            await merge(conn, b, head)
            return await after(conn, b)

    (m,) = run(go())["after_merge"]
    assert m["feedback"] == []


def test_a_merge_lands_only_its_own_commits(dsn, tmp_path):
    """B was provisioned before A merged and took A's head in; B's own
    commits and paths leave A's out."""
    w = World(tmp_path)
    a, bb = w.brief(), w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, a)
            await start(conn, bb)
            w.branch("a")
            ha = commit(w.ws, "a.py", "a = 1\n")
            w.land(a, ha)
            await merge(conn, a, ha)
            w.branch("b")
            commit(w.ws, "b.py", "b = 1\n")
            sh(w.ws, "merge", "-q", "--no-edit", ha)
            hb = commit(w.ws, "b2.py", "b = 2\n")
            w.land(bb, hb)
            await merge(conn, bb, hb)
            return ha, await after(conn, bb)

    ha, out = run(go())
    (m,) = out["after_merge"]
    assert m["landed"]["paths"] == ["b.py", "b2.py"] and ha not in m["landed"]["commits"]
    assert len(m["landed"]["commits"]) == 2


def test_a_hand_commit_on_the_target_is_not_the_merges(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            w.branch("hand")
            hand = commit(w.ws, "a.py", "by hand\n")
            sh(w.ws, "push", "-q", str(w.up), f"{hand}:refs/heads/main")
            w.fetch()
            w.branch("b")
            commit(w.ws, "b.py", "b = 1\n")
            sh(w.ws, "merge", "-q", "--no-edit", hand)
            head = sh(w.ws, "rev-parse", "HEAD")
            w.land(b, head)
            await merge(conn, b, head)
            return hand, await after(conn, b)

    hand, out = run(go())
    (m,) = out["after_merge"]
    assert m["landed"]["paths"] == ["b.py"] and hand not in m["landed"]["commits"]


def test_a_head_the_mirror_lacks_records_nulls_not_an_empty_landing(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            await merge(conn, b, "f" * 40)
            return await after(conn, b)

    (m,) = run(go())["after_merge"]
    assert m["landed"]["commits"] is None and m["landed"]["paths"] is None
    assert m["landed"]["why"] == "GitError"


def test_a_workspace_merge_records_no_paths_and_runs_no_git(dsn, tmp_path, monkeypatch):
    w = World(tmp_path)
    b = w.brief(mirror=False)
    calls = []
    real = git._git

    def counted(*args, **kw):
        calls.append(args)
        return real(*args, **kw)

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            head = commit(w.ws, "a.py", "a = 1\n")
            monkeypatch.setattr(git, "_git", counted)
            await merge(conn, b, head)
            landed_calls = list(calls)
            return landed_calls, await after(conn, b)

    landed_calls, out = run(go())
    (m,) = out["after_merge"]
    assert landed_calls == []
    assert m["landed"] == {"before": w.base, "commits": None, "paths": None, "why": outcomes.NO_KERNEL_COPY}
    assert m["paths"] is None and m["rework_why"] == outcomes.NOT_RECORDED
    assert m["revert"] is None and m["revert_why"] == f"{outcomes.NO_KERNEL_COPY} of the target"


# -- rework ----------------------------------------------------------------------------


def test_rework_lists_later_merges_on_the_same_target_sharing_a_code_path(dsn):
    url = f"/toy/{ledger.new_id()}.git"

    def brief(branch="main"):
        return tasks.Brief(instruction="toy", origin_url=url, target_branch=branch)

    e, a, bb, c, d = brief(), brief(), brief(), brief(), brief("other")

    async def go():
        async with await db.connect(dsn) as conn:
            for t in (e, a, bb, c, d):
                await start(conn, t)
            await merge(conn, e, "e" * 40, landed=recorded(["a.py"]))
            await merge(conn, a, "a" * 40, landed=recorded(["a.py"]))
            await merge(conn, bb, "b" * 40, landed=recorded(["a.py", "b.py"]))
            await merge(conn, c, "c" * 40, landed=recorded(["c.py"]))
            await merge(conn, d, "d" * 40, landed=recorded(["a.py"]))
            return await after(conn, a), await outcomes.done_merges(conn, url, "main")

    out, done = run(go())
    (m,) = out["after_merge"]
    assert [x["task_id"] for x in m["later"]] == [bb.id]
    assert m["later"][0]["shared_code"] == ["a.py"] and m["later"][0]["shared_docs"] == []
    assert isinstance(m["later"][0]["days_after"], float) and m["later"][0]["days_after"] >= 0
    assert m["later_docs"] == [] and m["later_unknown"] == [] and m["rework_why"] is None
    assert [x["task_id"] for x in done] == [e.id, a.id, bb.id, c.id]


def test_a_doc_only_overlap_is_later_docs_and_claude_md_is_code(dsn):
    url = f"/toy/{ledger.new_id()}.git"
    a, docs, rules = (tasks.Brief(instruction="toy", origin_url=url, target_branch="main") for _ in range(3))

    async def go():
        async with await db.connect(dsn) as conn:
            for t in (a, docs, rules):
                await start(conn, t)
            await merge(conn, a, "a" * 40, landed=recorded(["CLAUDE.md", "docs/data.md", "x.py"]))
            await merge(conn, docs, "b" * 40, landed=recorded(["docs/data.md"]))
            await merge(conn, rules, "c" * 40, landed=recorded(["CLAUDE.md"]))
            return await after(conn, a)

    (m,) = run(go())["after_merge"]
    assert [x["task_id"] for x in m["later_docs"]] == [docs.id]
    assert m["later_docs"][0]["shared_docs"] == ["docs/data.md"]
    assert [x["task_id"] for x in m["later"]] == [rules.id] and m["later"][0]["shared_code"] == ["CLAUDE.md"]


def test_unrecorded_paths_are_unknown_never_no_rework(dsn):
    url = f"/toy/{ledger.new_id()}.git"
    old, a, unknown = (tasks.Brief(instruction="toy", origin_url=url, target_branch="main") for _ in range(3))

    async def go():
        async with await db.connect(dsn) as conn:
            for t in (old, a, unknown):
                await start(conn, t)
            await merge(conn, old, "0" * 40, read=False)
            await merge(conn, a, "a" * 40, landed=recorded(["a.py"]))
            await merge(conn, unknown, "b" * 40, read=False)
            return await after(conn, old), await after(conn, a)

    first, second = run(go())
    (o,) = first["after_merge"]
    assert o["landed"] is None and o["paths"] is None and o["later"] is None and o["later_docs"] is None
    assert o["rework_why"] == outcomes.NOT_RECORDED and o["later_unknown"] == [unknown.id]
    (m,) = second["after_merge"]
    assert m["later"] == [] and m["later_unknown"] == [unknown.id]


def test_a_rename_counts_both_paths(dsn, tmp_path):
    w = World(tmp_path)
    a, bb = w.brief(), w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, a)
            await start(conn, bb)
            ha = commit(w.ws, "a.py", "a = 1\n")
            w.land(a, ha)
            await merge(conn, a, ha)
            sh(w.ws, "mv", "a.py", "c.py")
            sh(w.ws, "commit", "-qm", "rename")
            hb = sh(w.ws, "rev-parse", "HEAD")
            w.land(bb, hb)
            await merge(conn, bb, hb)
            return await after(conn, a), await after(conn, bb)

    out_a, out_b = run(go())
    assert out_b["after_merge"][0]["landed"]["paths"] == ["a.py", "c.py"]
    assert [x["task_id"] for x in out_a["after_merge"][0]["later"]] == [bb.id]


def test_a_non_ascii_doc_path_is_shared_docs(dsn, tmp_path):
    w = World(tmp_path)
    a, bb = w.brief(), w.brief()
    path = "docs/naïve.md"

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, a)
            await start(conn, bb)
            ha = commit(w.ws, path, "one\n")
            w.land(a, ha)
            await merge(conn, a, ha)
            hb = commit(w.ws, path, "two\n")
            w.land(bb, hb)
            await merge(conn, bb, hb)
            return await after(conn, a)

    (m,) = run(go())["after_merge"]
    assert m["landed"]["paths"] == [path]
    assert m["later_docs"][0]["shared_docs"] == [path] and m["later"] == []


# -- revert and on-branch --------------------------------------------------------------


def merged(dsn, w: World, b: tasks.Brief) -> tuple[str, str]:
    """B merged with one commit to `a.py`; returns the commit and head."""

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            w.branch("main")
            head = commit(w.ws, "a.py", "a = 1\n")
            w.land(b, head)
            await merge(conn, b, head)
            return head

    head = run(go())
    return head, head


def revert_of(dsn, b: tasks.Brief) -> tuple[dict | None, str | None]:
    async def go():
        async with await db.connect(dsn) as conn:
            (m,) = outcomes.merges(await ledger.read(conn, b.id))
            return outcomes.revert(b, m)

    return run(go())


def on_target(w: World, message: str, path: str = "z.py") -> str:
    """A commit on the target after the merge, fetched into the cache."""
    sh(w.ws, "fetch", "-q", "origin")
    w.branch("tip", sh(w.ws, "rev-parse", "origin/main"))
    p = w.ws / path
    p.write_text(p.read_text() + "x\n" if p.exists() else "x\n")
    sh(w.ws, "add", path)
    msg = w.ws.parent / "msg"
    msg.write_bytes(message.encode() if isinstance(message, str) else message)
    sh(w.ws, "commit", "-q", "-F", str(msg))
    sha = sh(w.ws, "rev-parse", "HEAD")
    sh(w.ws, "push", "-q", str(w.up), f"{sha}:refs/heads/main")
    w.fetch()
    return sha


def test_a_revert_after_the_head_names_the_merges_commit(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()
    c, _head = merged(dsn, w, b)
    r = on_target(w, f"Revert a\n\nThis reverts commit {c}.\n")
    reading, why = revert_of(dsn, b)
    assert why is None and reading["on_branch"] is True and reading["source"] == "cache"
    assert reading["reverted_by"] == [{"sha": r, "reverts": [c]}]


def test_a_github_style_revert_is_found(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()
    c, _ = merged(dsn, w, b)
    r = on_target(w, f'Revert "a"\n\nReverts owner/repo#5\n\nThis reverts commit {c}.\n')
    reading, _ = revert_of(dsn, b)
    assert reading["reverted_by"] == [{"sha": r, "reverts": [c]}]


def test_a_revert_of_an_outside_commit_or_by_abbreviated_sha_is_not_the_merges(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()
    c, _ = merged(dsn, w, b)
    on_target(w, f"Revert base\n\nThis reverts commit {w.base}.\n")
    on_target(w, f"Revert a\n\nThis reverts commit {c[:12]}.\n")
    reading, _ = revert_of(dsn, b)
    assert reading["reverted_by"] == [] and reading["on_branch"] is True


def test_a_body_holding_byte_0xff_gives_a_reading(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()
    c, _ = merged(dsn, w, b)
    r = on_target(w, b"odd \xff byte\n\nThis reverts commit " + c.encode() + b".\n")
    reading, why = revert_of(dsn, b)
    assert why is None and reading["reverted_by"] == [{"sha": r, "reverts": [c]}]


def test_a_branch_moved_off_the_head_is_not_on_branch(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()
    merged(dsn, w, b)
    w.fetch()
    sh(w.ws, "push", "-q", "-f", str(w.up), f"{w.base}:refs/heads/main")
    w.fetch()
    reading, why = revert_of(dsn, b)
    assert why is None and reading["on_branch"] is False and reading["reverted_by"] == []


def test_a_head_the_cache_lacks_is_not_fetched_since_the_merge(dsn, tmp_path):
    w = World(tmp_path)
    w.fetch()  # before the merge
    b = w.brief()
    merged(dsn, w, b)
    assert revert_of(dsn, b) == (None, outcomes.NOT_FETCHED)


def test_as_of_is_the_caches_fetch_time(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()
    merged(dsn, w, b)
    w.fetch()
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    os.utime(w.cache / "FETCH_HEAD", (when.timestamp(), when.timestamp()))
    reading, _ = revert_of(dsn, b)
    assert reading["as_of"] == when.isoformat()


def test_where_revert_reads(dsn, tmp_path):
    """A task on its own origin has no target to read; with no cache of
    the merge url there is none; a spec whose repo differs from the merge
    url reads the merge url's cache."""
    w = World(tmp_path)
    private, nocache, elsewhere = w.brief(private=True), w.brief(), w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            for t in (private, nocache, elsewhere):
                await start(conn, t)
            head = commit(w.ws, "a.py", "a = 1\n")
            for t in (private, nocache, elsewhere):
                w.land(t, head)
                await merge(conn, t, head)
            first = (await after(conn, private), await after(conn, nocache))
            # the spec's own repo is cached too; the merge url's cache is the one read
            other = tmp_path / "spec.git"
            sh(tmp_path, "clone", "-q", "--bare", str(w.up), str(other))
            spec_cache = kws.cache_path(str(other), w.work)
            sh(tmp_path, "clone", "-q", "--bare", str(other), str(spec_cache))
            missing = await after(conn, elsewhere)
            w.fetch()
            return first, missing, await after(conn, elsewhere)

    (p, n), missing, found = run(go())
    (m,) = p["after_merge"]
    assert m["revert"] is None and m["revert_why"] == outcomes.PRIVATE_TARGET
    assert m["later"] is None and m["later_docs"] is None and m["rework_why"] == outcomes.PRIVATE_TARGET
    assert n["after_merge"][0]["revert_why"] == outcomes.NO_CACHE
    assert missing["after_merge"][0]["revert_why"] == outcomes.NO_CACHE
    assert found["after_merge"][0]["revert"]["on_branch"] is True


def test_cache_path_names_the_directory_the_cache_is_made_in(tmp_path):
    src = tmp_path / "src"
    sh(tmp_path, "init", "-q", "-b", "main", str(src))
    commit(src, "README.md", "x\n")
    spec = kws.Spec.from_dict({"name": "toy", "repo": str(src), "kind": "plain", "suite": "true"})
    work = tmp_path / "work"
    assert kws._cache(spec, None, work) == kws.cache_path(str(src), work)
    assert kws.cache_path(str(src), work).is_dir()


# -- used ------------------------------------------------------------------------------


async def marks(conn, task: str) -> list[dict]:
    return [r["payload"] for r in await ledger.read(conn, task) if r["type"] == outcomes.USED]


def test_used_on_a_merged_task_names_its_delivery_and_merge(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            first = commit(w.ws, "a.py", "a = 1\n")
            w.land(b, first)
            await deliver(conn, b.id, first)
            await merge(conn, b, first)
            second = commit(w.ws, "a.py", "a = 2\n")
            w.land(b, second)
            delivered = await deliver(conn, b.id, second)
            effect = await merge(conn, b, second)
            state = machine.fold(await ledger.read(conn, b.id)).summary()["state"]
            used = await outcomes.mark_used(conn, b.id, by="tom", note="ran it")
            stand_in = await outcomes.mark_used(conn, b.id, by="stand-in", role_played=True)
            after_state = machine.fold(await ledger.read(conn, b.id)).summary()["state"]
            return (second, delivered, effect, used, stand_in, state, after_state,
                    await marks(conn, b.id), await after(conn, b))  # fmt: skip

    second, delivered, effect, used, stand_in, state, after_state, rows, out = run(go())
    assert state == after_state
    row = rows[0]
    assert row["used_id"] == used and row["delivery_event_id"] == delivered and row["candidate"] == second
    assert row["effect_id"] == effect and row["head_sha"] == second and row["note"] == "ran it"
    assert row["provenance"]["by"] == "tom" and row["provenance"]["role_played"] is False
    one, two = out["after_merge"]
    assert one["used"] == [] and [u["used_id"] for u in two["used"]] == [used, stand_in]
    assert two["used_count"] == 1 and out["deliveries_used"] == []


def test_used_on_a_delivered_task_with_no_merge_names_the_delivery(dsn, tmp_path):
    b = World(tmp_path).brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            delivered = await deliver(conn, b.id, "c" * 40)
            used = await outcomes.mark_used(conn, b.id, by="tom")
            return delivered, used, await marks(conn, b.id), await after(conn, b)

    delivered, used, rows, out = run(go())
    assert rows[0]["effect_id"] is None and rows[0]["head_sha"] is None
    assert rows[0]["delivery_event_id"] == delivered
    (d,) = out["deliveries_used"]
    assert d["event_id"] == delivered and [u["used_id"] for u in d["used"]] == [used] and d["used_count"] == 1
    assert out["after_merge"] == []


def test_used_after_patch_names_the_merged_delivery_and_delivery_names_another(dsn, tmp_path):
    """Merge, feedback, patch, back in merge with a new delivery: the mark
    names the delivery the merge carried; `delivery` names the new one."""
    w = World(tmp_path)
    b = w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            head = commit(w.ws, "a.py", "a = 1\n")
            w.land(b, head)
            merged_delivery = await deliver(conn, b.id, head)
            effect = await merge(conn, b, head)
            await feedback(conn, b.id, "patch it")
            newer = await deliver(conn, b.id, "f" * 40)
            await outcomes.mark_used(conn, b.id, by="tom")
            await outcomes.mark_used(conn, b.id, by="tom", delivery=newer)
            return merged_delivery, newer, effect, await marks(conn, b.id), await after(conn, b)

    merged_delivery, newer, effect, rows, out = run(go())
    assert rows[0]["delivery_event_id"] == merged_delivery and rows[0]["effect_id"] == effect
    assert rows[1]["delivery_event_id"] == newer and rows[1]["effect_id"] is None
    assert [d["event_id"] for d in out["deliveries_used"]] == [newer]
    assert out["after_merge"][0]["used_count"] == 1


def test_used_on_a_stopped_merged_task_records_and_the_merge_is_shown(dsn, tmp_path):
    w = World(tmp_path)
    b = w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            head = commit(w.ws, "a.py", "a = 1\n")
            w.land(b, head)
            await deliver(conn, b.id, head)
            await merge(conn, b, head)
            await ledger.append(conn, b.id, "task.stopped", {"reason": "done"})
            used = await outcomes.mark_used(conn, b.id, by="tom")
            return used, await after(conn, b)

    used, out = run(go())
    assert [u["used_id"] for u in out["after_merge"][0]["used"]] == [used]


def test_used_refuses_an_unknown_task_no_delivery_and_another_tasks_delivery(dsn, tmp_path):
    w = World(tmp_path)
    b, other = w.brief(), w.brief()

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            await start(conn, other)
            theirs = await deliver(conn, other.id)
            errors = []
            for task, kw in ((ledger.new_id(), {}), (b.id, {}), (b.id, {"delivery": theirs})):
                if kw:
                    await deliver(conn, b.id)
                try:
                    await outcomes.mark_used(conn, task, by="tom", **kw)
                except LookupError as exc:
                    errors.append(str(exc))
            return errors, theirs

    errors, theirs = run(go())
    assert errors[0].startswith("no task ")
    assert errors[1] == f"task {b.id} has no delivery to mark used"
    assert errors[2] == f"task {b.id} has no delivery {theirs}"


def test_used_without_by_exits_with_the_usage_error():
    out = subprocess.run(
        [sys.executable, "-m", "core", "used", "some-task"],
        cwd=ROOT,
        env={**os.environ, "VALOR_DB": TEST_DB},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 2 and "the following arguments are required: --by" in out.stderr


# -- the other surfaces ----------------------------------------------------------------


@pytest.mark.parametrize("marker", [{}, {"calibration": True}])
def test_legacy_and_calibration_tasks_give_empty_lists(dsn, marker):
    b = tasks.Brief(instruction="old")

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b, marker=marker)
            return await after(conn, b)

    assert run(go()) == {"after_merge": [], "deliveries_used": []}


def test_tasks_status_has_no_after_merge_and_runs_no_git(dsn, tmp_path, monkeypatch):
    w = World(tmp_path)
    b = w.brief()

    def refuse(*_a, **_kw):
        raise AssertionError("git ran")

    async def go():
        async with await db.connect(dsn) as conn:
            await start(conn, b)
            head = commit(w.ws, "a.py", "a = 1\n")
            w.land(b, head)
            await deliver(conn, b.id, head)
            await merge(conn, b, head)
            monkeypatch.setattr(git, "trusted", refuse)
            monkeypatch.setattr(git, "_git", refuse)
            return await tasks.status(conn, b.id)

    state = run(go())
    assert "after_merge" not in state and "deliveries_used" not in state


def test_done_merges_is_every_tasks_done_merges_on_one_target(dsn):
    url = f"/toy/{ledger.new_id()}.git"
    a, bb, c = (
        tasks.Brief(instruction="toy", origin_url=url, target_branch=t) for t in ("main", "main", "other")
    )

    async def go():
        async with await db.connect(dsn) as conn:
            for t in (a, bb, c):
                await start(conn, t)
            ea = await merge(conn, a, "a" * 40, landed=recorded(["a.py"]))
            await merge(conn, bb, "b" * 40, kind="failed", landed=recorded(["b.py"]))
            eb = await merge(conn, bb, "b" * 40, landed=recorded(["b.py"]))
            await merge(conn, c, "c" * 40, landed=recorded(["c.py"]))
            return ea, eb, await outcomes.done_merges(conn, url, "main")

    ea, eb, done = run(go())
    assert [(d["task_id"], d["effect_id"]) for d in done] == [(a.id, ea), (bb.id, eb)]
    assert done[0]["landed"]["paths"] == ["a.py"] and done[0]["head_sha"] == "a" * 40
    assert done[0]["url"] == url and done[0]["target_branch"] == "main" and done[0]["merged_at"]
    assert done[0]["event_id"] < done[1]["event_id"]
