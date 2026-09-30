"""The adapter against the real runtime. Plan 06, tasks 3, 7, and 8.

Every test here boots a VM, so each is slow by a second or so and all of them
skip where apple/container is not running. Other sessions boot containers on
the same network; names carry a random suffix and `running()` filters by
prefix, so a stranger is noise and never a failure.
"""

import asyncio
import subprocess
import threading
from pathlib import Path

import pytest

from adapters.apple_container import AppleContainer
from infra.sandbox import mounts
from schemas.sandbox import SandboxProfile
from schemas.space import Secret, Space
from tests.conftest import requires_container

pytestmark = requires_container


@pytest.fixture
def root(tmp_path) -> Path:
    """A sandbox root of this test's own, so nothing touches ~/.cori."""
    return tmp_path


@pytest.fixture
async def boxes(root):
    """Boots containers and destroys every one of them afterwards."""
    c = AppleContainer(sandbox_root=root, read_secret=lambda item: f"value-of-{item}")
    made = []

    async def boot(profile: SandboxProfile):
        h = await c.create(profile)
        made.append(h)
        return h

    c.boot = boot
    yield c
    for h in reversed(made):
        await c._cli("rm", "-f", h.id, timeout=60.0)


def worktree_profile(root: Path, *, key="o1", env=None) -> SandboxProfile:
    mount = root / "s1" / "worktrees" / key
    mount.mkdir(parents=True, exist_ok=True)
    return SandboxProfile(
        name="worktree",
        space="s1",
        mount_source=str(mount),
        readonly=False,
        network="hostonly",
        key=key,
        env=env or {},
    )


async def test_create_exec_read_write_round_trip(boxes, root):
    profile = worktree_profile(root)
    h = await boxes.boot(profile)
    assert h.id.startswith("cori-worktree-o1-")
    assert h.profile == profile

    await boxes.write(h, "/work/in.txt", b"written from the host\n")
    r = await boxes.exec(h, "tr a-z A-Z < in.txt > out.txt", timeout=30)
    assert r.exit_status == 0, r.stderr
    assert r.timed_out is False
    assert await boxes.read(h, "/work/out.txt") == b"WRITTEN FROM THE HOST\n"

    # The working directory is the mount, and the file is on the host disk.
    cwd = await boxes.exec(h, "pwd", timeout=30)
    assert cwd.stdout.strip() == "/work"
    assert (Path(profile.mount_source) / "out.txt").read_bytes() == (
        b"WRITTEN FROM THE HOST\n"
    )

    # A path outside the mount is read through an exec, not from the host.
    assert (await boxes.read(h, "/etc/hostname")).strip() == h.id.encode()


async def test_host_written_file_is_writable_inside(boxes, root):
    """virtiofs maps the person's uid to the sandbox's agent both ways, or the
    Executor cannot append to a file the kernel placed (plan 06, Risks)."""
    profile = worktree_profile(root, key="o2")
    h = await boxes.boot(profile)
    await boxes.write(h, "/work/notes.md", b"first line\n")
    r = await boxes.exec(h, "echo 'second line' >> notes.md", timeout=30)
    assert r.exit_status == 0, r.stderr
    assert await boxes.read(h, "/work/notes.md") == b"first line\nsecond line\n"


async def test_exec_timeout_kills_inside(boxes, root):
    """The timeout is armed in the guest, so nothing is left running in the
    sandbox after the tool returns."""
    h = await boxes.boot(worktree_profile(root, key="o3"))
    r = await boxes.exec(h, "sleep 30", timeout=1)
    assert r.timed_out is True
    assert r.exit_status == 137
    assert r.duration_ms < 2000, r.duration_ms

    ps = await boxes.exec(h, "ps -eo args | grep -c '[s]leep 30' || true", timeout=30)
    assert ps.stdout.strip() == "0", ps.stdout

    # An early SIGKILL that is not the deadline is not a timeout.
    early = await boxes.exec(h, "kill -9 $$", timeout=30)
    assert early.exit_status == 137
    assert early.timed_out is False


async def test_exec_host_backstop_when_the_cli_hangs(boxes, root, monkeypatch):
    """If the CLI itself never returns, the host side gives up ten seconds
    after the guest deadline and says so rather than hanging the kernel."""
    h = await boxes.boot(worktree_profile(root, key="o4"))

    async def hang(*args, timeout=None):
        from infra.sandbox.proc import Completed

        await asyncio.sleep(0.05)
        return Completed(-1, b"", b"", int((timeout or 0) * 1000), True)

    monkeypatch.setattr(boxes, "_cli", hang)
    r = await boxes.exec(h, "true", timeout=1)
    assert r.timed_out is True
    assert r.exit_status == -1


# ---- task 7: the three profiles against the runtime ----------------------


@pytest.fixture
def space(tmp_path, root, monkeypatch):
    """A space whose one root is a git repository on this machine, with the
    sandbox root pointed at this test's own directory."""
    monkeypatch.setenv("CORI_SANDBOX_ROOT", str(root))
    repo = tmp_path / "psyoptimal"
    repo.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "person@example.com"),
        ("config", "user.name", "The Person"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("the first commit\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "first"], cwd=repo, check=True, capture_output=True
    )
    return Space(
        id="s1",
        kind="client",
        roots=[str(repo)],
        max_effect_class="propose",
        secrets=[
            Secret(
                name="PGPASSWORD", keychain_name="psyoptimal_db", data_class="PROJECT"
            ),
            Secret(
                name="BANK_TOKEN", keychain_name="operator_bank", data_class="OPERATOR"
            ),
        ],
    )


async def test_three_profiles(boxes, space):
    """worktree writes to the host, scratch and verify refuse a write inside
    and through the adapter, and the verify VM is not the Executor's."""
    worktree = await mounts.profile_for("worktree", space, "o1", artifact_kind="code")
    executor = await boxes.boot(worktree)
    r = await boxes.exec(
        executor,
        "echo 'from the executor' > report.md && echo here > /tmp/executor-was-here",
        timeout=60,
    )
    assert r.exit_status == 0, r.stderr
    assert (Path(worktree.mount_source) / "report.md").read_text() == (
        "from the executor\n"
    )

    await boxes.stop(executor)
    ref = await boxes.snapshot(
        executor, snapshot_id="01990000-0000-7000-8000-0000000000b1"
    )

    verify = await mounts.profile_for(
        "verify", space, "checks", artifact_kind="code", snapshot=ref
    )
    verifier = await boxes.boot(verify)
    marker = await boxes.exec(verifier, "cat /tmp/executor-was-here", timeout=60)
    assert marker.exit_status != 0, "the verify VM is the Executor's"
    assert await boxes.read(verifier, "/work/report.md") == b"from the executor\n"
    refused = await boxes.exec(verifier, "echo x >> /work/report.md", timeout=60)
    assert refused.exit_status != 0, "a verify mount took a write"
    with pytest.raises(PermissionError, match="read-only"):
        await boxes.write(verifier, "/work/report.md", b"x")

    scratch = await mounts.profile_for("scratch", space, "b1", artifact_kind="code")
    reader = await boxes.boot(scratch)
    assert await boxes.read(reader, "/work/README.md") == b"the first commit\n"
    denied = await boxes.exec(reader, "echo x > /work/new.txt", timeout=60)
    assert denied.exit_status != 0, "a scratch mount took a write"
    with pytest.raises(PermissionError, match="read-only"):
        await boxes.write(reader, "/work/new.txt", b"x")
    assert not (Path(scratch.mount_source) / "new.txt").exists()


def serve_on_the_host() -> tuple[int, object]:
    """An HTTP server on the Mac, which is the only thing a host-only sandbox
    is meant to reach (tech stack §6: the network is the Mac and therefore the
    gateway)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1], server


async def test_hostonly_reaches_only_the_mac(boxes, space):
    port, server = serve_on_the_host()
    gateway = AppleContainer.gateway_ip()
    worktree = await mounts.profile_for("worktree", space, "o1", artifact_kind="code")
    scratch = await mounts.profile_for("scratch", space, "b1", artifact_kind="code")
    try:
        for profile in (worktree, scratch):
            h = await boxes.boot(profile)
            reach = await boxes.exec(
                h,
                'python3 -c "import urllib.request as u;'
                f"print(u.urlopen('http://{gateway}:{port}/', timeout=5).read())\"",
                timeout=60,
            )
            assert reach.exit_status == 0, reach.stderr
            assert "ok" in reach.stdout

            away = await boxes.exec(
                h,
                'python3 -c "import urllib.request as u;'
                "u.urlopen('http://1.1.1.1/', timeout=4)\"",
                timeout=60,
            )
            assert away.exit_status != 0, "a sandbox reached past the Mac"
    finally:
        server.shutdown()


async def test_secrets_arrive_in_environment_not_files(boxes, space):
    """Space secrets are injected as class-labeled values in the environment,
    never as files (tech stack §6). The value here is a stub, never a real
    Keychain item."""
    profile = await mounts.profile_for(
        "worktree", space, "o1", artifact_kind="code", max_data_class="OPERATOR"
    )
    assert profile.env["PGPASSWORD"] == "psyoptimal_db"
    h = await boxes.boot(profile)
    value = "value-of-psyoptimal_db"

    got = await boxes.exec(h, "printenv PGPASSWORD", timeout=60)
    assert got.stdout.strip() == value
    labelled = await boxes.exec(h, "printenv CORI_DATA_CLASS_PGPASSWORD", timeout=60)
    assert labelled.stdout.strip() == "PROJECT"
    operator = await boxes.exec(h, "printenv BANK_TOKEN", timeout=60)
    assert operator.stdout.strip() == "value-of-operator_bank"

    holding = await boxes.exec(
        h,
        "find / -name '*.env*' -type f 2>/dev/null | "
        f"xargs -r grep -l '{value}' 2>/dev/null; true",
        timeout=120,
    )
    assert holding.stdout.strip() == "", holding.stdout


# ---- task 8: what this system has up ------------------------------------


async def test_running_lists_only_cori_containers(boxes, space):
    """After a kernel restart, `running()` is the only record of what is up.
    Someone else's container on the same runtime is not this system's."""
    first = await mounts.profile_for("worktree", space, "o1", artifact_kind="code")
    second = await mounts.profile_for("scratch", space, "b1", artifact_kind="code")
    mine = {(await boxes.boot(first)).id, (await boxes.boot(second)).id}

    stranger = "s99-other"
    await boxes._cli("rm", "-f", stranger, timeout=60.0)
    await boxes._cli(
        "run", "-d", "--name", stranger, "--network", "cori-hostonly", first.image
    )
    try:
        up = await boxes.running()
        ids = {h.id for h in up}
        assert mine <= ids
        assert stranger not in ids
        assert all(h.id.startswith("cori-") for h in up)

        by_id = {h.id: h for h in up}
        for profile in (first, second):
            rebuilt = next(
                h for h in up if h.profile.key == profile.key and h.id in mine
            )
            assert rebuilt.profile == profile.model_copy(update={"env": {}})
            assert rebuilt.profile.env == {}
            assert rebuilt.created_at is not None

        # A rebuilt handle is a working handle: the kernel can stop and destroy
        # a sandbox it did not create in this process.
        rebuilt = by_id[next(iter(mine))]
        receipt = await boxes.stop(rebuilt)
        assert receipt.confirmed_dead_at >= receipt.killed_at
        await boxes.destroy(rebuilt)
        assert rebuilt.id not in {h.id for h in await boxes.running()}
    finally:
        await boxes._cli("rm", "-f", stranger, timeout=60.0)
