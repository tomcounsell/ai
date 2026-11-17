"""The copies of Chrome.app a signal-stopped Chrome leaves are removed by the
kernel's reclaim pass, never one a live process holds (task 7f96f2f1: 21
leaked copies, 16 GB, on the Cowboy, 2026-11-17)."""

import asyncio
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from core import db, serve, tasks, workspace

pytestmark = pytest.mark.spend(usd=0)


def copy(root, name, *, age_s=3600.0):
    d = root / name / "Google Chrome.app" / "Contents" / "MacOS"
    d.mkdir(parents=True)
    (d / "Google Chrome").write_bytes(b"x" * 1024)
    old = time.time() - age_s
    os.utime(root / name, (old, old))
    return root / name


@pytest.fixture
def holder():
    """A process holding a file open in a copy, as a live Chrome holds its
    executable."""
    procs = []

    def hold(path):
        p = subprocess.Popen(["/bin/sh", "-c", 'exec 3<"$0"; sleep 60', str(path)])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            out = subprocess.run(["/usr/sbin/lsof", "-Fn", "-p", str(p.pid)], capture_output=True, text=True)
            if os.path.realpath(path) in out.stdout:
                break
            time.sleep(0.05)
        procs.append(p)

    yield hold
    for p in procs:
        p.kill()
        p.wait()


def test_an_old_unheld_copy_goes_and_a_held_or_new_copy_stays(tmp_path, holder):
    old, held, new = copy(tmp_path, "c.old"), copy(tmp_path, "c.held"), copy(tmp_path, "c.new", age_s=10)
    holder(held / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome")
    assert workspace.chrome_clones(tmp_path) == ["c.old"]
    assert not old.exists() and held.exists() and new.exists()


def test_a_copy_held_through_a_linked_root_stays(tmp_path, holder):
    """`getconf` names `/var/folders/…`, lsof prints `/private/var/…`."""
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    held, old = copy(real, "c.held"), copy(real, "c.old")
    holder(held / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome")
    assert workspace.chrome_clones(tmp_path / "link") == ["c.old"]
    assert held.exists() and not old.exists()


def test_what_lsof_printed_is_held_whatever_its_exit(tmp_path, monkeypatch):
    held, old = copy(tmp_path, "c.held"), copy(tmp_path, "c.old")
    out = f"p1\nn{held}/Google Chrome.app/Contents/MacOS/Google Chrome\n"
    real_run = subprocess.run

    def run(argv, **kw):
        if argv[0] == "/usr/sbin/lsof":
            return SimpleNamespace(returncode=1, stdout=out, stderr="lsof: WARNING: can't stat() x\n")
        return real_run(argv, **kw)

    monkeypatch.setattr(workspace.subprocess, "run", run)
    assert workspace.chrome_clones(tmp_path) == ["c.old"] and held.exists()
    out = ""
    with pytest.raises(RuntimeError):
        workspace.chrome_clones(tmp_path)


def test_a_missing_root_is_nothing_to_do(tmp_path):
    assert workspace.chrome_clones(tmp_path / "none") == []


def test_reclaim_sweeps_the_copies_and_a_failed_sweep_still_removes_workspaces(dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(serve, "RECLAIM_AFTER_S", 0.0)
    swept = []
    monkeypatch.setattr(workspace, "chrome_clones", lambda: swept.append(1) or ["c"])

    async def finished(conn, name):
        (tmp_path / name / "kernel.git").mkdir(parents=True)
        task = await tasks.start(conn, tasks.Brief(instruction="x", mirror=str(tmp_path / name / "kernel.git")))
        await tasks.stop(conn, task, reason="done")

    async def go():
        async with await db.connect(dsn) as conn:
            await finished(conn, "a")
            await serve.Kernel(None, {}, None, dsn).reclaim(conn)
            assert swept and not (tmp_path / "a").exists()

            def fail():
                raise RuntimeError("lsof")

            monkeypatch.setattr(workspace, "chrome_clones", fail)
            await finished(conn, "b")
            await serve.Kernel(None, {}, None, dsn).reclaim(conn)  # does not raise
            assert not (tmp_path / "b").exists()

    asyncio.run(go())
