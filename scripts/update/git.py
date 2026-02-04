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


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
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


def stash_changes(project_dir: Path) -> bool:
    """Stash uncommitted changes. Returns True if stash was created."""
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    msg = f"remote-update auto-stash {timestamp}"

    result = run_cmd(
        ["git", "stash", "push", "-m", msg],
        cwd=project_dir,
        check=False,
    )
    return result.returncode == 0


def stash_pop(project_dir: Path) -> bool:
    """Pop stashed changes. Returns True if successful."""
    result = run_cmd(["git", "stash", "pop"], cwd=project_dir, check=False)
    return result.returncode == 0


def pull_ff_only(project_dir: Path) -> tuple[bool, str]:
    """Pull with --ff-only. Returns (success, output)."""
    result = run_cmd(
        ["git", "pull", "--ff-only"],
        cwd=project_dir,
        check=False,
    )
    output = result.stdout + result.stderr
    return result.returncode == 0, output.strip()


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

    # Check for dirty working tree
    if is_dirty(project_dir):
        stashed = True
        if not stash_changes(project_dir):
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
            stash_restored = stash_pop(project_dir)

        return GitPullResult(
            success=False,
            before_sha=before_sha,
            after_sha=before_sha,
            commit_count=0,
            commits=[],
            stashed=stashed,
            stash_restored=stash_restored,
            error=f"git pull --ff-only failed: {output}",
        )

    # Restore stash
    if stashed:
        stash_restored = stash_pop(project_dir)

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
