"""Git operations for update system."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitPullResult:
    """Result of a git pull operation."""

    success: bool
    before_sha: str
    after_sha: str
    commit_count: int
    commits: list[str]  # One-line summaries
    stashed: bool
    stash_restored: bool
    error: str | None = None


@dataclass
class UpgradePendingInfo:
    """Info about pending critical dependency upgrades."""

    pending: bool
    timestamp: str | None = None
    reason: str | None = None


def run_cmd(
    cmd: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def get_current_sha(project_dir: Path) -> str:
    """Get current HEAD SHA."""
    result = run_cmd(["git", "rev-parse", "HEAD"], cwd=project_dir)
    return result.stdout.strip()


def get_short_sha(project_dir: Path, sha: str = "HEAD") -> str:
    """Get short SHA."""
    result = run_cmd(["git", "rev-parse", "--short", sha], cwd=project_dir)
    return result.stdout.strip()


def is_dirty(project_dir: Path) -> bool:
    """Check if working tree has uncommitted changes."""
    result = run_cmd(["git", "status", "--porcelain"], cwd=project_dir)
    return bool(result.stdout.strip())


def get_dirty_files(project_dir: Path, limit: int = 5) -> list[str]:
    """Get list of dirty files."""
    result = run_cmd(["git", "status", "--porcelain"], cwd=project_dir)
    lines = result.stdout.strip().split("\n")
    return [line.strip() for line in lines[:limit] if line.strip()]


def stash_changes(project_dir: Path) -> str | None:
    """Stash uncommitted changes. Returns the stash commit sha, or ``None``.

    The sha is the durable handle: ``refs/stash`` is a per-*repository* stack
    shared by every worktree, so ``stash@{0}`` names whatever was pushed most
    recently by anyone. Returning the sha lets :func:`stash_pop` restore *our*
    entry rather than a peer lane's (issue #2650, shape 1).
    """
    from bridge.utc import utc_now

    timestamp = utc_now().strftime("%Y%m%d-%H%M%S")
    msg = f"remote-update auto-stash {timestamp}"

    result = run_cmd(
        ["git", "stash", "push", "-m", msg],
        cwd=project_dir,
        check=False,
    )
    if result.returncode != 0:
        return None

    sha = run_cmd(["git", "rev-parse", "refs/stash"], cwd=project_dir, check=False)
    if sha.returncode != 0:
        return None
    return sha.stdout.strip() or None


def _resolve_stash_ref(project_dir: Path, stash_sha: str) -> str | None:
    """Find the current ``stash@{N}`` position of a stash commit, by sha.

    Positions shift whenever any worktree of the repository pushes or drops a
    stash, so a position is only valid at the instant it is read.
    """
    listing = run_cmd(["git", "stash", "list", "--format=%H %gd"], cwd=project_dir, check=False)
    if listing.returncode != 0:
        return None
    for line in listing.stdout.splitlines():
        sha, _, name = line.strip().partition(" ")
        if sha == stash_sha and name:
            return name
    return None


def stash_pop(project_dir: Path, stash_sha: str | None = None) -> bool:
    """Restore a stash by commit sha. Returns True if successful.

    **Never pops ``stash@{0}`` (issue #2650, shape 1).** The stash stack is
    per-repository, not per-worktree, so between our push and our pop a
    concurrent lane's ``git stash`` becomes ``stash@{0}`` — and popping it
    would restore that lane's uncommitted work into this checkout while
    leaving ours buried. Identity, not position, is what makes the restore
    correct.

    The sha is resolved to its stack position at pop time (positions shift as
    peers push and pop), then dropped explicitly, because ``git stash pop``
    only accepts a stack reference.
    """
    if not stash_sha:
        return False

    ref = _resolve_stash_ref(project_dir, stash_sha)
    if ref is None:
        # Our entry is gone (already restored, or dropped by someone else).
        # Applying nothing beats applying a stranger's work.
        return False

    applied = run_cmd(["git", "stash", "apply", ref], cwd=project_dir, check=False)
    if applied.returncode != 0:
        return False

    # Re-resolve before dropping. `apply` does not shift the stack, but a peer
    # lane's push between the two calls does, and dropping a stale position
    # would discard their entry instead of ours.
    ref = _resolve_stash_ref(project_dir, stash_sha)
    if ref is not None:
        run_cmd(["git", "stash", "drop", ref], cwd=project_dir, check=False)
    return True


def get_upstream_ref(project_dir: Path) -> str | None:
    """Return the current branch's upstream remote-tracking ref (``origin/main``).

    ``None`` when the branch has no upstream configured (detached HEAD, or a
    local-only branch), which callers surface as a pull failure.
    """
    result = run_cmd(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=project_dir,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def pull_ff_only(project_dir: Path) -> tuple[bool, str]:
    """Fast-forward the current branch to its upstream, rebasing on divergence.

    Returns (success, output).

    **Never uses bare ``git pull`` (issue #2650, shape 2).** ``git pull``'s
    merge step resolves its target through ``.git/FETCH_HEAD`` — a file shared
    by every worktree of a repository, not per-worktree state. This machine
    runs concurrent SDLC lanes across many worktrees of one repo, so a peer
    lane's fetch lands between our fetch and our merge, and the merge reads
    the peer's refs instead of ours::

        fatal: Cannot fast-forward to multiple branches

    That failure hit three consecutive ``/update`` attempts on 2026-08-07 and
    is pure collateral: nothing is wrong with either lane's work.

    Fetching and then merging the *remote-tracking ref* by name removes the
    race outright. ``refs/remotes/origin/main`` is a named ref that a peer's
    fetch of the same branch can only advance to the same value, so a
    concurrent fetch is at worst a no-op for us and never a wrong target.
    """
    upstream = get_upstream_ref(project_dir)
    if not upstream:
        return False, "no upstream tracking branch configured for the current branch"

    remote, _, branch = upstream.partition("/")
    if not remote or not branch:
        return False, f"could not parse upstream ref: {upstream!r}"

    fetch = run_cmd(["git", "fetch", remote, branch], cwd=project_dir, check=False)
    if fetch.returncode != 0:
        return False, (fetch.stdout + fetch.stderr).strip()

    # Merge the named remote-tracking ref, NOT FETCH_HEAD.
    result = run_cmd(["git", "merge", "--ff-only", upstream], cwd=project_dir, check=False)
    output = result.stdout + result.stderr

    if result.returncode == 0:
        return True, output.strip()

    # If ff-only failed due to divergence, rebase onto the same named ref.
    if "diverging" in output.lower() or "not possible to fast-forward" in output.lower():
        result = run_cmd(["git", "rebase", upstream], cwd=project_dir, check=False)
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()

    return False, output.strip()


def get_commits_between(project_dir: Path, before: str, after: str) -> list[str]:
    """Get one-line commit summaries between two SHAs."""
    result = run_cmd(
        ["git", "log", "--oneline", f"{before}..{after}"],
        cwd=project_dir,
    )
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]


def get_changed_files(project_dir: Path, before: str, after: str) -> list[str]:
    """Get list of files changed between two SHAs."""
    result = run_cmd(
        ["git", "diff", "--name-only", f"{before}..{after}"],
        cwd=project_dir,
    )
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]


def check_critical_dep_changes(project_dir: Path, before: str, after: str) -> list[str]:
    """Check if critical dependencies changed. Returns list of changes."""
    result = run_cmd(
        ["git", "diff", f"{before}..{after}", "--", "pyproject.toml"],
        cwd=project_dir,
    )

    changes = []
    for line in result.stdout.split("\n"):
        # Look for added lines with pinned critical deps
        if line.startswith("+") and "==" in line:
            if any(dep in line for dep in ["telethon", "anthropic", "claude-agent-sdk"]):
                changes.append(line.strip())

    return changes


def git_pull(project_dir: Path) -> GitPullResult:
    """
    Perform git pull with automatic stash/unstash.

    Returns GitPullResult with all details.
    """
    before_sha = get_current_sha(project_dir)
    stashed = False
    stash_restored = False
    stash_sha: str | None = None

    # Ensure pre-commit secret scanning hook is active
    run_cmd(["git", "config", "core.hooksPath", ".githooks"], cwd=project_dir, check=False)

    # Check for dirty working tree
    if is_dirty(project_dir):
        stashed = True
        stash_sha = stash_changes(project_dir)
        if not stash_sha:
            return GitPullResult(
                success=False,
                before_sha=before_sha,
                after_sha=before_sha,
                commit_count=0,
                commits=[],
                stashed=True,
                stash_restored=False,
                error="Failed to stash changes",
            )

    # Pull
    success, output = pull_ff_only(project_dir)

    if not success:
        # Restore stash if we stashed
        if stashed:
            stash_restored = stash_pop(project_dir, stash_sha)

        return GitPullResult(
            success=False,
            before_sha=before_sha,
            after_sha=before_sha,
            commit_count=0,
            commits=[],
            stashed=stashed,
            stash_restored=stash_restored,
            error=f"fast-forward to upstream failed: {output}",
        )

    # Restore stash
    if stashed:
        stash_restored = stash_pop(project_dir, stash_sha)

    after_sha = get_current_sha(project_dir)

    # Get commit info
    if before_sha == after_sha:
        commits = []
        commit_count = 0
    else:
        commits = get_commits_between(project_dir, before_sha, after_sha)
        commit_count = len(commits)

    return GitPullResult(
        success=True,
        before_sha=before_sha,
        after_sha=after_sha,
        commit_count=commit_count,
        commits=commits,
        stashed=stashed,
        stash_restored=stash_restored,
    )


def check_upgrade_pending(project_dir: Path) -> UpgradePendingInfo:
    """Check if there's a pending critical dependency upgrade."""
    flag_file = project_dir / "data" / "upgrade-pending"

    if not flag_file.exists():
        return UpgradePendingInfo(pending=False)

    content = flag_file.read_text().strip()
    parts = content.split(" ", 1)
    timestamp = parts[0] if parts else None
    reason = parts[1] if len(parts) > 1 else None

    return UpgradePendingInfo(
        pending=True,
        timestamp=timestamp,
        reason=reason,
    )


def clear_upgrade_pending(project_dir: Path) -> None:
    """Remove the upgrade-pending flag."""
    flag_file = project_dir / "data" / "upgrade-pending"
    flag_file.unlink(missing_ok=True)


def set_upgrade_pending(project_dir: Path, reason: str) -> None:
    """Set the upgrade-pending flag."""
    import datetime

    flag_file = project_dir / "data" / "upgrade-pending"
    flag_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    flag_file.write_text(f"{timestamp} {reason}\n")


def set_restart_requested(project_dir: Path, commit_count: int) -> None:
    """Set the restart-requested flag for graceful bridge restart."""
    import datetime

    flag_file = project_dir / "data" / "restart-requested"
    flag_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    flag_file.write_text(f"{timestamp} {commit_count} commit(s)\n")
