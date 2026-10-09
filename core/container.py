"""The verification VM: the review's rerun in a fresh Apple container.

The review runner (`core/fresh.py`) calls `verify`. The base and the
candidate each run in a VM started from a kernel-built image:

- the base image, built from `core/images/base/` only and tagged by the
  digest of those files;
- a dependency image per project and manifest key (`deps_key`): the base
  image with the commit's lockfiles (`checks.LOCKFILES`) installed by the
  spec's own installing setup commands (`uv sync`, `npm ci`, `npm
  install`) with the network open, under the environment the VM's setup
  gets less its two offline variables, so they fetch what the offline
  setup asks for. A spec with none runs in the base image.

The base runs in the image of the base's own manifests, the candidate in
the image of its own, so a candidate never chooses its base's environment.
Every build runs in a fresh builder with `--no-cache`, deleted before and
after it. A local image is run by tag, so each tag's digest is recorded at
build time in `images.json` beside the lock and read back with `container
image inspect` just before the run; a mismatch is a `kernel` cause and the
tag is deleted, so the next run builds it again.

A run gets no network, `verify_memory_mb` and `verify_cpus`, and two
mounts: the run's `src/` (holding only `git archive`'s `source.tar` and the
kernel-written `spec.json`) read-only at `/valor/src`, and its `out/` at
`/valor/out`. `/valor` is root's with mode 700 inside the VM, so nothing
the candidate runs reaches either; `run.sh` (in the base image) runs the
setup offline, the suite, and the lint as the user `valor`, then writes
`out/result.json` and copies out the JUnit file, each setup command's
output, and the lint's. A run's setup entries carry each command's output
file and tail as the host's do; `verify.ran` records only their commands
and exits.

One verification at a time on the machine: an `fcntl.flock` on `LOCK`,
taken before `container system start` and held through the builds, the
runs, the `verify.ran` append, the prune, and `container system stop`. The
runtime is resident only while a verification runs. Every container
carries `valor.db` (the first 12 hex of the sha256 of the owning database's
name) and `valor.task`; the builder, which takes no label, is owned through
the `BUILDER_OWNER` file. `reap` kills and deletes what a killed kernel of
the same database left, when the lock is free.

Every CLI call writes its output to a file and is waited on by its exit;
the long ones (a build, a run) are raced against the task's stop, which
kills the group and then the builder or VM. No step has a time limit.
"""

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from psycopg.conninfo import conninfo_to_dict

from core import binaries, checks, db, git, ledger, machine, runs, workspace
from core.settings import settings

# Read from the installed release (`container system status`, `launchctl
# print gui/<uid>`, the launch-agent plists under the data directory).
RELEASE = "1.5.0"  # commit d265d669ecae041bf338cb3b39c4118316d138f0
# The runtime's data directory, relative to the home directory: its images,
# containers, kernels, and the launch-agent plists of its services.
DATA_DIR = "Library/Application Support/com.apple.container"
# The prefix every mach service of the runtime is registered under.
MACH_PREFIX = "com.apple.container."
MACH_NAMES = (
    "com.apple.container.apiserver",
    "com.apple.container.core.container-core-images",
    "com.apple.container.core.machine-apiserver",
    "com.apple.container.network.container-network-vmnet.default",
)

# The machine lock and the kernel's records beside it, relative to the home
# directory: shared by every kernel and test database on the machine, and
# denied to every sandbox profile.
STATE_DIR = "Library/Application Support/valor"
LOCK = Path.home() / STATE_DIR / "container.lock"
BUILDER_OWNER = LOCK.parent / "builder.owner"
IMAGES = LOCK.parent / "images.json"

IMAGES_DIR = Path(__file__).parent / "images"
BASE_DIR = IMAGES_DIR / "base"
DEPS_FILE = IMAGES_DIR / "deps" / "Containerfile"

WHERE = "vm"
# What `run.sh` gives the candidate inside the VM.
VM_HOME = Path("/home/valor")
VM_PASSFILE = VM_HOME / ".pgpass"
VM_JUNIT = VM_HOME / "junit.xml"
VM_PORTS = {"postgres": 5432, "redis": 6379}
VM_PATH = "/home/valor/.local/bin:/usr/lib/postgresql/18/bin:/usr/local/bin:/usr/bin:/bin"
VM_CACHES = {
    "UV_CACHE_DIR": "/home/valor/.cache/uv",
    "UV_PYTHON_INSTALL_DIR": "/home/valor/.local/share/uv/python",
    "npm_config_cache": "/home/valor/.npm",
    "UV_LINK_MODE": "copy",
}
VM_OFFLINE = {"UV_OFFLINE": "1", "npm_config_offline": "true"}
# The setup commands the dependency build runs with the network open
# (`core/images/base/deps.sh`); every other runs offline in the VM.
INSTALLING = re.compile(r"^(uv sync|npm ci|npm install)\b")
# The skip reason of a test marked `macos` (tests/conftest.py).
MACOS_ONLY = "macOS only"


class Failed(Exception):
    """A step that gives the run a cause: `kernel` or `commit`, and why."""

    def __init__(self, cause: str, why: str):
        super().__init__(why)
        self.cause, self.why = cause, why


# -- owners and the CLI ----------------------------------------------------------------


def owner(dsn: str) -> str:
    """The `valor.db` label of the database `dsn` names."""
    name = conninfo_to_dict(dsn).get("dbname") or ""
    return hashlib.sha256(name.encode()).hexdigest()[:12]


def require() -> str:
    """The CLI, after it and every helper pass `binaries.require`."""
    for helper in binaries.CONTAINER_HELPERS:
        binaries.require(helper)
    return binaries.require(binaries.CONTAINER)


def denied() -> bool:
    """Whether a sandbox this process runs under denies the runtime: every
    profile denies its mach services with its CLI, helpers, data directory,
    and the machine lock's directory (`core/workspace.py`)."""
    denies = runs._sandbox_check()
    return bool(denies) and denies(os.getpid(), MACH_NAMES[0])


def present() -> bool:
    """Whether the runtime can be run here: its CLI installed, and not
    denied by a sandbox this process runs under, where it reads as absent."""
    return Path(binaries.CONTAINER).exists() and not denied()


def call(*args: str) -> tuple[int, str]:
    """One short CLI call, its output to a temporary file, waited on by its
    exit. Returns (exit code, output)."""
    cli = require()
    with tempfile.TemporaryFile() as out:
        code = subprocess.run(
            [cli, *args], stdin=subprocess.DEVNULL, stdout=out, stderr=out, check=False
        ).returncode
        out.seek(0)
        return code, out.read().decode(errors="replace")


async def _race(args: list[str], out: Path, stop: asyncio.Task, on_stop: Callable[[], None]) -> int:
    """A long CLI call in its own session, its output to `out`, raced against
    the stop. On a stop: the group killed, `on_stop` run, `_Stopped`
    raised."""
    cli = require()
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        proc = await asyncio.create_subprocess_exec(
            cli, *args, stdin=asyncio.subprocess.DEVNULL, stdout=fd, stderr=fd, start_new_session=True
        )
    finally:
        os.close(fd)
    finished = asyncio.create_task(proc.wait())
    try:
        done, _ = await asyncio.wait({finished, stop}, return_when=asyncio.FIRST_COMPLETED)
        if finished in done:
            return proc.returncode
        runs._kill_group(proc.pid)
        await finished
        await asyncio.to_thread(on_stop)
        raise checks._Stopped
    finally:
        if not finished.done():
            runs._kill_group(proc.pid)
            await finished
            await asyncio.to_thread(on_stop)


async def short(
    stop: asyncio.Task, *args: str, on_stop: Callable[[], None] = lambda: None
) -> tuple[int, str]:
    """One short CLI call raced against the stop, as `_race` races a long
    one. Returns (exit code, output); raises `_Stopped` on a stop."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        code = await _race(list(args), out, stop, on_stop)
        return code, out.read_bytes().decode(errors="replace")


def running() -> bool:
    """Whether the container system is up. `container system status` exits
    non-zero when it is stopped."""
    code, out = call("system", "status")
    return code == 0 and re.search(r"^status\s+running$", out, re.MULTILINE) is not None


async def start(stop: asyncio.Task) -> float:
    """`container system start`, with the kernel the install fetched, raced
    against the stop; the seconds it took. Raises `Failed` (`kernel`)."""
    started = time.monotonic()
    code, out = await short(stop, "system", "start", "--disable-kernel-install")
    if code != 0:
        raise Failed("kernel", f"the container system did not start: {out.strip()[-300:]}")
    return round(time.monotonic() - started, 1)


def stop_system() -> None:
    call("system", "stop")


async def system_stopped(stop: asyncio.Task) -> None:
    """`system stop` raced against the stop; on a stop, `stop_system`
    itself runs."""
    await short(stop, "system", "stop", on_stop=stop_system)


def remove(name: str) -> None:
    """A VM killed and deleted, never stopped gracefully. What a stop and a
    sweep run, so nothing races it."""
    call("kill", name)
    call("delete", "--force", name)


async def removed(name: str, stop: asyncio.Task) -> None:
    """`remove` raced against the stop; on a stop, `remove` itself runs."""
    await short(stop, "kill", name, on_stop=lambda: remove(name))
    await short(stop, "delete", "--force", name, on_stop=lambda: remove(name))


def delete_builder() -> None:
    call("builder", "delete", "--force")
    BUILDER_OWNER.unlink(missing_ok=True)


async def builder_deleted(stop: asyncio.Task) -> None:
    """`delete_builder` raced against the stop; on a stop, `delete_builder`
    itself runs."""
    await short(stop, "builder", "delete", "--force", on_stop=delete_builder)
    BUILDER_OWNER.unlink(missing_ok=True)


def containers() -> list[dict[str, Any]]:
    """Every container on the machine: id and labels."""
    code, out = call("list", "--all", "--format", "json")
    if code != 0:
        return []
    return [
        {"id": c["configuration"]["id"], "labels": c["configuration"].get("labels") or {}}
        for c in json.loads(out or "[]")
    ]


# -- the machine lock ----------------------------------------------------------------


@contextlib.asynccontextmanager
async def machine_lock(stop: asyncio.Task):
    """The machine lock, tried once a second until free or the stop is heard
    (`_Stopped`, the lock never taken)."""
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                done, _ = await asyncio.wait({stop}, timeout=1.0)
                if done:
                    raise checks._Stopped from None
        yield
    finally:
        os.close(fd)


def try_lock() -> int | None:
    """The machine lock's descriptor if it is free now, else None."""
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


# -- images ---------------------------------------------------------------------------


def base_tag() -> str:
    """`valor/base:<sha256 of the files in core/images/base/>`."""
    h = hashlib.sha256()
    for p in sorted(BASE_DIR.iterdir()):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return f"valor/base:{h.hexdigest()}"


def installs(project: dict[str, Any]) -> bool:
    return any(INSTALLING.match(c) for c in project.get("setup") or ())


def manifests(mirror: str | Path, sha: str) -> str:
    """The lockfiles at the commit's top level, as `git ls-tree` lists them."""
    return git.trusted(mirror, "ls-tree", sha, "--", *checks.LOCKFILES)


def deps_key(base: str, project: dict[str, Any], listing: str) -> str:
    """What a dependency image depends on: the base image's tag, the
    Containerfile that builds it, the spec's kind, setup, and env (which
    `deps.sh` installs under), and the lockfiles."""
    h = hashlib.sha256()
    h.update(DEPS_FILE.read_bytes() + b"\0")
    env = json.dumps(project.get("env") or {}, sort_keys=True)
    for part in (base, project.get("kind") or "", repr(list(project.get("setup") or ())), env, listing):
        h.update(part.encode() + b"\0")
    return h.hexdigest()


def deps_tag(project: dict[str, Any], key: str) -> str:
    name = re.sub(r"[^a-z0-9._-]", "-", (project.get("name") or "project").lower())
    return f"valor/{name}:{key}"


def _images() -> dict[str, dict[str, str]]:
    try:
        return json.loads(IMAGES.read_text())
    except FileNotFoundError, json.JSONDecodeError:
        return {}


def _record(tag: str, digest: str | None, db_label: str) -> None:
    images = _images()
    if digest is None:
        images.pop(tag, None)
    else:
        images[tag] = {"digest": digest, "db": db_label}
    tmp = IMAGES.with_suffix(".tmp")
    tmp.write_text(json.dumps(images, indent=1, sort_keys=True))
    tmp.replace(IMAGES)


async def inspect(tag: str, stop: asyncio.Task) -> str | None:
    """The digest the runtime holds for `tag`, or None, raced against the
    stop."""
    code, out = await short(stop, "image", "inspect", tag)
    if code != 0:
        return None
    try:
        return json.loads(out)[0]["configuration"]["descriptor"]["digest"]
    except ValueError, LookupError, TypeError:
        return None


async def checked(tag: str, stop: asyncio.Task) -> str | None:
    """The recorded digest of `tag` when the runtime holds that digest; None
    when neither holds it. A tag held under another digest, or held with no
    record, is deleted and `Failed` (`kernel`) raised for a recorded one.
    Each CLI call is raced against the stop."""
    recorded = (await asyncio.to_thread(_images)).get(tag, {}).get("digest")
    held = await inspect(tag, stop)
    if recorded is not None and held == recorded:
        return recorded
    if held is not None:
        await short(stop, "image", "delete", tag)
    if recorded is not None:
        await asyncio.to_thread(_record, tag, None, "")
        if held is not None:
            raise Failed("kernel", f"{tag} is held under {held}, not the recorded {recorded}")
    return None


async def build(tag: str, context: Path, out: Path, stop: asyncio.Task, db_label: str,
                *, file: Path | None = None, build_args: dict[str, str] | None = None) -> str | None:  # fmt: skip
    """One image built in a fresh builder with no cache, labelled with the
    owning database, its digest recorded. The builder is deleted before and
    after. Returns the digest, or None when the build failed."""
    await builder_deleted(stop)
    BUILDER_OWNER.write_text(db_label)
    args = ["build", "--no-cache", "-t", tag, "-l", f"valor.db={db_label}"]
    for k, v in (build_args or {}).items():
        args += ["--build-arg", f"{k}={v}"]
    if file is not None:
        args += ["-f", str(file)]
    try:
        code = await _race([*args, str(context)], out, stop, delete_builder)
    finally:
        if stop.done():
            await asyncio.to_thread(delete_builder)
        else:
            await builder_deleted(stop)
    if code != 0:
        return None
    digest = await inspect(tag, stop)
    if digest is not None:
        await asyncio.to_thread(_record, tag, digest, db_label)
    return digest


async def ensure_base(lay: workspace.Layout, stop: asyncio.Task, db_label: str) -> tuple[str, str]:
    """The base image's tag and digest, built when missing. A failed build
    is the kernel's (`Failed`)."""
    tag = base_tag()
    digest = await checked(tag, stop)
    if digest is None:
        digest = await build(tag, BASE_DIR, lay.checks / "vm-base-image.out", stop, db_label)
        if digest is None:
            raise Failed("kernel", f"the base image did not build (see {lay.checks / 'vm-base-image.out'})")
    return tag, digest


async def ensure_deps(lay: workspace.Layout, b, sha: str, base: str, key: str, stop: asyncio.Task,
                      db_label: str, cause: str) -> tuple[str, str]:  # fmt: skip
    """The dependency image of the manifests at `sha`, built when missing
    from a context holding only those manifests and `spec.json`. A failed
    build has `cause`."""
    project = b.project or {}
    tag = deps_tag(project, key)
    digest = await checked(tag, stop)
    if digest is not None:
        return tag, digest
    context = lay.checks / f"vm-deps-{key[:12]}"
    await asyncio.to_thread(workspace.rmtree, context)
    (context / "manifests").mkdir(parents=True)
    listing = await asyncio.to_thread(manifests, b.mirror, sha)
    for line in listing.splitlines():
        name = line.split("\t", 1)[1]
        body = await asyncio.to_thread(git.show, b.mirror, sha, name)
        (context / "manifests" / name).write_bytes(body or b"")
    (context / "spec.json").write_text(json.dumps(spec_json(project), indent=1))
    out = lay.checks / f"vm-deps-{key[:12]}.out"
    digest = await build(tag, context, out, stop, db_label, file=DEPS_FILE, build_args={"BASE": base})
    await asyncio.to_thread(workspace.rmtree, context)
    if digest is None:
        raise Failed(cause, f"the dependencies at {sha[:12]} did not install (see {out})")
    return tag, digest


async def prune(rows_by_task: dict[str, list[dict]], keep: set[str], db_label: str,
                stop: asyncio.Task) -> list[str]:  # fmt: skip
    """Every dependency image of this database named by no open task's latest
    VM `verify.ran` and not in `keep`, deleted, each delete raced against the
    stop. Returns the tags deleted; raises `_Stopped` on a stop."""
    for rows in rows_by_task.values():
        if machine.fold(rows).state in (machine.State.MERGED, machine.State.STOPPED):
            continue
        for r in reversed(rows):
            p = r["payload"]
            if r["type"] == "verify.ran" and p.get("where") == WHERE:
                keep |= {p.get("image_tag"), p.get("base_image_tag")}
                break
    gone = []
    for tag, rec in (await asyncio.to_thread(_images)).items():
        if rec.get("db") == db_label and not tag.startswith("valor/base:") and tag not in keep:
            await short(stop, "image", "delete", tag)
            await asyncio.to_thread(_record, tag, None, "")
            gone.append(tag)
    return gone


# -- the run ---------------------------------------------------------------------------


def spec_json(project: dict[str, Any]) -> dict[str, Any]:
    """The spec as `run.sh` and `deps.sh` read it: services, roles, setup,
    suite with `{junit}` filled in, the lint as the kernel runs it, and the
    environment, the Postgres app password left as `{app_password}` for
    `run.sh` to fill."""
    spec = workspace.spec_of(project)
    env = workspace.harness_env(
        workspace.Layout(VM_HOME), spec, VM_PORTS, {"app": "{app_password}"},
        bin_dir=VM_HOME / ".local" / "bin", passfile=VM_PASSFILE,
    )  # fmt: skip
    own = {k: env[k] for k in spec.env}
    env.update({"PATH": VM_PATH, **VM_CACHES, **VM_OFFLINE})
    env.update(own)
    return {
        "kind": project.get("kind") or "plain",
        "services": list(project.get("services") or ()),
        "roles": list(project.get("roles") or ()),
        "setup": list(project.get("setup") or ()),
        "suite": (project.get("suite") or "true").replace("{junit}", str(VM_JUNIT)),
        "lint": checks.lint_command(project),
        "env": env,
    }


def run_name(task_id: str, role: str) -> str:
    return f"valor-verify-{task_id}-{role}"


async def run(ctx, lay: workspace.Layout, b, sha: str, role: str, image: str, stop: asyncio.Task,
              db_label: str) -> dict[str, Any]:  # fmt: skip
    """One VM run at `sha` in `image`: the source and spec in `src/`, the
    result read from `out/`. Returns the run record (`tests`, `exit`,
    `cause`, `why`, `setup`, `lint`, `peak_mb`, `macos_skipped`,
    `duration_s`). Raises `_Stopped` on a stop, the VM gone."""
    project = b.project or {}
    started = time.monotonic()
    check_dir = lay.checks / f"vm-{role}-{sha[:12]}"
    await asyncio.to_thread(workspace.rmtree, check_dir)
    (check_dir / "src").mkdir(parents=True)
    (check_dir / "out").mkdir()
    record: dict[str, Any] = {
        "commit": sha, "image": image, "exit": None, "tests": None, "junit": None, "setup": None,
        "lint": None, "peak_mb": None, "oom_kill": None, "macos_skipped": 0, "cause": None, "why": None,
    }  # fmt: skip

    def done(**kw) -> dict[str, Any]:
        record.update(kw, duration_s=round(time.monotonic() - started, 1))
        return record

    try:
        await asyncio.to_thread(
            git.trusted, b.mirror, "archive", "--format=tar", "-o", str(check_dir / "src" / "source.tar"), sha
        )
    except git.GitError as exc:
        return done(cause="kernel", why=f"no archive of {sha[:12]} from the mirror: {exc}")
    (check_dir / "src" / "spec.json").write_text(json.dumps(spec_json(project), indent=1))
    name = run_name(ctx.task_id, role)
    await removed(name, stop)
    args = [
        "run", "--name", name, "-l", f"valor.db={db_label}", "-l", f"valor.task={ctx.task_id}",
        "--network", "none", "-m", f"{settings.verify_memory_mb}M", "-c", str(settings.verify_cpus),
        "--mount", f"type=bind,source={check_dir / 'src'},target=/valor/src,readonly",
        "--mount", f"type=bind,source={check_dir / 'out'},target=/valor/out",
        image,
    ]  # fmt: skip
    try:
        code = await _race(args, lay.checks / f"{check_dir.name}.run.out", stop, lambda: remove(name))
    finally:
        if stop.done():
            await asyncio.to_thread(remove, name)
        else:
            await removed(name, stop)
    return done(**read_result(lay, check_dir.name, code, role, project))


def read_result(
    lay: workspace.Layout, name: str, code: int, role: str, project: dict[str, Any]
) -> dict[str, Any]:
    """The run's fields from `out/result.json`, `out/junit.xml`,
    `out/lint.out`, and each setup command's `out/setup-N.out`, each read
    through `workspace.read_turn_file`; the lint's ruff locations for kind
    `python-uv` only, and each setup command's output file and tail, as on
    the host."""
    fd = os.open(lay.checks, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        body, why = workspace.read_turn_file(fd, f"{name}/out/result.json")
        lint_out, _ = workspace.read_turn_file(fd, f"{name}/out/lint.out")
        junit, _ = workspace.read_turn_file(fd, f"{name}/out/junit.xml")
        try:
            result = json.loads(body) if body is not None else None
        except ValueError as exc:
            return {"cause": "kernel", "why": f"the VM's result is not JSON ({exc})"}
        setup = []
        for n, s in enumerate((result or {}).get("setup") or ()):
            output, _ = workspace.read_turn_file(fd, f"{name}/out/setup-{n}.out")
            tail = (output or b"").decode(errors="replace")[-1500:]
            setup.append({**s, "tail": tail, "output": f"setup-{n}.out"})
    finally:
        os.close(fd)
    if result is None:
        return {"cause": "kernel", "why": f"the VM exited {code} with no result ({why})"}
    if result.get("services") == "failed":
        return {"cause": "kernel", "why": f"the VM's services did not start: {result.get('why')}"}
    out: dict[str, Any] = {
        "setup": setup, "peak_mb": result.get("peak_mb"), "oom_kill": result.get("oom_kill"),
    }  # fmt: skip
    failed = [s for s in setup if s.get("exit") != 0]
    if failed:
        s = failed[0]
        return {**out, "exit": s["exit"], "tail": s["tail"], "cause": "commit",
                "why": f"setup failed at {role}: {s['command']} exited {s['exit']}"}  # fmt: skip
    suite = result.get("suite") or {}
    out["exit"] = suite.get("exit")
    tests, why = checks.read_junit(lay.checks, name, relpath="out/junit.xml")
    out["tests"], out["junit"] = tests, why
    out["macos_skipped"] = macos_skipped(junit) if tests is not None else 0
    lint = result.get("lint")
    if lint is not None:
        lint = {"command": checks.lint_command(project), **lint}
        if lint_out is not None and project.get("kind") == "python-uv":
            lint["locations"] = checks.lint_locations(lint_out.decode(errors="replace").splitlines())
    out["lint"] = lint
    if out["exit"] != 0 and (
        (result.get("oom_kill") or 0) > 0
        or (out["peak_mb"] is not None and out["peak_mb"] >= settings.verify_memory_mb)
    ):
        return {
            **out,
            "cause": "memory",
            "why": f"the VM ran out of memory at {settings.verify_memory_mb} MB",
        }
    if tests is None and out["exit"] not in (0, 1):
        return {
            **out,
            "cause": "commit",
            "why": f"no JUnit report ({why}) and the suite exited {out['exit']} at {role}",
        }
    return out


def macos_skipped(junit: bytes | None) -> int:
    """How many tests the JUnit file says were skipped as `macos`."""
    if junit is None or checks._declares(junit):
        return 0
    try:
        tree = ET.fromstring(junit)
    except ET.ParseError:
        return 0
    return sum(
        1
        for case in tree.iter("testcase")
        for s in case.findall("skipped")
        if (s.get("message") or "").startswith(MACOS_ONLY)
    )


# -- verify --------------------------------------------------------------------------


def keys(b, candidate: str) -> dict[str, str]:
    """The reuse keys: the base image's tag and the base and head
    dependency keys (each the base tag itself when the spec installs
    nothing)."""
    project = b.project or {}
    base = base_tag()
    if not installs(project):
        return {"base_image": base, "base": base, "head": base}
    return {
        "base_image": base,
        "base": deps_key(base, project, manifests(b.mirror, b.base_sha)),
        "head": deps_key(base, project, manifests(b.mirror, candidate)),
    }


def reusable(rows: list[dict], candidate: str, head_key: str) -> dict | None:
    """The latest VM `verify.ran` with this candidate, head key, and
    `memory_mb`, and a cause other than `kernel`."""
    for r in reversed(rows):
        p = r["payload"]
        if (
            r["type"] == "verify.ran"
            and p.get("where") == WHERE
            and p.get("candidate") == candidate
            and p.get("digest") == head_key
            and p.get("memory_mb") == settings.verify_memory_mb
            and p.get("cause") != "kernel"
        ):
            return r
    return None


def reusable_base(rows: list[dict], base_sha: str, base_key: str) -> dict | None:
    """The base run of the latest VM `verify.ran` with this base, base key,
    and `memory_mb`, its cause not `kernel`."""
    for r in reversed(rows):
        p = r["payload"]
        run_ = p.get("base_run") or {}
        if (
            r["type"] == "verify.ran"
            and p.get("where") == WHERE
            and p.get("base") == base_sha
            and p.get("base_digest") == base_key
            and p.get("memory_mb") == settings.verify_memory_mb
            and run_.get("cause") != "kernel"
        ):
            return run_
    return None


async def verify(ctx, lay: workspace.Layout, b, candidate: str, rows: list[dict], stop: asyncio.Task,
                 record: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> dict[str, Any]:  # fmt: skip
    """The base and head runs in VMs and the `verify.ran` payload, passed to
    `record` (which appends it) while the machine lock is held. Returns
    `record`'s result, or a runner result: `failed` on a `kernel` cause
    (nothing appended), `stopped`. Raises `_Stopped` on a stop."""
    project = b.project or {}
    db_label = owner(ctx.dsn)
    try:
        k = await asyncio.to_thread(keys, b, candidate)
        deleted = await asyncio.to_thread(checks.removed_definitions, b.mirror, b.base_sha, candidate)
    except git.GitError as exc:
        return {"status": "failed", "turn": {"result": f"the manifests or the diff: {exc}"}}
    try:
        await asyncio.to_thread(require)
    except binaries.Untrusted as exc:
        return {"status": "failed", "turn": {"result": str(exc)}}
    async with machine_lock(stop):
        started_s = None
        head_failed: Failed | None = None
        try:
            started_s = await start(stop)
            base_tag_, base_digest_ = await ensure_base(lay, stop, db_label)
            images = {"base": (base_tag_, base_digest_), "head": (base_tag_, base_digest_)}
            if installs(project):
                images["base"] = await ensure_deps(
                    lay, b, b.base_sha, base_tag_, k["base"], stop, db_label, "kernel"
                )
                if k["head"] == k["base"]:
                    images["head"] = images["base"]
                else:
                    try:
                        images["head"] = await ensure_deps(
                            lay, b, candidate, base_tag_, k["head"], stop, db_label, "commit"
                        )
                    except Failed as exc:
                        if exc.cause != "commit":
                            raise
                        images["head"] = None
                        head_failed = exc
            base_run = reusable_base(rows, b.base_sha, k["base"])
            if base_run is None:
                tag, digest = images["base"]
                if await checked(tag, stop) != digest:
                    raise Failed("kernel", f"{tag} is no longer held")
                base_run = await run(ctx, lay, b, b.base_sha, "base", tag, stop, db_label)
                if base_run["cause"] == "kernel":
                    raise Failed("kernel", base_run["why"])
            if images["head"] is None:
                head_run = {"commit": candidate, "image": None, "exit": None, "tests": None, "setup": None,
                            "lint": None, "peak_mb": None, "oom_kill": None, "macos_skipped": 0,
                            "cause": "commit", "why": head_failed.why, "duration_s": 0.0}  # fmt: skip
            else:
                tag, digest = images["head"]
                if await checked(tag, stop) != digest:
                    raise Failed("kernel", f"{tag} is no longer held")
                head_run = await run(ctx, lay, b, candidate, "head", tag, stop, db_label)
                if head_run["cause"] == "kernel":
                    raise Failed("kernel", head_run["why"])
            lists = checks.compare(base_run, head_run, deleted)
            payload = verify_payload(candidate, b.base_sha, k, images, base_run, head_run, lists, started_s)
            result = await record(payload)
            keep = {t for t in (images["base"][0], (images["head"] or ("",))[0])}
            rows_by_task = await _open_rows(ctx.dsn)
            await prune(rows_by_task, keep, db_label, stop)
            return result
        except Failed as exc:
            return {"status": "failed", "turn": {"result": exc.why}}
        finally:
            if stop.done():
                await asyncio.to_thread(stop_system)
            else:
                await system_stopped(stop)


async def _open_rows(dsn: str) -> dict[str, list[dict]]:
    async with await db.connect(dsn) as conn:
        ids = [
            r[0]
            for r in await (await conn.execute("SELECT id FROM documents WHERE kind = 'task'")).fetchall()
        ]
        return {t: await ledger.read(conn, t) for t in ids}


def verify_payload(candidate: str, base_sha: str, k: dict[str, str], images: dict, base_run: dict,
                   head_run: dict, lists: dict[str, list[str]], started_s: float | None) -> dict[str, Any]:  # fmt: skip
    """The `verify.ran` fields: ids, counts, codes, lint locations, the
    images and the VM's numbers, never a message the candidate printed."""
    tests = head_run.get("tests")
    head_image = images["head"] or (None, None)
    return {
        "where": WHERE,
        "candidate": candidate,
        "base": base_sha,
        "digest": k["head"],
        "base_digest": k["base"],
        "image": head_image[1],
        "image_tag": head_image[0],
        "base_image": images["base"][1],
        "base_image_tag": images["base"][0],
        "manifests_differ": k["head"] != k["base"],
        "memory_mb": settings.verify_memory_mb,
        "cpus": settings.verify_cpus,
        "peak_mb": head_run.get("peak_mb"),
        "release": RELEASE,
        "system_start_s": started_s,
        "result_owner": "kernel",
        "macos_skipped": head_run.get("macos_skipped", 0),
        "exit": head_run.get("exit"),
        "counts": {key: len(v) for key, v in tests.items()} if tests is not None else None,
        "lint": head_run.get("lint"),
        "setup": [{"command": s.get("command"), "exit": s.get("exit")} for s in head_run.get("setup") or ()],
        "failures": lists["failures"],
        "failing_at_base": lists["failing_at_base"],
        "deleted_at_head": lists["deleted_at_head"],
        "duration_s": head_run.get("duration_s"),
        "cause": head_run.get("cause"),
        "why": head_run.get("why"),
        "base_run": {
            key: base_run.get(key)
            for key in ("tests", "exit", "cause", "why", "peak_mb", "image", "duration_s", "macos_skipped")
        },
    }


# -- the sweep ------------------------------------------------------------------------


def reap(dsn: str) -> list[str]:
    """What a killed kernel of this database left, when no verification is
    live on the machine (the lock is free): its labelled containers killed
    and deleted, its builder deleted. The system is stopped only when no
    container and no builder remains. Returns what was removed."""
    if not present():
        return []
    fd = try_lock()
    if fd is None:
        return []
    try:
        if not running():
            return []
        mine = owner(dsn)
        gone = []
        for c in containers():
            if c["labels"].get("valor.db") == mine:
                remove(c["id"])
                gone.append(c["id"])
        if BUILDER_OWNER.exists() and BUILDER_OWNER.read_text().strip() == mine:
            delete_builder()
            gone.append("builder")
        if not BUILDER_OWNER.exists() and not containers():
            stop_system()
        return gone
    finally:
        os.close(fd)
