"""The verification VM (`core/container.py`) on Apple's real `container`, real
git, and a real Postgres ledger.

The reading of results, the reuse keys, and the lock run anywhere. Every
test marked `container` runs real VMs and builds and skips where the runtime
is not installed; the first builds the base image when its tag is missing.
The toy suite runs with the Command Line Tools' Python on the host and the
base image's `python3` in the VM.

Live spend: none.
"""

import asyncio
import dataclasses
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg.conninfo import make_conninfo

from core import binaries, checks, container, db, fresh, ledger, machine, tasks
from core import workspace as kws
from core.settings import settings
from tests import test_checks, test_review

pytestmark = [pytest.mark.spend(usd=0)]

VM_SUITE = test_checks.VM_SUITE
GATEWAY = "192.168.64.1"  # the default network's gateway (`container network list`)


def memory(monkeypatch, mb: int) -> None:
    monkeypatch.setattr(container, "settings", dataclasses.replace(settings, verify_memory_mb=mb))


def run(coro):
    return asyncio.run(coro)


def never() -> asyncio.Task:
    return asyncio.ensure_future(asyncio.Event().wait())


def unstopped(fn, *args):
    """A CLI step that races a stop, run with none coming."""

    async def go():
        return await fn(*args, never())

    return run(go())


def lan_address() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent: the route picks the address
        return s.getsockname()[0]
    finally:
        s.close()


def names() -> list[str]:
    return [c["id"] for c in container.containers()]


def builder_up() -> bool:
    """Whether a builder is running: `builder status` exits 0 either way, so
    its JSON is read."""
    code, out = container.call("builder", "status", "--format", "json")
    if code != 0 or not out.strip().startswith("["):
        return False
    return any(b.get("status", {}).get("state") == "running" for b in json.loads(out))


async def at_candidate(dsn, tmp_path, *, writes, files=None, **spec):
    """A task in checks with a candidate (`test_review.at_review`), its
    Brief, rows, and candidate."""
    task, _b, _ws = await test_review.at_review(
        dsn, tmp_path, writes=writes, files=files, suite=spec.pop("suite", VM_SUITE), **spec
    )
    async with await db.connect(dsn) as conn:
        b = await tasks.brief(conn, task)
        rows = await ledger.read(conn, task)
    return task, b, rows, machine.fold(rows).candidate.sha


async def verified(dsn, task, b, rows, candidate, stop=None):
    """`container.verify` as the review runner calls it, the payload
    appended as `verify.ran`."""

    async def record(payload):
        async with await db.connect(dsn) as conn, conn.transaction():
            return {"verify": payload, "id": await ledger.append(conn, task, fresh.VERIFY, payload)}

    lay = kws.Layout(Path(b.mirror).parent)
    ctx = SimpleNamespace(task_id=task, dsn=dsn)
    return await container.verify(ctx, lay, b, candidate, rows, stop or never(), record)


def detached(name: str, label: str) -> None:
    """A labelled VM left running, as a killed kernel leaves one."""
    code, out = container.call(
        "run", "-d", "--name", name, "-l", f"valor.db={label}", "-l", "valor.task=t",
        "--network", "none", "--entrypoint", "/bin/sleep", container.base_tag(), "infinity",
    )  # fmt: skip
    assert code == 0, out


async def base_image() -> str:
    """The base image's tag, built under the lock when missing; the system
    left running."""
    stop = never()
    await container.start(stop)
    async with container.machine_lock(stop):
        tag, _ = await container.ensure_base(kws.Layout(Path("/nonexistent")), stop, "tests")
    return tag


# -- reading the result ---------------------------------------------------------------------


def _out(tmp_path: Path, result: dict | None, junit: str | None = None, lint: str | None = None):
    lay = kws.Layout(tmp_path / "task")
    out = lay.checks / "vm-head-abc" / "out"
    out.mkdir(parents=True)
    if result is not None:
        (out / "result.json").write_text(json.dumps(result))
    if junit is not None:
        (out / "junit.xml").write_text(junit)
    if lint is not None:
        (out / "lint.out").write_text(lint)
    return lay


JUNIT = (
    '<testsuite><testcase classname="tests.test_a" name="test_one"/>'
    '<testcase classname="tests.test_a" name="test_mac"><skipped message="macOS only: needs macOS itself"/>'
    '</testcase><testcase classname="tests.test_a" name="test_other"><skipped message="later"/></testcase>'
    "</testsuite>"
)
OK = {"setup": [], "suite": {"exit": 0, "duration_s": 1.0}, "lint": None, "peak_mb": 100, "oom_kill": 0}


def test_a_run_past_its_memory_is_a_memory_cause_not_a_failure(tmp_path, monkeypatch):
    memory(monkeypatch, 512)
    lay = _out(tmp_path, {**OK, "suite": {"exit": 137}, "oom_kill": 1}, JUNIT)
    got = container.read_result(lay, "vm-head-abc", 0, "head", {})
    assert got["cause"] == "memory" and "512 MB" in got["why"]
    lay = _out(tmp_path / "2", {**OK, "suite": {"exit": 1}, "peak_mb": 512}, JUNIT)
    assert container.read_result(lay, "vm-head-abc", 0, "head", {})["cause"] == "memory"


def test_a_clean_exit_at_the_memory_peak_is_no_memory_cause(tmp_path, monkeypatch):
    memory(monkeypatch, 512)
    lay = _out(tmp_path, {**OK, "peak_mb": 512}, JUNIT)
    got = container.read_result(lay, "vm-head-abc", 0, "head", {})
    assert "cause" not in got and got["exit"] == 0 and got["peak_mb"] == 512


def test_no_result_or_failed_services_are_the_kernels_and_failed_setup_the_commits(tmp_path):
    assert (
        container.read_result(_out(tmp_path / "a", None), "vm-head-abc", 1, "head", {})["cause"] == "kernel"
    )
    bad = _out(tmp_path / "b", {"services": "failed", "why": "initdb failed"})
    assert container.read_result(bad, "vm-head-abc", 0, "head", {})["cause"] == "kernel"
    setup = _out(tmp_path / "c", {**OK, "setup": [{"command": "make", "exit": 2, "duration_s": 0.1}]})
    got = container.read_result(setup, "vm-head-abc", 0, "head", {})
    assert got["cause"] == "commit" and got["exit"] == 2 and "make exited 2" in got["why"]
    crashed = _out(tmp_path / "d", {**OK, "suite": {"exit": 139}})
    assert container.read_result(crashed, "vm-head-abc", 0, "head", {})["cause"] == "commit"


def test_each_setup_commands_output_is_kept_as_on_the_host(tmp_path):
    """The VM's `out/setup-N.out` gives each setup entry its file's name and
    its last 1500 characters, and a failed setup's record its tail."""
    ran = [
        {"command": "make deps", "exit": 0, "duration_s": 0.1},
        {"command": "make", "exit": 2, "duration_s": 0.1},
    ]
    lay = _out(tmp_path, {**OK, "setup": ran})
    out = lay.checks / "vm-head-abc" / "out"
    (out / "setup-0.out").write_text("fetched\n")
    (out / "setup-1.out").write_text("x" * 5000 + "error: no network\n")
    got = container.read_result(lay, "vm-head-abc", 0, "head", {})
    first, failed = got["setup"]
    assert first["tail"] == "fetched\n" and first["output"] == "vm-head-abc/out/setup-0.out"
    assert failed["output"] == "vm-head-abc/out/setup-1.out" and len(failed["tail"]) == 1500
    assert got["tail"] == failed["tail"] and failed["tail"].endswith("error: no network\n")


def test_tests_lint_and_the_macos_skips_are_read_from_the_output(tmp_path):
    lay = _out(tmp_path, {**OK, "lint": {"exit": 1, "duration_s": 0.1}}, JUNIT, "a.py:3:1: E501 too long\n")
    got = container.read_result(lay, "vm-head-abc", 0, "head", {"kind": "python-uv", "lint": "ruff check ."})
    assert "cause" not in got
    assert got["tests"]["passed"] == ["tests.test_a::test_one"] and got["macos_skipped"] == 1
    assert got["lint"]["exit"] == 1 and got["lint"]["locations"] == [
        {"path": "a.py", "line": 3, "rule": "E501"}
    ]
    assert got["peak_mb"] == 100


def test_the_vm_spec_names_the_vms_paths_and_runs_offline():
    project = {"name": "toy", "kind": "plain", "services": ["postgres"], "setup": ["uv sync --frozen"],
               "suite": "pytest --junitxml={junit}", "env": {"MINE": "1"}}  # fmt: skip
    spec = container.spec_json(project)
    assert spec["suite"] == f"pytest --junitxml={container.VM_JUNIT}"
    env = spec["env"]
    assert env["PATH"] == container.VM_PATH and env["UV_OFFLINE"] == "1" and env["MINE"] == "1"
    assert env["PGPORT"] == "5432" and "{app_password}" in env["DATABASE_URL"]
    assert env["PGPASSFILE"] == str(container.VM_PASSFILE)
    assert container.installs(project) and not container.installs({"setup": ["make"]})


# -- reuse -----------------------------------------------------------------------------------


def _ran(**p):
    return {
        "type": fresh.VERIFY,
        "id": 1,
        "payload": {"where": "vm", "memory_mb": settings.verify_memory_mb, **p},
    }


def test_raising_the_memory_reruns_a_memory_cause_and_a_kernel_cause_is_never_reused(monkeypatch):
    rows = [_ran(candidate="c", digest="k", cause="memory")]
    assert container.reusable(rows, "c", "k") is not None
    memory(monkeypatch, settings.verify_memory_mb * 2)
    assert container.reusable(rows, "c", "k") is None
    monkeypatch.undo()
    assert container.reusable([_ran(candidate="c", digest="k", cause="kernel")], "c", "k") is None
    assert container.reusable([_ran(candidate="c", digest="k", where="host")], "c", "k") is None
    assert container.reusable(rows, "c", "other") is None


def test_the_base_run_is_reused_across_two_candidates_of_a_task():
    base_run = {"tests": {"pass": ["t::a"]}, "cause": None}
    rows = [_ran(candidate="c1", base="b", base_digest="bk", base_run=base_run)]
    assert container.reusable_base(rows, "b", "bk") == base_run
    assert container.reusable_base(rows, "b", "other") is None
    assert (
        container.reusable_base([_ran(base="b", base_digest="bk", base_run={"cause": "kernel"})], "b", "bk")
        is None
    )


def test_the_dependency_key_covers_the_containerfile_that_builds_it(tmp_path, monkeypatch):
    project = {"kind": "python-uv", "setup": ["uv sync --frozen"]}
    key = container.deps_key("valor/base:x", project, "listing")
    edited = tmp_path / "Containerfile"
    edited.write_bytes(container.DEPS_FILE.read_bytes() + b"RUN true\n")
    monkeypatch.setattr(container, "DEPS_FILE", edited)
    assert container.deps_key("valor/base:x", project, "listing") != key


def test_the_dependency_key_covers_the_specs_environment():
    project = {"kind": "python-uv", "setup": ["uv sync --frozen"]}
    key = container.deps_key("valor/base:x", project, "listing")
    assert container.deps_key("valor/base:x", {**project, "env": {"UV_PYTHON": "3.12"}}, "listing") != key


# -- a stop ends a hung CLI call ---------------------------------------------------------------


def test_a_stop_ends_a_hung_short_cli_call(tmp_path, monkeypatch):
    """A CLI call that never returns ends on a stop with `_Stopped`, its
    process gone: `system start`, `image inspect` (through `checked`),
    `kill` and `delete`, `builder delete` before and after a build, an
    `image delete` while pruning, and `system stop`. The removal, builder
    delete or system stop a stop runs is not raced. The stop fires once the
    hung call has written its line."""
    pids, hang, fifo = tmp_path / "pids", tmp_path / "hang", tmp_path / "hung"
    pids.touch()
    os.mkfifo(fifo)
    heard_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)  # a reader, so the CLI's write never blocks
    cli = tmp_path / "container"
    cli.write_text(
        f'#!/bin/sh\necho "$$ $*" >> {pids}\n'
        f'if [ "$(($(wc -l < {pids})))" = "$(cat {hang})" ]; then echo >> {fifo}; exec sleep 600; fi\nexit 0\n'
    )
    cli.chmod(0o755)
    monkeypatch.setattr(container, "require", lambda: str(cli))
    monkeypatch.setattr(container, "IMAGES", tmp_path / "images.json")
    monkeypatch.setattr(container, "BUILDER_OWNER", tmp_path / "builder-owner")
    container._record("valor/held:test", "sha256:x", "tests")

    def lines() -> list[list[str]]:
        return [line.split(" ", 1) for line in pids.read_text().splitlines()]

    def stopped(step, at: int = 1) -> list[str]:
        """`step` with the CLI hanging at its `at`th call and the stop set
        when that call has started; the calls the step made."""
        before = len(lines())
        hang.write_text(str(before + at))

        async def go():
            loop = asyncio.get_running_loop()
            stop = loop.create_future()

            def heard():
                os.read(heard_fd, 64)
                loop.remove_reader(heard_fd)
                if not stop.done():
                    stop.set_result(None)

            loop.add_reader(heard_fd, heard)
            try:
                with pytest.raises(checks._Stopped):
                    await step(asyncio.ensure_future(stop))
            finally:
                loop.remove_reader(heard_fd)

        run(go())
        return [args for _, args in lines()[before:]]

    def gone(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    def build(stop):
        return container.build("valor/x:1", tmp_path, tmp_path / "build.out", stop, "tests")

    try:
        assert stopped(container.start) == ["system start --disable-kernel-install"]
        assert stopped(lambda stop: container.checked("valor/held:test", stop)) == [
            "image inspect valor/held:test"
        ]
        name = "valor-verify-t-head"
        assert stopped(lambda stop: container.removed(name, stop)) == [
            f"kill {name}",
            f"kill {name}",
            f"delete --force {name}",
        ]
        assert stopped(build) == ["builder delete --force", "builder delete --force"]
        assert not container.BUILDER_OWNER.exists()
        assert stopped(build, at=3) == [
            "builder delete --force",
            f"build --no-cache -t valor/x:1 -l valor.db=tests {tmp_path}",
            "builder delete --force",
            "builder delete --force",
        ]
        assert not container.BUILDER_OWNER.exists()
        assert stopped(lambda stop: container.prune({}, set(), "tests", stop)) == [
            "image delete valor/held:test"
        ]
        assert "valor/held:test" in container._images()  # the delete did not finish
        assert stopped(container.system_stopped) == ["system stop", "system stop"]
        assert all(gone(int(pid)) for pid, _ in lines())
    finally:
        os.close(heard_fd)


# -- the lock --------------------------------------------------------------------------------


def test_a_stop_while_waiting_on_the_lock_returns_without_taking_it():
    held = container.try_lock()
    assert held is not None

    async def go():
        stop = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_later(0.2, stop.set_result, None)
        with pytest.raises(checks._Stopped):
            async with container.machine_lock(asyncio.ensure_future(stop)):
                raise AssertionError("the lock was taken")

    try:
        run(go())
        assert container.try_lock() is None  # still the holder's alone
    finally:
        os.close(held)


def test_a_helper_writable_by_its_group_is_refused(tmp_path, monkeypatch):
    """A copy of a helper, every entry above it root's, the copy itself
    group-writable: refused, as is the same copy left as the user's."""
    helper = Path(binaries.CONTAINER_HELPERS[-1])
    copy = tmp_path / helper.relative_to("/")
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"#!/bin/sh\n")
    copy.chmod(0o775)
    with pytest.raises(binaries.Untrusted, match="not owned by root"):
        binaries.require(copy)
    real = os.lstat

    def as_root(p):
        st = real(p)
        return os.stat_result((st.st_mode & ~0o022 if Path(p) != copy else st.st_mode, *st[1:4], 0, *st[5:]))

    monkeypatch.setattr(os, "lstat", as_root)
    with pytest.raises(binaries.Untrusted, match="writable by its group"):
        binaries.require(copy)
    copy.chmod(0o755)
    assert binaries.require(copy) == str(copy)


@pytest.mark.container
def test_the_installed_runtime_passes_and_its_facts_hold():
    container.require()
    code, out = container.call("--version")
    assert code == 0 and container.RELEASE in out


# -- runs in real VMs ------------------------------------------------------------------------


PROBES = """\
import os, socket, subprocess


def _refused(host, port):
    try:
        socket.create_connection((host, port)).close()
    except OSError:
        return True
    return False


def test_no_network():
    for host, port in {targets!r}:
        assert _refused(host, port), (host, port)


def test_the_kernels_files_are_out_of_reach():
    reads = [lambda: open("/valor/src/spec.json").read(), lambda: os.listdir("/valor/out")]
    writes = [lambda: open("/valor/out/result.json", "w"), lambda: open("/valor/src/planted", "w")]
    for attempt in reads + writes:
        try:
            attempt()
        except PermissionError:
            continue
        raise AssertionError(attempt)


def test_no_sleep_outlives_setup():
    assert subprocess.run(["pgrep", "-x", "sleep"]).returncode == 1


def test_reads_host_files():
    for path in {host_files!r}:
        open(path).read()
"""


@pytest.mark.container
def test_a_verification_runs_both_commits_in_vms_and_leaves_the_system_stopped(dsn, tmp_path):
    """Base and head in fresh VMs with no network, the kernel's files out of
    the candidate's reach, a setup child killed after its command, host files
    absent, the numbers on `verify.ran`, and the system stopped after."""
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    lan = lan_address()
    targets = [("1.1.1.1", 443), ("127.0.0.1", port), (lan, port), (GATEWAY, port), (GATEWAY, settings.pgport),
               (lan, settings.pgport)]  # fmt: skip
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    planted = tmp_path / "work" / "host-file"
    planted.parent.mkdir(parents=True)
    planted.write_text("on the host\n")
    host_files = [str(planted), *([str(known_hosts)] if known_hosts.exists() else [])]
    for path in host_files:  # the suite's reads pass on the host
        Path(path).read_bytes()
    writes = {
        "tests/test_probe.py": PROBES.format(targets=targets, host_files=host_files),
        "tests/test_b.py": "def test_kept():\n    assert False\n",
    }
    try:

        async def go():
            task, b, rows, candidate = await at_candidate(
                dsn, tmp_path, writes=writes, setup=["sh -c 'sleep 600 >/dev/null 2>&1 &'"]
            )
            return task, b, candidate, await verified(dsn, task, b, rows, candidate)

        _task, b, candidate, got = run(go())
    finally:
        listener.close()
    v = got["verify"]
    assert v["where"] == "vm" and v["cause"] is None and v["release"] == container.RELEASE, v
    assert sorted(v["failures"]) == ["tests.test_b::test_kept", "tests.test_probe::test_reads_host_files"]
    assert v["counts"]["passed"] == 8 and v["result_owner"] == "kernel"  # the toy's five, three probes
    assert (
        0 < v["peak_mb"] <= v["memory_mb"] == settings.verify_memory_mb and v["cpus"] == settings.verify_cpus
    )
    assert v["image_tag"] == v["base_image_tag"] == container.base_tag() and not v["manifests_differ"]
    assert v["setup"] == [{"command": "sh -c 'sleep 600 >/dev/null 2>&1 &'", "exit": 0}]
    assert v["base_run"]["tests"]["passed"] and v["base_run"]["cause"] is None
    lay = kws.Layout(Path(b.mirror).parent)
    assert sorted(p.name for p in (lay.checks / f"vm-head-{candidate[:12]}" / "src").iterdir()) == [
        "source.tar", "spec.json",
    ]  # fmt: skip
    assert not container.running()


@pytest.mark.container
def test_a_stop_during_the_run_leaves_no_vm_and_the_system_stopped(dsn, tmp_path):
    suite = f"if [ -e hang ]; then exec sleep 600; fi; {VM_SUITE}"

    async def go():
        task, b, rows, candidate = await at_candidate(dsn, tmp_path, writes={"hang": "x\n"}, suite=suite)
        stop = asyncio.get_running_loop().create_future()
        running = asyncio.ensure_future(verified(dsn, task, b, rows, candidate, asyncio.ensure_future(stop)))
        name = container.run_name(task, "head")
        up = await asyncio.to_thread(test_checks._wait, lambda: name in names(), 900)
        stop.set_result(None)
        with pytest.raises(checks._Stopped):
            await running
        return up, task

    up, task = run(go())
    assert up
    assert not container.running()

    async def appended():
        async with await db.connect(dsn) as conn:
            return [r for r in await ledger.read(conn, task) if r["type"] == fresh.VERIFY]

    assert run(appended()) == []


@pytest.mark.container
def test_a_stop_during_a_build_leaves_no_builder(tmp_path):
    async def go():
        base = await base_image()
        context = tmp_path / "ctx"
        context.mkdir()
        (context / "Containerfile").write_text(f"FROM {base}\nRUN sleep 600\n")
        stop = asyncio.get_running_loop().create_future()
        async with container.machine_lock(never()):
            building = asyncio.ensure_future(
                container.build("valor/stuck:test", context, tmp_path / "build.out", asyncio.ensure_future(stop),
                                "tests")
            )  # fmt: skip
            out = tmp_path / "build.out"
            up = await asyncio.to_thread(
                test_checks._wait,
                lambda: out.exists() and "sleep 600" in out.read_text(errors="replace"),
                900,
            )
            stop.set_result(None)
            with pytest.raises(checks._Stopped):
                await building
        return up

    try:
        assert run(go())
        assert not builder_up() and not container.BUILDER_OWNER.exists()
        assert unstopped(container.inspect, "valor/stuck:test") is None
    finally:
        container.stop_system()


@pytest.mark.container
def test_a_build_leaves_no_builder_and_no_cache_the_next_build_reuses(tmp_path):
    async def go():
        base = await base_image()
        digests = []
        async with container.machine_lock(never()):
            for i, step in enumerate(("echo planted > /cache/marker", "test ! -e /cache/marker")):
                context = tmp_path / f"ctx{i}"
                context.mkdir()
                (context / "Containerfile").write_text(
                    f"FROM {base}\nRUN --mount=type=cache,target=/cache {step}\n"
                )
                digests.append(await container.build(f"valor/cache{i}:test", context, tmp_path / f"{i}.out",
                                                     never(), "tests"))  # fmt: skip
                assert not builder_up()
        return digests

    try:
        first, second = run(go())
        assert first and second, (tmp_path / "1.out").read_text()
    finally:
        for tag in ("valor/cache0:test", "valor/cache1:test"):
            container.call("image", "delete", tag)
            container._record(tag, None, "")
        container.stop_system()


@pytest.mark.container
def test_an_image_retagged_by_hand_is_a_kernel_cause_and_dropped(tmp_path):
    async def go():
        base = await base_image()
        out = {}
        async with container.machine_lock(never()):
            for name in ("one", "two"):
                context = tmp_path / name
                context.mkdir()
                (context / "Containerfile").write_text(f"FROM {base}\nRUN echo {name} > /name\n")
                out[name] = await container.build(f"valor/{name}:test", context, tmp_path / f"{name}.out",
                                                  never(), "tests")  # fmt: skip
        return out

    try:
        digests = run(go())
        assert unstopped(container.checked, "valor/one:test") == digests["one"]
        # `image tag` leaves a built tag in place, so the swap deletes it first.
        assert container.call("image", "delete", "valor/one:test")[0] == 0
        assert container.call("image", "tag", "valor/two:test", "valor/one:test")[0] == 0
        with pytest.raises(container.Failed) as raised:
            unstopped(container.checked, "valor/one:test")
        assert raised.value.cause == "kernel"
        assert (
            unstopped(container.inspect, "valor/one:test") is None
            and "valor/one:test" not in container._images()
        )
    finally:
        for tag in ("valor/one:test", "valor/two:test"):
            container.call("image", "delete", tag)
            container._record(tag, None, "")
        container.stop_system()


PACKAGE = '{"name": "toy", "version": "1.0.0"}\n'
PLANTING = (
    '{"name": "toy", "version": "1.0.0", "scripts": {"postinstall": '
    '"echo planted > /home/valor/planted; sleep 600 &"}}\n'
)
PLANTED = "import os\n\n\ndef test_not_planted():\n    assert not os.path.exists('/home/valor/planted')\n"


@pytest.mark.container
def test_the_base_runs_in_its_own_manifests_image_and_images_are_kept_while_named(dsn, tmp_path, monkeypatch):
    """A candidate whose install script plants a file the tests read: the
    base run does not see it, `manifests_differ` is true, the build with a
    child left behind still finishes, an unchanged manifest reuses its
    image, and pruning keeps both while an open task names each."""
    files = {**test_checks.BASE_TESTS, ".gitignore": "setup-*\nnode_modules/\npackage-lock.json\n",
             "package.json": PACKAGE, "tests/test_planted.py": PLANTED}  # fmt: skip

    async def go():
        task, b, rows, candidate = await at_candidate(
            dsn, tmp_path, files=files, writes={"package.json": PLANTING}, setup=["npm install"]
        )
        return task, b, await verified(dsn, task, b, rows, candidate)

    _task, b, got = run(go())
    v = got["verify"]
    assert v["manifests_differ"] and v["image_tag"] != v["base_image_tag"], v
    assert "tests.test_planted::test_not_planted" in v["base_run"]["tests"]["passed"]
    assert v["failures"] == ["tests.test_planted::test_not_planted"]
    tags = {v["image_tag"], v["base_image_tag"]}
    unstopped(container.start)
    try:
        assert all(unstopped(container.checked, t) for t in tags)

        async def again():
            def refused(*a, **kw):
                raise AssertionError("an unchanged manifest built again")

            monkeypatch.setattr(container, "build", refused)
            lay = kws.Layout(Path(b.mirror).parent)
            k = container.keys(b, b.base_sha)
            return await container.ensure_deps(lay, b, b.base_sha, container.base_tag(), k["base"], never(),
                                               container.owner(dsn), "kernel")  # fmt: skip

        assert run(again()) == (v["base_image_tag"], v["base_image"])
        rows = run(container._open_rows(dsn))
        assert tags.isdisjoint(unstopped(container.prune, rows, set(), container.owner(dsn)))
        assert all(unstopped(container.checked, t) for t in tags)
        assert tags <= set(unstopped(container.prune, {}, set(), container.owner(dsn)))
        assert not any(unstopped(container.checked, t) for t in tags)
    finally:
        container.stop_system()


BACKENDS = {
    "hatchling": '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    '[tool.hatch.build.targets.wheel]\npackages = ["toy"]\n',
    # Its hooks run egg_info, which logs `running egg_info` on stdout. With
    # `find`, egg_info succeeds on the manifests alone, as popoto's does.
    "setuptools": '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n'
    '[tool.setuptools.packages.find]\ninclude = ["toy*"]\n',
}


@pytest.mark.container
@pytest.mark.parametrize("backend", sorted(BACKENDS))
def test_a_popoto_shaped_spec_installs_offline_in_the_vm(dsn, tmp_path, backend):
    """`uv sync --frozen --extra dev` with a build system and popoto's
    `UV_PYTHON = "3.12"`: the dependency image fetches the build system and
    the 3.12 wheels of a binary dependency, and the VM installs the project
    offline, each setup command's output kept."""
    project = tmp_path / "lock"
    project.mkdir()
    pyproject = (
        '[project]\nname = "toy"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
        '[project.optional-dependencies]\ndev = ["iniconfig==2.1.0", "msgpack==1.1.2"]\n' + BACKENDS[backend]
    )
    (project / "pyproject.toml").write_text(pyproject)
    (project / "toy").mkdir()
    (project / "toy" / "__init__.py").write_text("")
    subprocess.run([shutil.which("uv") or "uv", "lock", "--python", "3.14"], cwd=project, check=True,
                   capture_output=True)  # fmt: skip
    files = {
        **test_checks.BASE_TESTS,
        ".gitignore": "setup-*\n.venv/\n",
        "pyproject.toml": pyproject,
        "uv.lock": (project / "uv.lock").read_text(),
        "toy/__init__.py": "",
        "tests/test_dev.py": "import sys\n\n\ndef test_dev_extra():\n    import iniconfig, msgpack, toy  # noqa: F401\n"
        "    assert sys.version_info[:2] == (3, 12)\n",
    }

    async def go():
        task, b, rows, candidate = await at_candidate(
            dsn, tmp_path, files=files, writes={"greeting.txt": "hi\n"}, setup=["uv sync --frozen --extra dev"],
            suite=".venv/bin/python -B run.py {junit}", env={"UV_PYTHON": "3.12"},
        )  # fmt: skip
        return b, candidate, await verified(dsn, task, b, rows, candidate)

    b, candidate, got = run(go())
    v = got["verify"]
    assert v["cause"] is None and v["setup"][0]["exit"] == 0, v
    assert "tests.test_dev::test_dev_extra" not in v["failures"] and v["counts"]["failed"] == 0, v
    assert (
        v["setup"][0]["output"] == f"vm-head-{candidate[:12]}/out/setup-0.out" and "tail" not in v["setup"][0]
    )
    assert "msgpack" in (kws.Layout(Path(b.mirror).parent).checks / v["setup"][0]["output"]).read_text()


@pytest.mark.container
def test_a_suite_past_its_memory_gives_a_memory_cause(dsn, tmp_path, monkeypatch):
    memory(monkeypatch, 512)
    hog = "def test_hog():\n    b = bytearray(1024 * 1024 * 1024)\n    b[::4096] = b'x' * len(b[::4096])\n"

    async def go():
        task, b, rows, candidate = await at_candidate(dsn, tmp_path, writes={"tests/test_hog.py": hog})
        return await verified(dsn, task, b, rows, candidate)

    v = run(go())["verify"]
    assert v["cause"] == "memory" and v["memory_mb"] == 512, v
    monkeypatch.undo()
    assert not container.reusable(
        [{"type": fresh.VERIFY, "id": 1, "payload": v}], v["candidate"], v["digest"]
    )


@pytest.mark.container
def test_no_network_as_root_in_a_run_vm():
    async def go():
        return await base_image()

    try:
        base = run(go())
        probe = "; ".join(
            f"if (exec 3<>/dev/tcp/{h}/{p}) 2>/dev/null; then echo open {h}; fi"
            for h, p in (("1.1.1.1", 443), (GATEWAY, settings.pgport), (lan_address(), settings.pgport))
        )
        code, out = container.call(
            "run", "--rm", "--network", "none", "--entrypoint", "/bin/bash", base, "-c", f"id -u; {probe}"
        )
        # The output less the CLI's progress lines.
        printed = [line for line in out.splitlines() if line.strip() and not line.startswith("[")]
        assert code == 0 and printed == ["0"], out
    finally:
        container.stop_system()


@pytest.mark.container
def test_the_builder_cannot_reach_the_kernels_postgres_or_a_tasks_services(tmp_path):
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    lan = lan_address()
    probe = " ".join(
        f"if (exec 3<>/dev/tcp/{h}/{p}) 2>/dev/null; then echo REACHED {h}:{p}; exit 1; fi;"
        for h in (GATEWAY, lan)
        for p in (settings.pgport, port)
    )

    async def go():
        base = await base_image()
        context = tmp_path / "ctx"
        context.mkdir()
        (context / "Containerfile").write_text(
            f'FROM {base}\nSHELL ["/bin/bash", "-c"]\nRUN {probe} echo none\n'
        )
        async with container.machine_lock(never()):
            return await container.build(
                "valor/reach:test", context, tmp_path / "reach.out", never(), "tests"
            )

    try:
        assert run(go()), (tmp_path / "reach.out").read_text()
    finally:
        listener.close()
        container.call("image", "delete", "valor/reach:test")
        container._record("valor/reach:test", None, "")
        container.stop_system()


# -- reaping ---------------------------------------------------------------------------------


KILLED = """\
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print("locked", flush=True)
os.read(0, 1)
"""


@pytest.mark.container
def test_a_killed_kernels_vm_is_reaped_by_the_next_sweep_and_the_system_stopped(dsn):
    run(base_image())
    name = "valor-verify-killed-head"
    kernel = subprocess.Popen(
        [sys.executable, "-c", KILLED, str(container.LOCK)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert kernel.stdout.readline() == "locked\n"
        detached(name, container.owner(dsn))
        assert container.reap(dsn) == []  # a live verification: nothing touched
        os.kill(kernel.pid, signal.SIGKILL)
        kernel.wait()
        assert container.reap(dsn) == [name]
        assert not container.running()
    finally:
        if kernel.poll() is None:
            kernel.kill()
            kernel.wait()
        if container.running():
            container.remove(name)
            container.stop_system()


@pytest.mark.container
def test_a_sweep_touches_only_its_own_databases_vms(dsn):
    other = make_conninfo(dsn, dbname="valor_rebuild_test_other")
    assert container.owner(other) != container.owner(dsn)
    run(base_image())
    name = "valor-verify-first-head"
    try:
        detached(name, container.owner(dsn))
        held = container.try_lock()
        try:
            assert container.reap(other) == [] and name in names()
        finally:
            os.close(held)
        assert container.reap(other) == [] and name in names() and container.running()
        assert container.reap(dsn) == [name] and not container.running()
    finally:
        if container.running():
            container.remove(name)
            container.stop_system()


# -- this repository ---------------------------------------------------------------------------


@pytest.mark.container
def test_this_repositorys_suite_runs_in_the_vm_with_the_macos_tests_skipped(dsn, tmp_path):
    """A commit of HEAD's tree against HEAD, each in a VM: every `macos` test
    skips and the count is on `verify.ran`. The base is HEAD itself, since an
    older commit's suite may predate the `macos` marks."""
    repo = Path(__file__).resolve().parent.parent
    mirror = tmp_path / "mirror.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(mirror)], check=True)
    head = subprocess.run(["git", "-C", str(mirror), "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "same tree"],
                          capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    project = {
        "name": "valor", "kind": "python-uv", "services": ["postgres"], "roles": ["valor_kernel"],
        "setup": ["uv sync --frozen"], "suite": "uv run pytest -q -p no:cacheprovider --junitxml={junit} tests",
        "env": {"VALOR_PGHOST": "127.0.0.1", "VALOR_PGPORT": "5432", "VALOR_PG_OWNER": "app",
                "VALOR_PG_PASSFILE": str(container.VM_PASSFILE), "VALOR_TEST_DB": "app_test"},
    }  # fmt: skip
    base_sha = subprocess.run(["git", "-C", str(mirror), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()  # fmt: skip
    b = SimpleNamespace(mirror=str(mirror), base_sha=base_sha, project=project)
    lay = kws.Layout(tmp_path / "task")
    lay.checks.mkdir(parents=True)
    task = "repo-suite"

    async def record(payload):
        return {"verify": payload, "id": 0}

    async def go():
        return await container.verify(
            SimpleNamespace(task_id=task, dsn=dsn), lay, b, head, [], never(), record
        )

    got = run(go())
    v = got["verify"]
    assert v["cause"] is None and v["macos_skipped"] > 0, v
    assert v["counts"]["skipped"] >= v["macos_skipped"] and v["counts"]["passed"] > 0
    # Sparse-file sizing is portable POSIX, so it runs (and passes) in the VM.
    passed = set(v["base_run"]["tests"]["passed"])
    assert {
        "tests.test_look::test_a_sparse_screen_is_sized_without_being_read",
        "tests.test_transcripts::test_a_sparse_file_is_skipped_without_reading_its_holes",
    } <= passed, v["base_run"]["tests"]["failed"]
    # A test the VM cannot pass carries the `macos` mark, so no failure at
    # base hides a regression.
    assert v["base_run"]["tests"]["failed"] == v["base_run"]["tests"]["errored"] == [], v["base_run"]["tests"]
