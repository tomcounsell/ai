"""Tasks 4 and 5: a stop kills the compute and keeps the disk, and the
snapshot is of a quiet mount hashed on the host. Spike 07's requirement is
that the CLI's exit is never the confirmation. Needs the real runtime."""

import asyncio
import hashlib
import time
from pathlib import Path

import pytest

from adapters.apple_container import AppleContainer
from schemas.sandbox import SandboxProfile
from tests.conftest import requires_container

pytestmark = requires_container

COUNTER = (
    "nohup setsid sh -c 'i=0; while true; do i=$((i+1)); "
    "echo $i >> /work/counter.txt; sleep 1; done' >/dev/null 2>&1 &"
)


@pytest.fixture
async def box(tmp_path):
    c = AppleContainer(sandbox_root=tmp_path)
    made = []

    async def boot(key="o1"):
        mount = tmp_path / "s1" / "worktrees" / key
        mount.mkdir(parents=True, exist_ok=True)
        h = await c.create(
            SandboxProfile(
                name="worktree",
                space="s1",
                mount_source=str(mount),
                readonly=False,
                network="hostonly",
                key=key,
                env={},
            )
        )
        made.append(h)
        return h

    c.boot = boot
    yield c
    for h in reversed(made):
        await c._cli("rm", "-f", h.id, timeout=60.0)


def counter_lines(h) -> int:
    p = Path(h.profile.mount_source) / "counter.txt"
    return len(p.read_text().splitlines()) if p.exists() else 0


async def test_stop_confirms_by_probe_not_exit(box):
    h = await box.boot()
    await box.exec(h, COUNTER, timeout=10)
    await asyncio.sleep(2.5)
    assert counter_lines(h) >= 2, "the workload never started"

    receipt = await box.stop(h)

    assert receipt.handle_id == h.id
    assert receipt.confirmed_dead_at >= receipt.killed_at
    assert "inspect:" in receipt.probe and "exec:" in receipt.probe

    # Execution is dead: the file stops growing and its mtime stops moving.
    path = Path(h.profile.mount_source) / "counter.txt"
    lines, mtime = counter_lines(h), path.stat().st_mtime_ns
    await asyncio.sleep(3)
    assert counter_lines(h) == lines
    assert path.stat().st_mtime_ns == mtime


async def test_disk_survives_stop_and_destroy(box):
    h = await box.boot(key="o2")
    await box.write(h, "/work/report.md", b"the objective's artifact\n")
    await box.stop(h)
    await box.destroy(h)
    # A worktree mount outlives its containers (seams §2.2).
    assert (Path(h.profile.mount_source) / "report.md").read_bytes() == (
        b"the objective's artifact\n"
    )


async def test_stop_is_idempotent(box):
    h = await box.boot(key="o3")
    first = await box.stop(h)
    t0 = time.perf_counter()
    second = await box.stop(h)
    assert time.perf_counter() - t0 < 5, "a second stop waited on a live probe"
    assert second.confirmed_dead_at >= second.killed_at >= first.killed_at


async def test_stop_retries_after_cli_error(box, monkeypatch):
    """A nonzero kill is a runtime error, not a live sandbox: read the state
    and kill again (spike 07, surprise 3)."""
    h = await box.boot(key="o4")
    real = box._cli
    kills = []

    async def flaky(*args, timeout=None):
        if args[:1] == ("kill",):
            kills.append(args)
            if len(kills) == 1:
                from infra.sandbox.proc import Completed

                return Completed(1, b"", b"the runtime is busy", 5, False)
        return await real(*args, timeout=timeout)

    monkeypatch.setattr(box, "_cli", flaky)
    receipt = await box.stop(h)
    assert len(kills) >= 2, "the failed kill was never retried"
    assert receipt.confirmed_dead_at >= receipt.killed_at
    assert await box._state(h.id) != "running"


async def test_stop_unconfirmed_when_the_probe_never_agrees(box, monkeypatch):
    """The kernel issues no replacement generation on this (tech stack §4)."""
    from adapters.apple_container import StopUnconfirmed

    h = await box.boot(key="o5")
    monkeypatch.setattr("adapters.apple_container.PROBE_WINDOW_S", 0.2)

    async def never_dies(name):
        return "running"

    monkeypatch.setattr(box, "_state", never_dies)
    with pytest.raises(StopUnconfirmed, match="confirmed it dead"):
        await box.stop(h)


async def test_snapshot_after_stop_hashes_match(box):
    h = await box.boot(key="o6")
    mount = Path(h.profile.mount_source)
    await box.write(h, "/work/report.md", b"the objective's artifact\n")
    await box.write(h, "/work/src/main.py", b"print('hello')\n")
    await box.exec(h, "ln -s /etc/passwd /work/escape-link", timeout=30)

    # The test's own cross-check: the guest agrees with the host on one file
    # before the stop. The adapter itself never runs sha256sum.
    inside = await box.exec(h, "sha256sum report.md", timeout=30)
    guest_hash = inside.stdout.split()[0]

    await box.stop(h)
    snapshot_id = "01990000-0000-7000-8000-00000000006a"
    ref = await box.snapshot(h, snapshot_id=snapshot_id)

    assert ref.id == snapshot_id
    assert ref.handle_id == h.id
    archive = Path(ref.path)
    assert archive == box.sandbox_root / "s1" / "snapshots" / f"{snapshot_id}.tar.gz"
    assert archive.exists()
    assert ref.sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()

    for path in mount.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        rel = str(path.relative_to(mount))
        assert ref.files[rel] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert ref.files["report.md"] == guest_hash
    assert "escape-link" not in ref.files

    # The link is stored as a link and the file it points at is not in the
    # archive.
    import tarfile

    with tarfile.open(archive) as tar:
        names = tar.getnames()
        link = tar.getmember("escape-link")
        assert link.issym() and link.linkname == "/etc/passwd"
        assert link.size == 0
    assert sorted(n for n in names if n != ".") == [
        "escape-link",
        "report.md",
        "src",
        "src/main.py",
    ]


async def test_snapshot_refuses_a_running_container(box):
    from adapters.apple_container import SandboxError

    h = await box.boot(key="o7")
    await box.write(h, "/work/a.txt", b"x\n")
    with pytest.raises(SandboxError, match="still running"):
        await box.snapshot(h, snapshot_id="01990000-0000-7000-8000-00000000007a")
