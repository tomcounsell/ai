"""Rolling the running kernel forward to its own merges.

A merge is the kernel's own when its `effect.outcome` (kind `done`,
action type `merge`) names the push URL and the branch of the checkout the
kernel runs from. The kernel reads those from the checkout's own config,
never from a task's workspace. The commit rolled is the `sha` the outcome
recorded, nothing else.

The git work here is synchronous and runs off the loop (`git.threaded`,
called by `core.serve.Kernel`); the rows are written on the loop.

- `judge`: which of the merge outcomes are the kernel's and not yet in the
  commit the process started from.
- `prepare`: fetch the branch into `refs/valor-kernel/rollout`, supersede
  every due merge that is not on it, choose the one to roll, and tell
  whether it restarts the kernel. The diff classified runs from `started`
  to the commit the restart runs (`runs_at`: the merged sha, or the
  checkout's head when it is already past it); a restart is due when it
  reaches past `persona/`, `skills/`, `docs/`, `tests/` and top-level
  `*.md`. A restart whose diff touches `uv.lock` or `pyproject.toml` also
  syncs the dependencies (`plan.sync`), and one touching `core/schema.sql`
  backs up the database first (`plan.backup`). A restart while the
  checkout holds an uncommitted `uv.lock`, `pyproject.toml`, or
  `core/schema.sql` stops at `uncommitted`: the restart would run that
  edit, which nothing merged. A checkout the fast-forward would refuse
  stops at `fast-forward`, before anything holds.
- `fast_forward`, `step_back`: `git merge --ff-only <sha>`, and the
  `reset --keep` back to where it started when the head is still `sha`.
- `sync_argv`, `sync`: `uv sync --frozen` in the checkout, with a PATH of
  system directories only and no Python download, so it keeps the
  checkout's `.venv` interpreter and finds none a turn could leave on a
  PATH; started through the watch.
- `migrate_argv`, `migrate`: the merged code's own `db.migrate`, through
  the kernel's interpreter in the checkout, started through the watch.

Every failed step is tried again on a later `serve_tick_s` wake; the
merge's notice, a report of what failed, goes out once it has failed
`NOTICE_AFTER` times in one process.

`Restart` leaves `serve` once `rollout.restarting` is written; `python -m
core serve` then exits with status 1 and launchd starts it again.

The bridges are launchd jobs of their own (`BRIDGES`), and each names
its lock connection with the commit it started at (`core.bridge.serve`).
A kernel start that ends a rollout restarts every bridge holding its lock
whose code the running commit changed (`core.serve.restart_bridges`),
after migrate and before the `rollout.ended` rows, which name the bridges
restarted and any that failed to:

- `closure`: every file a bridge can import, by walking the imports of
  `bridges/<name>/` through `core/` and `bridges/`, function-level and
  relative ones too, with each package's `__init__.py`.
- `reaches`: whether a diff changes a file of the closure, anything under
  `bridges/<name>/`, or the dependencies. A diff of only `persona/`,
  `skills/`, `docs/`, `tests/` and top-level `*.md` reaches none.
- `stale`: which live bridges run code the running commit changed where
  it reaches them, the bridge's commit behind or ahead of it. One with no
  stamp (started before bridges stamped their head), or whose stamp git
  cannot diff, is stale; one stamped `NO_HEAD` (it could not read its
  head) is not, since a kick would bring it back the same.
- `kick`: `launchctl kickstart -k gui/<uid>/<label>`, waiting at most
  `KICK_TIMEOUT_S`, never for the bridge to come back. A bridge reconciles
  its outbox on start, so a send cut off by the kill is looked up before
  it is tried again. A failed kick is tried again only at the next start
  that ends a rollout. The kick restarts launchd's instance of the label:
  a bridge started by hand that holds the lock is not replaced.

Imports the standard library, `core.binaries`, `core.credentials`,
`core.git`, and `core.targets`.
"""

import ast
import contextlib
import os
import re
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core import binaries, credentials, git, targets

REF = "refs/valor-kernel/rollout"
# Each bridge's launchd job, by channel: `LABEL` in `bridges/<name>/__main__.py`.
BRIDGES = {
    "telegram": "com.valor.kernel.telegram",
    "slack": "com.valor.kernel.slack",
    "email": "com.valor.email",
    "local": "com.valor.kernel.local",
}
# The stamp of a bridge that could not read its own head: `core.bridge.NO_HEAD`.
NO_HEAD = "none"
KICK_TIMEOUT_S = 10.0
# Read per turn (`persona/`, `skills/`) or never read by the kernel.
QUIET_DIRS = ("persona/", "skills/", "docs/", "tests/")
DEPENDENCIES = ("uv.lock", "pyproject.toml")
SCHEMA = "core/schema.sql"
# Failures of one merge's rollout, in one process, before its notice.
NOTICE_AFTER = 3
_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")


class Restart(Exception):  # not an error: the kernel's exit to run the merged code
    def __init__(self, sha: str):
        super().__init__(f"kernel restarting for {sha}")
        self.sha = sha


@dataclass(frozen=True)
class Merge:
    """A kernel merge outcome: its effect, task, row, and result."""

    effect_id: str
    task_id: str
    row: int
    remote: str
    branch: str
    sha: str

    @classmethod
    def of(cls, row: int, task_id: str, effect_id: str, result: dict) -> Merge | None:
        remote, branch, sha = (
            (result or {}).get("remote"),
            (result or {}).get("branch"),
            (result or {}).get("sha"),
        )
        if not (isinstance(remote, str) and isinstance(branch, str) and isinstance(sha, str)):
            return None
        if not _SHA.fullmatch(sha):
            return None
        return cls(effect_id, task_id, row, remote, branch, sha)


@dataclass
class Plan:
    """What prepare found. `failed` is `(step, reason)` for `target`;
    `sync` and `backup` say the restart syncs the dependencies and backs up
    the database first."""

    target: Merge | None = None
    covered: list[Merge] = field(default_factory=list)
    superseded: list[Merge] = field(default_factory=list)
    restart: bool = False
    sync: bool = False
    backup: bool = False
    steps: list[str] = field(default_factory=list)
    failed: tuple[str, str] | None = None


def restarts(paths: list[str]) -> bool:
    """Whether a diff with these paths may change what the kernel imports
    or reads at start."""
    return any(not (p.startswith(QUIET_DIRS) or ("/" not in p and p.endswith(".md"))) for p in paths)


def closure(checkout: Path, name: str) -> set[str]:
    """Every file of the checkout the bridge `name` can import: each `.py`
    under `bridges/<name>/` and `bridges/__init__.py`, then every `core/`
    or `bridges/` module any of them imports, anywhere in the file, with
    the `__init__.py` of each package on its path, transitively. Paths are
    relative to the checkout."""
    todo = [p.relative_to(checkout).as_posix() for p in sorted((checkout / "bridges" / name).rglob("*.py"))]
    todo.append("bridges/__init__.py")
    files: set[str] = set()
    while todo:
        path = todo.pop()
        if path in files or not (checkout / path).is_file():
            continue
        files.add(path)
        try:
            tree = ast.parse((checkout / path).read_bytes(), path)
        except SyntaxError, ValueError:
            continue
        package = path.split("/")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                within = package[: len(package) - node.level + 1] if node.level else []
                base = ".".join([*within, *([node.module] if node.module else [])])
                modules = [base, *(f"{base}.{a.name}" for a in node.names)]
            else:
                continue
            for module in modules:
                todo.extend(_module_files(checkout, module))
    return files


def _module_files(checkout: Path, module: str) -> list[str]:
    """The files importing `module` runs: each package's `__init__.py` on
    its path and the module's own file, those in `core/` or `bridges/`."""
    parts = [p for p in module.split(".") if p]
    if not parts or parts[0] not in ("core", "bridges"):
        return []
    found = []
    for i in range(1, len(parts) + 1):
        stem = "/".join(parts[:i])
        found += [f for f in (f"{stem}/__init__.py", f"{stem}.py") if (checkout / f).is_file()]
    return found


def reaches(paths: list[str], files: set[str], name: str) -> bool:
    """Whether a diff with these paths changes code the bridge `name` runs:
    a file of its `closure`, anything under `bridges/<name>/`, or the
    dependencies."""
    return any(p in files or p.startswith(f"bridges/{name}/") or p in DEPENDENCIES for p in paths)


def stale(checkout: Path, running: str, live: list[tuple[str, str | None]]) -> set[str]:
    """The channels among the live bridges, `(channel, stamp)`, whose code
    differs from `running` where it reaches them."""
    out: set[str] = set()
    for channel, stamp in live:
        if stamp in (NO_HEAD, running):
            continue
        if stamp is None or not _SHA.fullmatch(stamp):
            out.add(channel)
            continue
        try:
            paths = git.diff_paths(checkout, stamp, running)
        except git.GitError:
            out.add(channel)
            continue
        if reaches(paths, closure(checkout, channel), channel):
            out.add(channel)
    return out


def kick_argv(label: str) -> list[str]:
    return [binaries.require(binaries.LAUNCHCTL), "kickstart", "-k", f"gui/{os.getuid()}/{label}"]


def kick(label: str) -> str | None:
    """Restart the launchd job `label` (`launchctl kickstart -k`), without
    waiting for it to come back; why it failed, or None."""
    try:
        done = subprocess.run(
            kick_argv(label), capture_output=True, text=True, timeout=KICK_TIMEOUT_S,
            env={"PATH": git.PATH}, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return f"launchctl kickstart did not return in {KICK_TIMEOUT_S:g} s"
    except (OSError, binaries.Untrusted) as exc:
        return str(exc)
    if done.returncode != 0:
        tail = " / ".join(done.stderr.strip().splitlines()[-3:])
        return f"launchctl kickstart exited with status {done.returncode}: {tail}"
    return None


def judge(checkout: Path, started: str, merges: list[Merge]) -> tuple[list[Merge], list[str]]:
    """The due merges (the checkout's push URL and branch, not contained in
    `started`), and the effect ids judged not due for this process. Raises
    `GitError` when the checkout's config is refused or cannot be read."""
    url, branch = git.push_url(checkout), git.branch(checkout)
    due, judged = [], []
    for m in merges:
        if m.remote != url or m.branch != branch or git.is_ancestor(checkout, m.sha, started):
            judged.append(m.effect_id)
        else:
            due.append(m)
    return due, judged


def fetch(checkout: Path, url: str, branch: str, credential: str | Path | None) -> None:
    """The branch's tip into `REF`, with the GitHub credential for a
    non-local remote only."""

    def call(header: Path | None) -> None:
        git.out(
            checkout,
            "fetch", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
            "--upload-pack=git-upload-pack", url, f"+refs/heads/{branch}:{REF}",
            credential=header,
            url=url if header is not None else None,
        )  # fmt: skip

    if credential is None or targets.local(url):
        call(None)
    else:
        with credentials.header_file(credential, url) as header:
            call(header)


def prepare(checkout: Path, started: str, due: list[Merge], credential: str | Path | None) -> Plan:
    """Fetch, choose, classify, and check the fast-forward can succeed.
    `due` is oldest first and non-empty."""
    plan = Plan(target=due[-1], steps=["fetch"])
    try:
        fetch(checkout, due[-1].remote, due[-1].branch, credential)
        on = []
        for m in due:
            (on if git.is_ancestor(checkout, m.sha, REF) else plan.superseded).append(m)
        if not on:
            plan.target = None
            return plan
        plan.target = next(
            (m for m in reversed(on) if all(o is m or git.is_ancestor(checkout, o.sha, m.sha) for o in on)),
            on[-1],
        )
        sha = plan.target.sha
        plan.covered = [m for m in on if m is not plan.target and git.is_ancestor(checkout, m.sha, sha)]
        plan.steps.append("restart class")
        runs = runs_at(checkout, sha)
        paths = git.diff_paths(checkout, started, runs)
        plan.restart = restarts(paths)
        if plan.restart:
            dirty = sorted(
                p for p in (_path(line) for line in git.dirty(checkout)) if p in (*DEPENDENCIES, SCHEMA)
            )
            if dirty:
                plan.failed = (
                    "uncommitted",
                    f"the checkout has uncommitted {' and '.join(dirty)}, which the restart would run",
                )
                return plan
            plan.sync = any(p in paths for p in DEPENDENCIES)
            plan.backup = SCHEMA in paths
        said = refused(checkout, sha)
        if said:
            plan.failed = ("fast-forward", said)
    except (git.GitError, credentials.CredentialError) as exc:
        plan.failed = (plan.steps[-1], str(exc))
    return plan


def runs_at(checkout: Path, sha: str) -> str:
    """The commit a restart for `sha` runs: the checkout's head when it is
    `sha` or past it (the fast-forward is then a no-op), else `sha`."""
    head = git.head(checkout)
    if head is not None and (head == sha or git.is_ancestor(checkout, sha, head)):
        return head
    return sha


def refused(checkout: Path, sha: str) -> str | None:
    """Why `git merge --ff-only <sha>` would refuse in the checkout now, or
    None: the head is `sha` or past it, or behind it with no uncommitted or
    untracked path among those the merge changes."""
    head = git.head(checkout)
    if head is None:
        return "the checkout has no commit"
    if head == sha or git.is_ancestor(checkout, sha, head):
        return None
    if not git.is_ancestor(checkout, head, sha):
        return f"the checkout at {head} has diverged from {sha}"
    changed = set(git.diff_paths(checkout, head, sha))
    clash = sorted(p for p in (_path(line) for line in git.dirty(checkout)) if p in changed)
    if clash:
        return f"the checkout has uncommitted changes the merge would overwrite: {', '.join(clash)}"
    return None


def _path(line: str) -> str:
    """The path of a `status --porcelain` line, unquoted when git quoted it."""
    path = line[3:]
    return path[1:-1] if path.startswith('"') and path.endswith('"') else path


def fast_forward(checkout: Path, sha: str) -> tuple[str | None, str | None]:
    """Fast-forward the checkout to `sha`; returns the head before and after."""
    before = git.head(checkout)
    git.out(checkout, "merge", "--ff-only", "--quiet", sha)
    return before, git.head(checkout)


def step_back(checkout: Path, before: str | None, sha: str) -> bool:
    """After a failed migrate: `reset --keep` to `before` only when the
    head is still `sha` and the fast-forward moved it. Returns whether the
    checkout is left on a commit other than `before` (mixed code)."""
    now = git.head(checkout)
    if now != sha or before in (None, sha):
        return now != before
    return git.run(checkout, "reset", "--keep", before).returncode != 0


def migrate_argv(checkout: Path, database: str) -> list[str]:
    """The merged code's `db.migrate`, alone (not `python -m core migrate`,
    which also runs `secure-login`), by the kernel's own interpreter."""
    return [
        str(checkout / ".venv" / "bin" / "python"),
        "-c",
        "import sys; from core import db; db.migrate(sys.argv[1])",
        database,
    ]


def migrate(checkout: Path, database: str) -> None:
    """Run `migrate_argv` in the checkout through the watch (`git.start`),
    so cancelling the caller kills it; raises with its stderr on failure."""
    _run(checkout, migrate_argv(checkout, database), dict(os.environ), "migrate")


def sync_argv(uv: str | Path) -> list[str]:
    return [str(uv), "sync", "--frozen"]


def sync_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PYTHON")}
    return env | {"PATH": git.PATH, "UV_PYTHON_DOWNLOADS": "never"}


def sync(checkout: Path, uv: str | Path) -> None:
    """Run `sync_argv` in the checkout through the watch, as `migrate` runs."""
    _run(checkout, sync_argv(uv), sync_env(), "uv sync")


def _run(checkout: Path, argv: list[str], env: dict[str, str], name: str) -> None:
    held = git.watch()
    with git.output_file() as out, git.output_file() as err:
        proc = git.start(argv, cwd=checkout, stdout=out, stderr=err, env=env)
        try:
            proc.wait()
        except BaseException:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
        finally:
            if held is not None:
                held.finished(proc)
        if held is not None and held.interrupted:
            raise git.Interrupted()
        if proc.returncode != 0:
            tail = git.read_output(err, True).strip().splitlines()[-5:]
            raise RuntimeError(f"{name} exited with status {proc.returncode}: {' / '.join(tail)}")
