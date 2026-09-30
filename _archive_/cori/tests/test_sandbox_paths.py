"""Task 3: host-side access to the mount is the one place the adapter can
reach past the VM, so containment is checked on the resolved path. These tests
need no container runtime. Plan 06, tasks 3 and the Properties section."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adapters.apple_container import AppleContainer
from schemas.sandbox import SandboxHandle, SandboxProfile


def handle(mount: Path, *, name="worktree", readonly=False) -> SandboxHandle:
    return SandboxHandle(
        id=f"cori-{name}-key-abc123",
        profile=SandboxProfile(
            name=name,
            space="s1",
            mount_source=str(mount),
            readonly=readonly,
            network="hostonly",
            key="key",
            env={},
        ),
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def mount(tmp_path) -> Path:
    m = tmp_path / "mount"
    (m / "sub").mkdir(parents=True)
    (m / "a.txt").write_text("inside\n")
    (m / "sub" / "b.txt").write_text("also inside\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("the person's own file\n")
    (m / "escape").symlink_to(outside)
    (m / "escape-file").symlink_to(outside / "secret.txt")
    (m / "inside-link").symlink_to(m / "a.txt")
    return m


async def test_write_readonly_profile_refused_before_host_touch(mount):
    # The host side of a read-only bind is writable, so the adapter enforces
    # what the mount flag enforces inside.
    h = handle(mount, name="scratch", readonly=True)
    c = AppleContainer(sandbox_root=mount.parent)
    before = {p: p.stat().st_mtime_ns for p in mount.rglob("*") if p.is_file()}
    with pytest.raises(PermissionError, match="read-only"):
        await c.write(h, "/work/a.txt", b"overwritten")
    assert {p: p.stat().st_mtime_ns for p in mount.rglob("*") if p.is_file()} == before
    assert (mount / "a.txt").read_text() == "inside\n"


async def test_write_outside_mount_refused(mount, tmp_path):
    h = handle(mount)
    c = AppleContainer(sandbox_root=mount.parent)
    for path in ("/etc/passwd", "/work/../outside/secret.txt", "/tmp/x"):
        with pytest.raises(PermissionError, match="outside the mount"):
            await c.write(h, path, b"x")
    assert (tmp_path / "outside" / "secret.txt").read_text().startswith("the person")


async def test_read_outside_mount_uses_exec(mount, monkeypatch):
    h = handle(mount)
    c = AppleContainer(sandbox_root=mount.parent)
    seen = []

    async def fake_cli(*args, timeout=None):
        seen.append(args)

        class R:
            returncode = 0
            stdout = b"root:x:0:0\n"
            stderr = b""

        return R()

    monkeypatch.setattr(c, "_cli", fake_cli)
    assert await c.read(h, "/etc/passwd") == b"root:x:0:0\n"
    assert seen == [("exec", h.id, "cat", "/etc/passwd")]

    # A path under the mount never reaches the CLI: it is a host-side read.
    seen.clear()
    assert await c.read(h, "/work/a.txt") == b"inside\n"
    assert seen == []


async def test_symlink_escape_refused(mount):
    h = handle(mount)
    c = AppleContainer(sandbox_root=mount.parent)
    for path in ("/work/escape-file", "/work/escape/secret.txt"):
        assert c.host_path(h, path) is None
        with pytest.raises(PermissionError, match="resolves outside the mount"):
            await c.read(h, path)
        with pytest.raises(PermissionError):
            await c.write(h, path, b"x")
    # A link that stays inside is served from the host side.
    assert await c.read(h, "/work/inside-link") == b"inside\n"


SEGMENTS = st.sampled_from(
    [
        "a.txt",
        "sub",
        "b.txt",
        "escape",
        "escape-file",
        "inside-link",
        ".",
        "..",
        "",
        "/",
    ]
)


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    segments=st.lists(SEGMENTS, max_size=6),
    absolute=st.booleans(),
    prefix=st.sampled_from(["/work", "", "/etc", "/work/sub"]),
)
def test_host_path_never_escapes_mount(mount, segments, absolute, prefix):
    """For every path a sandbox can name, host_path returns None or a path
    whose resolve() is under the mount's resolve(); and a write on a read-only
    profile changes no file's mtime anywhere under the mount."""
    h = handle(mount)
    c = AppleContainer(sandbox_root=mount.parent)
    path = "/".join([prefix, *segments]) if not absolute else "/" + "/".join(segments)

    host = c.host_path(h, path)
    if host is not None:
        base = mount.resolve()
        resolved = host.resolve()
        assert resolved == base or base in resolved.parents, (path, host)

    ro = handle(mount, name="verify", readonly=True)
    before = {p: p.stat().st_mtime_ns for p in mount.rglob("*") if p.is_file()}
    with pytest.raises(PermissionError):
        import asyncio

        asyncio.run(c.write(ro, path, b"x"))
    assert {p: p.stat().st_mtime_ns for p in mount.rglob("*") if p.is_file()} == before


def test_mount_source_outside_the_sandbox_root_is_refused(tmp_path):
    from adapters.apple_container import SandboxError

    root = tmp_path / "sandboxes"
    root.mkdir()
    stray = tmp_path / "elsewhere"
    stray.mkdir()
    c = AppleContainer(sandbox_root=root)

    worktree = SandboxProfile(
        name="worktree",
        space="s1",
        mount_source=str(stray),
        readonly=False,
        network="hostonly",
        key="o1",
        env={},
    )
    with pytest.raises(SandboxError, match="outside the sandbox root"):
        c._check_mount_source(worktree)

    # scratch binds a root of the space itself, which lives wherever the
    # person keeps it.
    scratch = worktree.model_copy(update={"name": "scratch", "readonly": True})
    c._check_mount_source(scratch)


def test_env_lines_read_the_keychain_and_pass_class_labels_through():
    c = AppleContainer(read_secret=lambda item: f"value-of-{item}")
    profile = SandboxProfile(
        name="worktree",
        space="s1",
        mount_source=str(Path(os.getcwd())),
        readonly=False,
        network="hostonly",
        key="o1",
        env={"PGPASSWORD": "psyoptimal_db", "CORI_DATA_CLASS_PGPASSWORD": "PROJECT"},
    )
    assert c._env_lines(profile) == [
        "CORI_DATA_CLASS_PGPASSWORD=PROJECT",
        "PGPASSWORD=value-of-psyoptimal_db",
    ]
