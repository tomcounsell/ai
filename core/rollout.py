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
  `*.md`. A restart whose diff or uncommitted paths touch `uv.lock` or
  `pyproject.toml` stops at `dependencies`, one touching `core/schema.sql`
  at `schema`: the kernel runs no `uv` and no `pg_dump`. A checkout the fast-forward would refuse
  stops at `fast-forward`, before anything holds.
- `fast_forward`, `step_back`: `git merge --ff-only <sha>`, and the
  `reset --keep` back to where it started when the head is still `sha`.
- `migrate_argv`, `migrate`: the merged code's own `db.migrate`, through
  the kernel's interpreter in the checkout, started through the watch.

`Restart` leaves `serve` once `rollout.restarting` is written; `python -m
core serve` then exits with status 1 and launchd starts it again.

Imports the standard library, `core.credentials`, `core.git`, and
`core.targets`.
"""

import contextlib
import os
import re
import signal
from dataclasses import dataclass, field
from pathlib import Path

from core import credentials, git, targets

REF = "refs/valor-kernel/rollout"
# Read per turn (`persona/`, `skills/`) or never read by the kernel.
QUIET_DIRS = ("persona/", "skills/", "docs/", "tests/")
DEPENDENCIES = ("uv.lock", "pyproject.toml")
SCHEMA = "core/schema.sql"
# The steps a failure at which repeats on every try: not retried by the process.
FINAL = ("dependencies", "schema", "migrate")
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
    """What prepare found. `failed` is `(step, reason)` for `target`."""

    target: Merge | None = None
    covered: list[Merge] = field(default_factory=list)
    superseded: list[Merge] = field(default_factory=list)
    restart: bool = False
    steps: list[str] = field(default_factory=list)
    failed: tuple[str, str] | None = None


def restarts(paths: list[str]) -> bool:
    """Whether a diff with these paths may change what the kernel imports
    or reads at start."""
    return any(not (p.startswith(QUIET_DIRS) or ("/" not in p and p.endswith(".md"))) for p in paths)


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
            dirty = {_path(line) for line in git.dirty(checkout)}
            stop = _stop(paths, dirty, runs)
            if stop:
                plan.failed = stop
                return plan
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


def _stop(paths: list[str], dirty: set[str], runs: str) -> tuple[str, str] | None:
    """The dependencies or schema stop for a restart into `runs`: `paths`
    changed from `started` to it, or `dirty` in the checkout, which the
    restart imports and migrate reads from the working tree."""

    def why(names: list[str]) -> str:
        said = []
        committed = [n for n in names if n in paths]
        uncommitted = [n for n in names if n in dirty and n not in paths]
        if committed:
            said.append(f"{runs} changes {' and '.join(committed)}")
        if uncommitted:
            said.append(f"the checkout has uncommitted {' and '.join(uncommitted)}")
        return "; ".join(said)

    changed = [p for p in DEPENDENCIES if p in paths or p in dirty]
    if changed:
        return "dependencies", f"{why(changed)}; the kernel runs no uv"
    if SCHEMA in paths or SCHEMA in dirty:
        return "schema", f"{why([SCHEMA])}; the kernel runs no backup"
    return None


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
    held = git.watch()
    with git.output_file() as out, git.output_file() as err:
        proc = git.start(
            migrate_argv(checkout, database), cwd=checkout, stdout=out, stderr=err, env=dict(os.environ)
        )
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
            raise RuntimeError(f"migrate exited with status {proc.returncode}: {' / '.join(tail)}")
