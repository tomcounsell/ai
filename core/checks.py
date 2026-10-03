"""The test runner: breadth, then the project's suite at base, then at head.

`test_runner(port)` is the runner for `Check.TEST`. It asks the breadth
judgement first (about a cent, reused per candidate), so an outage in both
judgement legs costs no suite run. Then it runs the project's own suite
command (the spec's `suite`, never the candidate's choice) twice: at the
base the task started from and at the candidate. Each run gets:

- a blind checkout from the kernel mirror under
  `checks/test-<role>-<sha>/repo` (`workspace.blind_checkout`);
- its own copy of the caches, cloned from `checks/seed/` (the base's
  setup output), so nothing one run writes reaches another;
- fresh Postgres and Redis instances on the task's own ports
  (`workspace.check_services`), the task's own stopped meanwhile;
- the spec's setup, run once more in the same run when it fails;
- the check profile, its own process group, a stop raced against it as for
  a turn, and `settings.suite_timeout_s`.

Each run appends `suite.ran`. A run with the same commit, role, command,
and environment digest, and no `cause`, is reused, so the base runs once per
task. `cause: "kernel"` (a service that would not start) is recorded nowhere
and the runner returns `failed`, so the branch reruns; `cause:
"commit"` (setup failed twice, the suite timed out, or no JUnit report and
an exit code saying the runner itself failed) is the commit's own fault.

The kernel compares the two runs by test id (`compare`) and records
`test.decided` with leg `kernel` (`verdicts.record_check`), which computes
the verdict.
"""

import asyncio
import hashlib
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core import db, git, judgement_sites, ledger, machine, runs, tasks, verdicts, workspace
from core.machine import Check, State
from core.settings import settings

SUITE = "suite.ran"
JUNIT = "tmp/junit.xml"
SEED = "seed"
BOTH_FAIL = "the suite fails at base too, and without per-test results no failure at head can be told apart"
# Files at a commit's top level that pin what setup installs.
LOCKFILES = (
    "uv.lock",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    ".python-version",
    ".nvmrc",
)


# -- JUnit -------------------------------------------------------------------------


def read_junit(checks_dir: Path, name: str) -> tuple[dict[str, list[str]] | None, str | None]:
    """The per-test results the suite wrote at `<name>/tmp/junit.xml` under
    the kernel-owned `checks_dir`: `checks_dir` and the check directory
    `name` each opened with `O_NOFOLLOW | O_DIRECTORY`, the file read whole
    with `workspace.read_turn_file` (no link followed, no FIFO blocked on).
    A file holding a DOCTYPE or an entity declaration is refused. Returns
    ({passed, failed, errored, skipped: [test id]}, None) or (None, why
    not). A test id is `classname::name`."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY
    try:
        root = os.open(checks_dir, flags)
    except OSError as exc:
        return None, f"no checks directory ({exc.strerror})"
    try:
        try:
            check = os.open(name, flags, dir_fd=root)
        except OSError as exc:
            return None, f"{name} is not a plain directory ({exc.strerror})"
        try:
            body, why = workspace.read_turn_file(check, JUNIT)
        finally:
            os.close(check)
    finally:
        os.close(root)
    if body is None:
        return None, why
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", body, re.IGNORECASE):
        return None, "the JUnit report holds a DOCTYPE"
    try:
        tree = ET.fromstring(body)
    except ET.ParseError as exc:
        return None, f"the JUnit report is not XML ({exc})"
    out: dict[str, list[str]] = {"passed": [], "failed": [], "errored": [], "skipped": []}
    for case in tree.iter("testcase"):
        name = case.get("name") or ""
        cls = case.get("classname") or case.get("file") or ""
        tid = f"{cls}::{name}" if cls else name
        tags = {child.tag for child in case}
        if "error" in tags:
            out["errored"].append(tid)
        elif "failure" in tags:
            out["failed"].append(tid)
        elif "skipped" in tags:
            out["skipped"].append(tid)
        else:
            out["passed"].append(tid)
    return out, None


# -- what the diff removes ---------------------------------------------------------------


def _name(test_id: str) -> str:
    """The test's function name: the id's last part, parameters dropped."""
    return test_id.rsplit("::", 1)[-1].split("[", 1)[0]


def removed_definitions(mirror: str | Path, base: str, head: str) -> Callable[[str], bool]:
    """A predicate: does the diff from `base` to `head` remove the
    definition of this test id?

    For an id that maps to a Python file in the base tree (its classname's
    dotted prefix as a path): the file was deleted; or a line defining the
    test's function or one of its classes was removed and not added back in
    the same file; or the id carries parameters, its function still exists,
    and the diff removes lines in that file (a dropped parametrize case).
    Any other id is removed when a line the diff removes holds its name."""
    deleted: set[str] = set()
    for line in git.trusted(mirror, "diff", "--no-renames", "--name-status", base, head).splitlines():
        status, _, path = line.partition("\t")
        if status == "D":
            deleted.add(path)
    removed: dict[str, list[str]] = {}
    added: dict[str, list[str]] = {}
    path = None
    diff = git.trusted(mirror, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "-U0", base, head)
    for line in diff.splitlines():
        if line.startswith("--- "):
            continue
        if line.startswith("+++ "):
            continue
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1]
            continue
        if path and line.startswith("-"):
            removed.setdefault(path, []).append(line[1:])
        elif path and line.startswith("+"):
            added.setdefault(path, []).append(line[1:])
    base_files = set(git.trusted(mirror, "ls-tree", "-r", "--name-only", base).splitlines())
    every_removed = [x for lines in removed.values() for x in lines]

    def defines(lines: list[str], name: str) -> bool:
        pattern = re.compile(rf"^\s*(async\s+def|def|class)\s+{re.escape(name)}\b")
        return any(pattern.match(x) for x in lines)

    def located(test_id: str) -> tuple[str, list[str]] | None:
        cls = test_id.rsplit("::", 1)[0] if "::" in test_id else ""
        if cls.endswith(".py") and cls in base_files:
            return cls, []
        parts = cls.split(".") if cls else []
        for k in range(len(parts), 0, -1):
            candidate = "/".join(parts[:k]) + ".py"
            if candidate in base_files:
                return candidate, parts[k:]
        return None

    def gone(test_id: str) -> bool:
        name = _name(test_id)
        where = located(test_id)
        if where is None:
            return any(name in x for x in every_removed)
        path, classes = where
        if path in deleted:
            return True
        lost = removed.get(path, [])
        back = added.get(path, [])
        for n in (name, *classes):
            if defines(lost, n) and not defines(back, n):
                return True
        return bool("[" in test_id.rsplit("::", 1)[-1] and lost)

    return gone


# -- comparing base and head --------------------------------------------------------


def _fails(run: dict[str, Any]) -> bool:
    return bool(run.get("cause")) or run.get("exit") != 0


def compare(
    base: dict[str, Any], head: dict[str, Any], deleted: Callable[[str], bool]
) -> dict[str, list[str]]:
    """The test verdict's lists from two `suite.ran` payloads:

    - `failures`: ids failing or erroring at head that did not fail at base,
      and every id that passed at base and is absent or skipped at head,
      unless the diff removes its definition; a head run the commit broke
      (`cause: commit`) is a failure itself; with no per-test result on
      either side, a head that fails while the base passes, or the note
      `BOTH_FAIL` when both fail;
    - `deleted_at_head`: ids passing at base, absent at head, whose
      definition the diff removes;
    - `failing_at_base`: ids failing or erroring at base and at head."""
    failures: list[str] = []
    deleted_at_head: list[str] = []
    failing_at_base: list[str] = []
    bt, ht = base.get("tests"), head.get("tests")
    if head.get("cause") == "commit":
        failures.append(head.get("why") or "the suite could not run at head")
    if bt is None and ht is None:
        if _fails(head) and not failures:
            failures.append(
                BOTH_FAIL
                if _fails(base)
                else f"the suite exited {head.get('exit')} at head and {base.get('exit')} at base"
            )
        elif _fails(head) and _fails(base):
            failures.append(BOTH_FAIL)
        return {"failures": failures, "deleted_at_head": [], "failing_at_base": []}
    bt = bt or {"passed": [], "failed": [], "errored": [], "skipped": []}
    base_pass = set(bt["passed"])
    base_fail = set(bt["failed"]) | set(bt["errored"])
    if ht is None:
        head_pass: set[str] = set()
        head_fail: set[str] = set()
        if not failures and head.get("exit") not in (0, None):
            failures.append(f"the suite exited {head.get('exit')} at head with no per-test results")
    else:
        head_pass = set(ht["passed"])
        head_fail = set(ht["failed"]) | set(ht["errored"])
    failures += sorted(head_fail - base_fail)
    failing_at_base = sorted(head_fail & base_fail)
    present = head_pass | head_fail | (set(ht["skipped"]) if ht else set())
    for tid in sorted(base_pass - head_pass - head_fail):
        if tid not in present and deleted(tid):
            deleted_at_head.append(tid)
        else:
            failures.append(tid)
    return {"failures": failures, "deleted_at_head": deleted_at_head, "failing_at_base": failing_at_base}


# -- one suite run ------------------------------------------------------------------------


def environment_digest(mirror: str | Path, sha: str, project: dict[str, Any], bin_dir: Path) -> str:
    """What a run's result depends on besides the commit's code: the
    lockfiles at the commit, the setup commands, the spec's env, and the
    bytes of each file in the work directory's `bin/`."""
    h = hashlib.sha256()
    listing = git.trusted(mirror, "ls-tree", sha, "--", *LOCKFILES)
    h.update(listing.encode())
    h.update(repr(list(project.get("setup") or ())).encode())
    h.update(repr(sorted((project.get("env") or {}).items())).encode())
    if bin_dir.is_dir():
        for p in sorted(bin_dir.iterdir()):
            h.update(p.name.encode() + b"\0")
            if p.is_symlink():
                h.update(b"link:" + os.readlink(p).encode())
            elif p.is_file():
                h.update(p.read_bytes())
    return h.hexdigest()


def reusable(rows: list[dict], sha: str, command: str, digest: str, role: str) -> dict | None:
    """The latest `suite.ran` with this commit, role, command, and digest and
    no `cause`. The role is in the key, so a head run of one check never
    stands in for another's."""
    for r in reversed(rows):
        p = r["payload"]
        if (
            r["type"] == SUITE
            and p.get("commit") == sha
            and p.get("role") == role
            and p.get("command") == command
            and p.get("digest") == digest
            and not p.get("cause")
        ):
            return r
    return None


class _Stopped(Exception):
    pass


async def _race(argv: list[str], *, cwd: Path, env: dict[str, str], mark: str, timeout: float,
                stop: asyncio.Task, out: Path) -> tuple[int | str, str, int]:  # fmt: skip
    """Run `argv` in its own process group, its output to `out` (a file the
    kernel opened outside the check directory), raced against the stop and
    `timeout`. Returns (exit code or `timeout`, output tail, peak summed
    footprint). Raises `_Stopped` after killing the group on a stop."""
    fd = os.open(out, os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=fd,
            stderr=fd,
            start_new_session=True,
        )
        finished = asyncio.create_task(proc.wait())
        deadline = time.monotonic() + timeout
        peak = 0
        code: int | str | None = None
        try:
            while True:
                left = deadline - time.monotonic()
                done, _ = await asyncio.wait(
                    {finished, stop}, timeout=max(0.0, min(1.0, left)), return_when=asyncio.FIRST_COMPLETED
                )
                if finished in done:
                    code = proc.returncode
                    break
                if stop in done:
                    runs._kill_group(proc.pid)
                    await finished
                    raise _Stopped
                if time.monotonic() >= deadline:
                    runs._kill_group(proc.pid)
                    await finished
                    code = "timeout"
                    break
                pids = await asyncio.to_thread(workspace._group, proc.pid)
                peak = max(peak, sum(workspace.footprint(p) for p in pids))
        finally:
            if not finished.done():
                runs._kill_group(proc.pid)
            await asyncio.to_thread(runs.reap, mark, proc.pid)
        size = os.fstat(fd).st_size
        tail = os.pread(fd, 4000, max(0, size - 4000)).decode(errors="replace")
    finally:
        os.close(fd)
    return code, tail, peak


async def _setup(checkout: Path, harness: dict[str, Any], commands: list[str], mark: str,
                 stop: asyncio.Task, out: Path) -> dict[str, Any]:  # fmt: skip
    """The spec's setup once in `checkout`; stops at the first failure."""
    ran: list[dict[str, Any]] = []
    for command in commands:
        step = f"{mark}-{len(ran)}"
        argv, env = workspace.setup_command(harness, step, command)
        code, tail, _ = await _race(
            argv, cwd=checkout, env=env, mark=step, timeout=settings.setup_timeout_s, stop=stop, out=out
        )
        ran.append({"command": command, "exit": code, "tail": tail[-1500:]})
        if code != 0:
            return {"ok": False, "commands": ran}
    return {"ok": True, "commands": ran}


async def suite(ctx, lay: workspace.Layout, b: tasks.Brief, sha: str, role: str, stop: asyncio.Task,
                *, digest: str, run_suite: bool = True) -> dict[str, Any]:  # fmt: skip
    """One run at `sha`: checkout, the cache clone, fresh services, setup
    (twice at most), the suite. Returns the `suite.ran` payload. With
    `run_suite` false only setup runs, to make the seed cache. Raises
    `_Stopped` on a stop."""
    project = b.project or {}
    command = project.get("suite") or "true"
    started = time.monotonic()
    check_dir = workspace.fresh_dir(lay.checks / f"test-{role}-{sha[:12]}")
    checkout = check_dir / "repo"
    out = lay.checks / f"{check_dir.name}.out"
    mark = f"test-{ctx.task_id}-{role}"
    payload: dict[str, Any] = {
        "commit": sha,
        "role": role,
        "command": command,
        "digest": digest,
        "exit": None,
        "tests": None,
        "junit": None,
        "duration_s": 0.0,
        "tail": "",
        "peak_footprint": 0,
        "setup": None,
        "cause": None,
        "why": None,
    }

    def done(**kw) -> dict[str, Any]:
        payload.update(kw, duration_s=round(time.monotonic() - started, 1))
        return payload

    try:
        await asyncio.to_thread(workspace.blind_checkout, b.mirror, b.base_sha, sha, checkout)
    except git.GitError as exc:
        return done(cause="commit", why=f"no checkout of {sha[:12]}: {exc}")
    seed = lay.checks / SEED
    cache = check_dir / "cache"
    if seed.is_dir():
        await asyncio.to_thread(workspace.clone_tree, seed, cache)
    else:
        cache.mkdir()
    services = workspace.check_services(lay, check_dir, project, ctx.task_id)
    try:
        # A failed start has already cleaned up inside `check_services`.
        env = await asyncio.to_thread(services.__enter__)
    except Exception as exc:  # noqa: BLE001  a service step failing is the kernel's, never the commit's
        return done(cause="kernel", why=f"the check's services did not start: {exc}")
    try:
        names = list(project.get("services") or ())
        ports = [int((project.get("ports") or {})[n]) for n in names]
        harness = workspace.check_harness(lay, check_dir, ports, env, services=True)
        commands = list(project.get("setup") or ())
        setup = await _setup(checkout, harness, commands, f"{mark}-setup", stop, out)
        if not setup["ok"]:
            setup = await _setup(checkout, harness, commands, f"{mark}-setup", stop, out)
        payload["setup"] = setup
        if not setup["ok"]:
            last = setup["commands"][-1]
            return done(
                exit=last["exit"], tail=last["tail"], cause="commit",
                why=f"setup failed twice at {role}: {last['command']} exited {last['exit']}",
            )  # fmt: skip
        if role == "base" and commands:
            await asyncio.to_thread(workspace.clone_tree, cache, seed)
        if not run_suite:
            return done()
        junit = check_dir / JUNIT
        argv = workspace.sandboxed(Path(harness["sandbox_profile"]), mark, "/bin/bash", "-c",
                                   command.replace("{junit}", str(junit)))  # fmt: skip
        run_env = {**workspace.turn_environment(harness), runs.TURN_ENV: mark}
        code, tail, peak = await _race(
            argv, cwd=checkout, env=run_env, mark=mark, timeout=settings.suite_timeout_s, stop=stop, out=out
        )
    finally:
        await asyncio.to_thread(services.__exit__, None, None, None)
    tests, why = read_junit(lay.checks, check_dir.name)
    cause = None
    reason = None
    if code == "timeout":
        cause, reason = "commit", f"the suite ran past {settings.suite_timeout_s:.0f} s at {role}"
    elif tests is None and code not in (0, 1):
        cause, reason = "commit", f"no JUnit report ({why}) and the suite exited {code} at {role}"
    return done(exit=code, tests=tests, junit=why, tail=tail, peak_footprint=peak, cause=cause, why=reason)


# -- the runner ---------------------------------------------------------------------------


def test_runner(port):
    """The runner for `Check.TEST`."""

    async def run(ctx) -> dict[str, Any]:
        async with await db.connect(ctx.dsn) as conn:
            rows = await ledger.read(conn, ctx.task_id)
            f = machine.fold(rows)
            if f.state is State.STOPPED:
                return {"status": "stopped", "state": await tasks.status(conn, ctx.task_id)}
            if f.state is not State.CHECKS or Check.TEST in f.checks:
                return {"status": "moved"}
            b = await tasks.brief(conn, ctx.task_id)
            state = await tasks.status(conn, ctx.task_id)

        def failed(why: str) -> dict[str, Any]:
            return {"status": "failed", "state": state, "turn": {"result": why}}

        if not b.mirror:
            return failed("the test runner runs only in a workspace the kernel provisioned")
        candidate = f.candidate
        try:
            breadth_id = await judgement_sites.breadth(port, ctx.dsn, ctx.task_id)
        except tasks.TaskStopped:
            return {"status": "stopped"}
        except judgement_sites.Unusable as exc:
            return failed(f"breadth: {exc}")
        async with await db.connect(ctx.dsn) as conn:
            rows = await ledger.read(conn, ctx.task_id)
        try:
            judgement_sites.breadth_outcome(rows, breadth_id, candidate)
        except judgement_sites.Unanswered as exc:
            return failed(str(exc))
        except judgement_sites.Unusable as exc:
            return failed(f"breadth: {exc}")

        lay = workspace.Layout(Path(b.mirror).parent)
        bin_dir = lay.root.parent / "bin"
        command = (b.project or {}).get("suite") or "true"
        listener = await db.connect(ctx.dsn)
        stop = None
        try:
            await listener.execute(f"LISTEN {tasks.STOP_CHANNEL}")
            stop = asyncio.create_task(runs._stop_heard(listener, ctx.task_id))
            ran: dict[str, dict[str, Any]] = {}
            for role, sha in (("base", b.base_sha), ("head", candidate.sha)):
                digest = await asyncio.to_thread(environment_digest, b.mirror, sha, b.project or {}, bin_dir)
                async with await db.connect(ctx.dsn) as conn:
                    rows = await ledger.read(conn, ctx.task_id)
                    if await tasks.is_stopped(conn, ctx.task_id):
                        return {"status": "stopped"}
                kept = reusable(rows, sha, command, digest, role)
                if kept is not None:
                    ran[role] = kept
                    continue
                if role == "head" and not (lay.checks / SEED).is_dir() and (b.project or {}).get("setup"):
                    base_digest = ran["base"]["payload"]["digest"]
                    await suite(ctx, lay, b, b.base_sha, "base", stop, digest=base_digest, run_suite=False)
                if not await ctx.alive():
                    return {"status": "lock lost"}
                payload = await suite(ctx, lay, b, sha, role, stop, digest=digest)
                if payload["cause"] == "kernel":
                    return failed(payload["why"])
                if not await ctx.alive():
                    return {"status": "lock lost"}
                async with await db.connect(ctx.dsn) as conn, conn.transaction():
                    await ledger.lock(conn, f"task:{ctx.task_id}")
                    if await tasks.is_stopped(conn, ctx.task_id):
                        return {"status": "stopped"}
                    event_id = await ledger.append(conn, ctx.task_id, SUITE, payload)
                ran[role] = {"id": event_id, "payload": payload}
        except _Stopped:
            return {"status": "stopped"}
        finally:
            if stop is not None:
                stop.cancel()
            await listener.close()

        base, head = ran["base"], ran["head"]
        try:
            deleted = await asyncio.to_thread(removed_definitions, b.mirror, b.base_sha, candidate.sha)
        except git.GitError as exc:
            return failed(f"the diff: {exc}")
        lists = compare(base["payload"], head["payload"], deleted)
        if not await ctx.alive():
            return {"status": "lock lost"}
        try:
            async with await db.connect(ctx.dsn) as conn:
                await verdicts.record_check(
                    conn,
                    ctx.task_id,
                    Check.TEST,
                    None,
                    breadth=breadth_id,
                    command=command,
                    failures=lists["failures"],
                    deleted_at_head=lists["deleted_at_head"],
                    failing_at_base=lists["failing_at_base"],
                    suites=[base["id"], head["id"]],
                    leg="kernel",
                )
        except verdicts.VerdictRefused as exc:
            async with await db.connect(ctx.dsn) as conn:
                if await tasks.is_stopped(conn, ctx.task_id):
                    return {"status": "stopped"}
            return failed(f"verdict refused: {exc}")
        return {"status": "moved"}

    return run
