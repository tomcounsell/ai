"""push_branch: the `propose` action module. Plan 07 task 5; tech stack §7.

A repository token cannot be scoped to one branch, so a sandbox never holds
one. The broker performs the push outside the sandbox, from the host side of
the worktree mount, restricted to the objective's own branch: `cori/<objective
id>` and nothing else. With one Executor per leaf and the worktree keyed by
the objective id, the branch is a function of the id and needs no second
field (seams §1.10, amendment D).

The token comes from the Keychain through `broker/credentials.py`, travels
only on the git command line, and is scrubbed from every error before it can
reach a row or a traceback. It is never written into the source tree's
config, which is why the push names the URL rather than a remote.

The push carries `<head_sha>:refs/heads/<branch>` and never `--force`. A
branch that moved under the broker is the person's to read, not the broker's
to overwrite.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from broker.credentials import Credential
from broker.errors import EffectRefused
from broker.module import TargetState
from infra.secrets import read_secret
from schemas.effect import PushBranch

__all__ = [
    "BRANCH_PATTERN",
    "EffectRefused",
    "PushBranch",
    "check",
    "check_branch",
    "delete_branch",
    "model",
    "query",
    "run",
]

model = PushBranch

BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
REQUIRED_PREFIX = "cori/"
REFUSED = {"main", "master"}
SECRET = "github_token"


def branch_for(objective_id: str) -> str:
    """The one branch an objective may push."""
    return f"{REQUIRED_PREFIX}{objective_id}"


def check_branch(branch: str, objective_id: str) -> None:
    """Accept the objective's own branch and refuse every other string.

    The malformed and `main`/`master` refusals come first so the reason a
    caller reads is the specific one, but the rule the whole function states
    is the equality on the last line.
    """
    if not branch or not BRANCH_PATTERN.fullmatch(branch) or ".." in branch:
        raise EffectRefused(f"branch name {branch!r} is malformed")
    if branch in REFUSED:
        raise EffectRefused(f"push to {branch!r} is refused")
    if not branch.startswith(REQUIRED_PREFIX) or branch == REQUIRED_PREFIX:
        raise EffectRefused(
            f"push to {branch!r} is refused: an objective branch starts with "
            f"{REQUIRED_PREFIX!r}"
        )
    if branch != branch_for(objective_id):
        raise EffectRefused(
            f"push to {branch!r} is refused: objective {objective_id} owns "
            f"{branch_for(objective_id)!r} and no other branch"
        )


def _git(source_dir: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(source_dir), *args], capture_output=True, text=True
    )


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "<token>").strip()


def _url(repo: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repo}.git"


def check(action: PushBranch) -> None:
    """Everything refusable without touching GitHub: the branch is the
    objective's, and `source_dir` is a work tree whose HEAD is what the
    action named."""
    check_branch(action.branch, action.objective_id or "")
    source = Path(action.source_dir)
    if not source.is_dir():
        raise EffectRefused(f"source_dir {action.source_dir!r} is not a directory")
    inside = _git(source, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise EffectRefused(f"source_dir {action.source_dir!r} is not a git work tree")
    head = _git(source, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise EffectRefused(f"source_dir {action.source_dir!r} has no HEAD")
    if head.stdout.strip() != action.head_sha:
        raise EffectRefused(
            f"head_moved: {action.source_dir!r} is at {head.stdout.strip()}, "
            f"the action named {action.head_sha}"
        )


async def run(conn, action: PushBranch, credential: Credential) -> dict[str, Any]:
    """Push the named SHA to the objective's branch. No force, ever."""
    token = credential.value(SECRET)
    r = _git(
        action.source_dir,
        "push",
        _url(action.repo, token),
        f"{action.head_sha}:refs/heads/{action.branch}",
    )
    if r.returncode != 0:
        raise RuntimeError(_scrub(r.stderr, token))
    return {"sha": action.head_sha}


async def query(conn, action: PushBranch, credential: Credential) -> TargetState:
    """What GitHub says about the branch right now."""
    token = credential.value(SECRET)
    r = _git(
        action.source_dir,
        "ls-remote",
        _url(action.repo, token),
        f"refs/heads/{action.branch}",
    )
    if r.returncode != 0:
        return "unreachable"
    line = r.stdout.strip()
    if not line:
        return "absent"
    return "present" if line.split()[0] == action.head_sha else "differs"


def delete_branch(action: PushBranch, token: str | None = None) -> None:
    """Remove a branch the broker pushed. Kept for the live tests; nothing in
    the M0 protocol deletes a branch."""
    check_branch(action.branch, action.objective_id or "")
    token = token or read_secret(SECRET)
    r = _git(
        action.source_dir,
        "push",
        _url(action.repo, token),
        "--delete",
        f"refs/heads/{action.branch}",
    )
    if r.returncode != 0:
        raise RuntimeError(_scrub(r.stderr, token))
