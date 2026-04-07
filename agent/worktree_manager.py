"""Git worktree manager for session filesystem isolation.

Creates and manages git worktrees for isolated coding sessions.
Each work item gets its own worktree under .worktrees/{slug}/.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

WORKTREES_DIR = ".worktrees"
VALID_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def resolve_repo_root(file_path: str | Path) -> Path:
    """Determine which git repository a file belongs to.

    Used by the /do-build skill to detect when a plan document lives in a
    different repo than the orchestrator (ai repo). The plan path is the
    source of truth for which repo should receive the worktree, branch,
    and PR.

    Runs ``git rev-parse --show-toplevel`` from the file's parent directory
    to find the enclosing git repository root.

    Args:
        file_path: Absolute or relative path to a file (e.g., a plan doc).
            If a directory is given, it is used directly. If a file is given,
            its parent directory is used.

    Returns:
        Absolute Path to the git repository root containing the file.

    Raises:
        FileNotFoundError: If the file or its parent directory does not exist.
        ValueError: If the path is not inside any git repository.
    """
    path = Path(file_path).resolve()

    # Use the directory containing the file, or the path itself if it's a dir
    search_dir = path if path.is_dir() else path.parent

    if not search_dir.exists():
        raise FileNotFoundError(f"Cannot resolve repo root: directory {search_dir} does not exist")

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=search_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.returncode != 0:
        raise ValueError(
            f"Path {file_path} is not inside a git repository. git error: {result.stderr.strip()}"
        )

    return Path(result.stdout.strip())


def _validate_slug(slug: str) -> None:
    """Validate slug to prevent path traversal and invalid directory names.

    Raises:
        ValueError: If the slug is invalid.
    """
    if not slug or not VALID_SLUG_RE.match(slug) or ".." in slug:
        raise ValueError(
            f"Invalid slug: {slug!r}. "
            "Slugs must be alphanumeric (with .-_ allowed) and cannot contain '..'."
        )


def _branch_exists(repo_root: Path, branch_name: str) -> bool:
    """Check if a git branch exists locally."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        cwd=repo_root,
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def _find_worktree_for_branch(repo_root: Path, branch_name: str) -> str | None:
    """Find if a branch is already associated with a git worktree.

    Parses ``git worktree list --porcelain`` to check whether *branch_name*
    (e.g. ``session/my-feature``) is checked out in any existing worktree.

    Args:
        repo_root: Path to the main repository.
        branch_name: Full branch name to search for (e.g. ``session/slug``).

    Returns:
        The worktree path as a string if found, or ``None`` if the branch
        is not associated with any worktree.
    """
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return None

    # Porcelain format: blocks separated by blank lines, each block has
    # "worktree <path>" and "branch refs/heads/<name>" lines.
    current_path: str | None = None
    full_ref = f"refs/heads/{branch_name}"
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line.split(" ", 1)[1]
        elif line.startswith("branch ") and current_path is not None:
            if line.split(" ", 1)[1] == full_ref:
                return current_path
    return None


def _cleanup_stale_worktree(repo_root: Path, branch_name: str, worktree_path: str) -> None:
    """Remove a stale worktree that is blocking branch checkout.

    Handles two cases:
    1. Worktree directory is missing but git still tracks it -- ``git worktree
       prune`` cleans the reference.
    2. Worktree directory exists but is stale (leftover from a crashed session)
       -- ``git worktree remove --force`` removes it.

    Args:
        repo_root: Path to the main repository.
        branch_name: The branch name locked by the stale worktree.
        worktree_path: Path string from ``git worktree list``.
    """
    wt = Path(worktree_path)

    if not wt.exists():
        # Directory is gone but git still references it -- prune fixes this.
        logger.warning(
            f"Stale worktree reference for branch {branch_name} "
            f"at {worktree_path} (directory missing). Pruning."
        )
        prune_worktrees(repo_root)
        return

    # Directory exists -- force-remove the worktree.
    logger.warning(
        f"Stale worktree for branch {branch_name} at {worktree_path}. "
        "Force-removing to unblock checkout."
    )
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        logger.info(f"Removed stale worktree: {worktree_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to remove stale worktree {worktree_path}: {e.stderr}")
        # As a last resort, prune and manually remove the directory.
        prune_worktrees(repo_root)
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
            logger.info(f"Manually removed stale worktree directory: {worktree_path}")
            # Prune again to clean up the now-missing reference.
            prune_worktrees(repo_root)


def create_worktree(repo_root: Path, slug: str, base_branch: str = "main") -> Path:
    """Create a git worktree for a work item.

    Handles stale worktrees automatically: if a previous session left a
    worktree referencing the target branch (``session/{slug}``), it is
    detected, cleaned up, and creation proceeds. This makes the function
    resilient to crashed or abandoned sessions.

    Recovery cases handled:
    - Worktree directory exists and is valid: returns existing path (no-op).
    - Worktree directory is gone but git still tracks it: prunes the stale
      reference, then creates a fresh worktree.
    - Worktree directory exists at a *different* path for the same branch:
      force-removes the stale worktree, then creates at the expected path.

    Args:
        repo_root: Path to the main repository
        slug: Work item slug (used for directory name and branch)
        base_branch: Branch to base the worktree on

    Returns:
        Path to the created worktree directory

    Raises:
        ValueError: If slug contains path traversal or invalid characters
        subprocess.CalledProcessError: If git worktree creation fails after
            recovery attempts
    """
    _validate_slug(slug)

    worktree_dir = repo_root / WORKTREES_DIR / slug
    branch_name = f"session/{slug}"

    if worktree_dir.exists():
        logger.info(f"Worktree already exists: {worktree_dir}")
        return worktree_dir

    # Ensure .worktrees/ parent exists
    (repo_root / WORKTREES_DIR).mkdir(exist_ok=True)

    # Check if the branch is already locked by a stale worktree. This is the
    # core fix for issue #237: a previous session may have created a worktree
    # that was never cleaned up, blocking checkout of the same branch.
    existing_wt = _find_worktree_for_branch(repo_root, branch_name)
    if existing_wt is not None:
        expected = str(worktree_dir)
        if existing_wt != expected:
            # Branch is locked by a worktree at a different path -- stale.
            _cleanup_stale_worktree(repo_root, branch_name, existing_wt)
        else:
            # Git thinks the worktree exists at the expected path but the
            # directory is missing (we checked above). Prune the reference.
            logger.warning(
                f"Git tracks worktree at {existing_wt} but directory is missing. "
                "Pruning stale reference."
            )
            prune_worktrees(repo_root)

    # If the branch already exists (e.g., from a previous session), reuse it
    if _branch_exists(repo_root, branch_name):
        cmd = ["git", "worktree", "add", str(worktree_dir), branch_name]
    else:
        cmd = [
            "git",
            "worktree",
            "add",
            str(worktree_dir),
            "-b",
            branch_name,
            base_branch,
        ]

    subprocess.run(
        cmd,
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


def get_or_create_worktree(repo_root: Path, slug: str, base_branch: str = "main") -> Path:
    """Return an existing worktree path or create a new one.

    This is the preferred entry point for the ``/do-build`` skill and any
    code that needs a worktree for a given slug.  It is intentionally
    idempotent: calling it when a worktree already exists is a no-op that
    returns the existing path, and calling it when no worktree exists
    creates one from scratch.

    This function exists to make the "give me a worktree, I don't care if
    it already exists" pattern explicit and self-documenting.  Under the
    hood it delegates entirely to :func:`create_worktree`, which already
    handles the resume-existing case (returns early when the directory is
    present) as well as stale-worktree cleanup.

    Args:
        repo_root: Path to the main repository.
        slug: Work item slug (used for directory name and branch).
        base_branch: Branch to base a *new* worktree on (ignored when
            the worktree already exists).

    Returns:
        Absolute path to the worktree directory
        (``repo_root / .worktrees / slug``).

    Raises:
        ValueError: If the slug is invalid.
        subprocess.CalledProcessError: If worktree creation fails after
            recovery attempts.
    """
    return create_worktree(repo_root, slug, base_branch)


def remove_worktree(repo_root: Path, slug: str, delete_branch: bool = True) -> bool:
    """Remove a git worktree and optionally its branch.

    If the current process CWD is inside the worktree being removed,
    this function changes CWD to repo_root first to prevent the shell
    from losing its working directory (see issue #301).

    Args:
        repo_root: Path to the main repository
        slug: Work item slug
        delete_branch: Whether to also delete the session branch

    Returns:
        True if successfully removed, False otherwise

    Raises:
        ValueError: If slug contains path traversal or invalid characters
    """
    _validate_slug(slug)
    worktree_dir = repo_root / WORKTREES_DIR / slug
    branch_name = f"session/{slug}"

    if not worktree_dir.exists():
        logger.info(f"Worktree not found: {worktree_dir}")
        return False

    # Guard against CWD death: if the current working directory is inside
    # the worktree we're about to remove, move to repo_root first.
    # Without this, the calling process (and Claude Code's persistent shell)
    # ends up with an invalid CWD and all subsequent commands fail.
    try:
        cwd = Path.cwd().resolve()
        wt_resolved = worktree_dir.resolve()
        if cwd == wt_resolved or wt_resolved in cwd.parents:
            logger.warning(
                f"CWD is inside worktree being removed ({cwd}). Changing to repo root: {repo_root}"
            )
            os.chdir(repo_root)
    except OSError:
        # CWD already invalid — move to repo root as recovery
        logger.warning("CWD is already invalid. Changing to repo root.")
        os.chdir(repo_root)

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


def _resolve_git_dir(repo_root: Path) -> Path:
    """Resolve the actual .git directory for a repo root.

    For regular repos, this is ``repo_root / .git`` (a directory).
    For worktrees, ``repo_root / .git`` is a file containing a ``gitdir:``
    pointer to the actual git directory.

    Args:
        repo_root: Path to the repository root.

    Returns:
        Path to the actual .git directory.

    Raises:
        ValueError: If no .git directory or file exists at repo_root.
    """
    git_path = repo_root / ".git"
    if not git_path.exists():
        raise ValueError(f"Not a git repository: {repo_root} (no .git found)")

    if git_path.is_dir():
        return git_path

    # .git is a file (worktree pointer): read the gitdir path
    content = git_path.read_text().strip()
    if content.startswith("gitdir: "):
        gitdir = Path(content[len("gitdir: ") :])
        if not gitdir.is_absolute():
            gitdir = (repo_root / gitdir).resolve()
        return gitdir

    raise ValueError(f"Unexpected .git file content at {repo_root}: {content}")


def _is_worktree(repo_root: Path) -> bool:
    """Check if a repo root is a worktree (not the main working tree).

    Worktrees have a .git file (pointer) instead of a .git directory.

    Args:
        repo_root: Path to check.

    Returns:
        True if repo_root is a worktree, False if it's the main repo.
    """
    git_path = repo_root / ".git"
    return git_path.exists() and git_path.is_file()


def ensure_clean_git_state(repo_root: Path) -> dict:
    """Detect and resolve dirty git state on the main working tree.

    Checks for in-progress merge, rebase, and cherry-pick operations,
    aborts them, and stashes any remaining uncommitted changes. This
    prevents SDLC skills from failing when switching branches or
    creating worktrees while another branch has unresolved conflicts.

    **Safety**: This function only operates on the main working tree
    (where ``.git`` is a directory), not on worktree directories (where
    ``.git`` is a file). If called on a worktree, it returns immediately
    with ``{"skipped": True}``.

    Args:
        repo_root: Path to the repository root. Must contain a ``.git``
            directory (not a worktree pointer file).

    Returns:
        Dict describing what was cleaned up:
        - ``skipped``: True if this is a worktree (no action taken)
        - ``merge_aborted``: True if an in-progress merge was aborted
        - ``rebase_aborted``: True if an in-progress rebase was aborted
        - ``cherry_pick_aborted``: True if an in-progress cherry-pick was aborted
        - ``changes_stashed``: True if uncommitted changes were stashed
        - ``stash_name``: The stash message if changes were stashed
        - ``errors``: List of error messages for any failed operations
        - ``was_clean``: True if no dirty state was detected

    Raises:
        ValueError: If ``repo_root`` does not contain a ``.git`` directory
            or file, or if the guard cannot fully clean the state.
    """
    result: dict = {
        "skipped": False,
        "merge_aborted": False,
        "rebase_aborted": False,
        "cherry_pick_aborted": False,
        "changes_stashed": False,
        "stash_name": None,
        "errors": [],
        "was_clean": False,
    }

    # Safety check: refuse to operate on worktrees
    if _is_worktree(repo_root):
        logger.info(f"Skipping git state guard: {repo_root} is a worktree, not the main repo")
        result["skipped"] = True
        return result

    # Resolve the git directory
    try:
        git_dir = _resolve_git_dir(repo_root)
    except ValueError as e:
        raise ValueError(f"Cannot guard git state: {e}") from e

    dirty = False

    # 1. Check for in-progress merge (MERGE_HEAD exists)
    if (git_dir / "MERGE_HEAD").exists():
        dirty = True
        logger.warning(f"Detected in-progress merge at {repo_root}. Aborting.")
        try:
            subprocess.run(
                ["git", "merge", "--abort"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            result["merge_aborted"] = True
            logger.info("Successfully aborted in-progress merge")
        except subprocess.CalledProcessError as e:
            msg = f"Failed to abort merge: {e.stderr.strip()}"
            result["errors"].append(msg)
            logger.warning(msg)

    # 2. Check for in-progress rebase (rebase-merge/ or rebase-apply/ exists)
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        dirty = True
        logger.warning(f"Detected in-progress rebase at {repo_root}. Aborting.")
        try:
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            result["rebase_aborted"] = True
            logger.info("Successfully aborted in-progress rebase")
        except subprocess.CalledProcessError as e:
            msg = f"Failed to abort rebase: {e.stderr.strip()}"
            result["errors"].append(msg)
            logger.warning(msg)

    # 3. Check for in-progress cherry-pick (CHERRY_PICK_HEAD exists)
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        dirty = True
        logger.warning(f"Detected in-progress cherry-pick at {repo_root}. Aborting.")
        try:
            subprocess.run(
                ["git", "cherry-pick", "--abort"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            result["cherry_pick_aborted"] = True
            logger.info("Successfully aborted in-progress cherry-pick")
        except subprocess.CalledProcessError as e:
            msg = f"Failed to abort cherry-pick: {e.stderr.strip()}"
            result["errors"].append(msg)
            logger.warning(msg)

    # 4. Check for uncommitted changes and stash them
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if status_result.stdout.strip():
        dirty = True
        stash_msg = "sdlc-auto-stash"
        logger.warning(f"Detected uncommitted changes at {repo_root}. Stashing as '{stash_msg}'.")
        try:
            subprocess.run(
                ["git", "stash", "push", "-m", stash_msg],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            result["changes_stashed"] = True
            result["stash_name"] = stash_msg
            logger.info("Successfully stashed uncommitted changes")
        except subprocess.CalledProcessError as e:
            msg = f"Failed to stash changes: {e.stderr.strip()}"
            result["errors"].append(msg)
            logger.warning(msg)

    if not dirty:
        result["was_clean"] = True
        logger.info(f"Git state is clean at {repo_root}")

    # If we detected dirty state but couldn't fully clean it, raise
    if result["errors"] and not result["was_clean"]:
        unresolved = "; ".join(result["errors"])
        raise ValueError(
            f"Git state guard could not fully clean {repo_root}. "
            f"Manual intervention needed: {unresolved}"
        )

    return result


def cleanup_after_merge(repo_root: Path, slug: str) -> dict:
    """Clean up worktree and local branch after a PR has been merged.

    This is the post-merge cleanup step for the SDLC pipeline. After
    `gh pr merge --squash --delete-branch` deletes the remote branch,
    this function removes the local worktree and branch that would
    otherwise block deletion.

    Safe to call in any state:
    - Worktree exists + branch exists: removes both
    - Worktree already removed + branch exists: deletes branch
    - Everything already cleaned up: no-op

    Args:
        repo_root: Path to the main repository.
        slug: Work item slug (e.g., "my-feature"). The worktree is
              expected at .worktrees/{slug} and the branch at
              session/{slug}.

    Returns:
        Dict with keys:
        - slug: The slug that was cleaned up
        - worktree_removed: True if a worktree was removed
        - branch_deleted: True if a local branch was deleted
        - already_clean: True if nothing needed cleanup
        - errors: List of error messages for any failed steps

    Raises:
        ValueError: If the slug is invalid.
    """
    _validate_slug(slug)

    branch_name = f"session/{slug}"
    worktree_dir = repo_root / WORKTREES_DIR / slug
    result = {
        "slug": slug,
        "worktree_removed": False,
        "branch_deleted": False,
        "already_clean": False,
        "errors": [],
    }

    had_worktree = worktree_dir.exists()
    had_branch = _branch_exists(repo_root, branch_name)

    # Step 1: Remove worktree if it exists
    if had_worktree:
        removed = remove_worktree(repo_root, slug, delete_branch=False)
        result["worktree_removed"] = removed
        if removed:
            logger.info(f"Post-merge: removed worktree for {slug}")
        else:
            msg = f"Failed to remove worktree .worktrees/{slug}"
            result["errors"].append(msg)
            logger.warning(f"Post-merge: {msg}")

    # Step 2: Prune stale worktree references (handles cases where the
    # directory was manually deleted but git still tracks the worktree)
    prune_worktrees(repo_root)

    # Step 3: Delete local branch if it still exists
    # Re-check after prune -- pruning may unblock branch deletion
    if had_branch or _branch_exists(repo_root, branch_name):
        branch_result = subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if branch_result.returncode == 0:
            result["branch_deleted"] = True
            logger.info(f"Post-merge: deleted local branch {branch_name}")
        else:
            msg = f"Failed to delete branch {branch_name}: {branch_result.stderr.strip()}"
            result["errors"].append(msg)
            logger.warning(f"Post-merge: {msg}")
    else:
        logger.info(f"Post-merge: branch {branch_name} already gone")

    # already_clean means nothing *needed* cleanup (not that cleanup failed)
    if not had_worktree and not had_branch:
        result["already_clean"] = True
        logger.info(f"Post-merge: nothing to clean up for {slug}")

    return result
