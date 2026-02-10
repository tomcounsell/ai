"""Git worktree manager for session filesystem isolation.

Creates and manages git worktrees for isolated coding sessions.
Each work item gets its own worktree under .worktrees/{slug}/.
"""

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

WORKTREES_DIR = ".worktrees"


def create_worktree(repo_root: Path, slug: str, base_branch: str = "main") -> Path:
    """Create a git worktree for a work item.

    Args:
        repo_root: Path to the main repository
        slug: Work item slug (used for directory name and branch)
        base_branch: Branch to base the worktree on

    Returns:
        Path to the created worktree directory

    Raises:
        subprocess.CalledProcessError: If git worktree creation fails
    """
    worktree_dir = repo_root / WORKTREES_DIR / slug
    branch_name = f"session/{slug}"

    if worktree_dir.exists():
        logger.info(f"Worktree already exists: {worktree_dir}")
        return worktree_dir

    # Ensure .worktrees/ parent exists
    (repo_root / WORKTREES_DIR).mkdir(exist_ok=True)

    # Create worktree with new branch based on base_branch
    subprocess.run(
        ["git", "worktree", "add", str(worktree_dir), "-b", branch_name, base_branch],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    # Copy settings.local.json if it exists (not tracked by git)
    local_settings = repo_root / ".claude" / "settings.local.json"
    if local_settings.exists():
        target_dir = worktree_dir / ".claude"
        target_dir.mkdir(exist_ok=True)
        shutil.copy2(local_settings, target_dir / "settings.local.json")

    logger.info(f"Created worktree: {worktree_dir} (branch: {branch_name})")
    return worktree_dir


def remove_worktree(repo_root: Path, slug: str, delete_branch: bool = True) -> bool:
    """Remove a git worktree and optionally its branch.

    Args:
        repo_root: Path to the main repository
        slug: Work item slug
        delete_branch: Whether to also delete the session branch

    Returns:
        True if successfully removed, False otherwise
    """
    worktree_dir = repo_root / WORKTREES_DIR / slug
    branch_name = f"session/{slug}"

    if not worktree_dir.exists():
        logger.info(f"Worktree not found: {worktree_dir}")
        return False

    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_dir)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        logger.info(f"Removed worktree: {worktree_dir}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to remove worktree {worktree_dir}: {e.stderr}")
        return False

    if delete_branch:
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=repo_root,
            capture_output=True,
            timeout=10,
        )
        logger.info(f"Deleted branch: {branch_name}")

    return True


def list_worktrees(repo_root: Path) -> list[dict]:
    """List all worktrees under .worktrees/.

    Returns:
        List of dicts with 'slug', 'path', 'branch' keys
    """
    worktrees_dir = repo_root / WORKTREES_DIR
    if not worktrees_dir.exists():
        return []

    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )

    worktrees = []
    current: dict = {}
    for line in result.stdout.strip().split("\n"):
        if line.startswith("worktree "):
            if current and WORKTREES_DIR in current.get("path", ""):
                worktrees.append(current)
            current = {"path": line.split(" ", 1)[1]}
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1]
            # Extract slug from path
            path = Path(current["path"])
            current["slug"] = path.name

    if current and WORKTREES_DIR in current.get("path", ""):
        worktrees.append(current)

    return worktrees


def prune_worktrees(repo_root: Path) -> None:
    """Prune stale worktree references (e.g., after manual directory deletion)."""
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo_root,
        capture_output=True,
        timeout=10,
    )
    logger.info("Pruned stale worktree references")
