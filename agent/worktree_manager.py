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
from typing import Literal

from config.settings import settings

logger = logging.getLogger(__name__)

WORKTREES_DIR = ".worktrees"
VALID_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

# ---------------------------------------------------------------------------
# Unmerged-branch guard (issue #1646)
# ---------------------------------------------------------------------------

_preserved_branch_count = 0  # restart-fragile counter; supplemented by live git branch --list


def merged_via_ancestor(repo_root: str, branch: str, base: str = "main") -> bool:
    """Return True iff branch tip is a git ancestor of base (non-squash merges only)."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", branch, base],
        cwd=repo_root,
        capture_output=True,
        timeout=settings.timeouts.git_subprocess_s,
    )
    return result.returncode == 0


def merged_via_tree(repo_root: str, branch: str, base: str = "main") -> bool:
    """Return True iff merging branch into base is a no-op (squash-safe oracle).

    Uses git merge-tree --write-tree: landed iff exit 0 AND result tree == base^{tree}.
    Robust to main advancing past the squash commit. Requires git >= 2.38.
    """
    try:
        # Get base tree oid
        base_tree = subprocess.run(
            ["git", "rev-parse", f"{base}^{{tree}}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
        )
        if base_tree.returncode != 0:
            return False
        base_tree_oid = base_tree.stdout.strip()

        # Run merge-tree
        merge_result = subprocess.run(
            ["git", "merge-tree", "--write-tree", base, branch],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
        )
        if merge_result.returncode != 0:
            # Conflict or other error — not landed
            return False

        result_tree_oid = merge_result.stdout.strip()
        return result_tree_oid == base_tree_oid
    except Exception:
        return False


def _resolve_base(repo_root: str, base: str = "main") -> str | None:
    """Resolve base branch name; fall back to origin/HEAD default branch."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", base],
        cwd=repo_root,
        capture_output=True,
        timeout=settings.timeouts.git_subprocess_s,
    )
    if result.returncode == 0:
        return base
    # Try origin/HEAD
    result2 = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=settings.timeouts.git_subprocess_s,
    )
    if result2.returncode == 0:
        ref = result2.stdout.strip()
        return ref.split("/")[-1] if "/" in ref else None
    return None


def safe_delete_branch(
    repo_root: str,
    branch_name: str,
    *,
    base: str = "main",
    predicate,
    force: bool = False,
) -> dict:
    """Delete branch only if merged per predicate; preserve and log if not.

    Args:
        repo_root: Path to git repo root.
        branch_name: Branch to delete.
        base: Base branch to check against (default "main").
        predicate: Callable(repo_root, branch, base) -> bool; True = landed, safe to delete.
        force: If True, use git branch -D (needed for squash-merged branches); else -d.

    Returns:
        dict with keys:
          - deleted: bool
          - skipped_unmerged: bool
          - branch: str (the branch name)
          - error: str | None
    """
    global _preserved_branch_count

    resolved_base = _resolve_base(repo_root, base)
    if resolved_base is None:
        logger.warning(
            "[unmerged-branch-guard] cannot resolve base '%s' for branch '%s'"
            " — refusing deletion (fail-safe)",
            base,
            branch_name,
        )
        _preserved_branch_count += 1
        _log_preserved_count(repo_root)
        return {
            "deleted": False,
            "skipped_unmerged": True,
            "branch": branch_name,
            "error": f"cannot resolve base '{base}'",
        }

    try:
        landed = predicate(repo_root, branch_name, resolved_base)
    except Exception as e:
        logger.warning(
            "[unmerged-branch-guard] predicate error for branch '%s': %s"
            " — refusing deletion (fail-safe)",
            branch_name,
            e,
        )
        _preserved_branch_count += 1
        _log_preserved_count(repo_root)
        return {
            "deleted": False,
            "skipped_unmerged": True,
            "branch": branch_name,
            "error": str(e),
        }

    if not landed:
        _preserved_branch_count += 1
        live_count = _count_live_session_branches(repo_root)
        logger.warning(
            "[unmerged-branch-guard] branch '%s' has unmerged commits"
            " — preserving (preserved=%d, live session/* branches=%s)",
            branch_name,
            _preserved_branch_count,
            live_count,
        )
        _log_preserved_count(repo_root)
        return {
            "deleted": False,
            "skipped_unmerged": True,
            "branch": branch_name,
            "error": None,
        }

    # Branch is landed — delete it
    flag = "-D" if force else "-d"
    result = subprocess.run(
        ["git", "branch", flag, branch_name],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=settings.timeouts.git_subprocess_s,
    )
    if result.returncode == 0:
        return {"deleted": True, "skipped_unmerged": False, "branch": branch_name, "error": None}
    else:
        err = result.stderr.strip()
        logger.warning(
            "[unmerged-branch-guard] git branch %s '%s' failed: %s", flag, branch_name, err
        )
        return {"deleted": False, "skipped_unmerged": False, "branch": branch_name, "error": err}


def _count_live_session_branches(repo_root: str) -> str:
    """Count live session/* branches via git for the observability line."""
    try:
        result = subprocess.run(
            ["git", "branch", "--list", "session/*"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
        )
        if result.returncode == 0:
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return str(len(lines))
    except Exception:  # noqa: S110 -- count degrades to 'unknown'
        pass
    return "unknown"


def _log_preserved_count(repo_root: str) -> None:
    """Emit a greppable preserved=N summary with live branch count."""
    live_count = _count_live_session_branches(repo_root)
    logger.warning(
        "[unmerged-branch-guard] preserved=%d (live session/* branches=%s)",
        _preserved_branch_count,
        live_count,
    )


class WorktreeBranchMismatchError(RuntimeError):
    """Raised when a reused worktree's HEAD does not match the expected branch
    and cannot be auto-recovered (issue #1377).

    The recoverable case (clean working tree) is handled by
    ``verify_worktree_branch`` itself via ``git checkout``; this exception is
    only raised when the worktree has uncommitted state or when the underlying
    git command fails.

    Attributes:
        worktree_path: The worktree directory that was checked.
        expected_branch: The branch the executor wanted the worktree on.
        actual_branch: The branch git reported (may be empty / detached HEAD).
        dirty_files: List of porcelain status lines explaining why auto-recovery
            was refused (empty when the failure was a subprocess error).
    """

    def __init__(
        self,
        worktree_path: Path | str,
        expected_branch: str,
        actual_branch: str,
        dirty_files: list[str] | None = None,
        cause: str | None = None,
    ) -> None:
        self.worktree_path = Path(worktree_path)
        self.expected_branch = expected_branch
        self.actual_branch = actual_branch
        self.dirty_files = list(dirty_files or [])
        msg = (
            f"Worktree branch mismatch at {self.worktree_path}: "
            f"expected={expected_branch!r}, actual={actual_branch!r}"
        )
        if self.dirty_files:
            msg += f", dirty_files={self.dirty_files}"
        if cause:
            msg += f" ({cause})"
        super().__init__(msg)


def verify_worktree_branch(worktree_path: Path, expected_branch: str) -> None:
    """Verify a reused worktree is checked out to the expected branch.

    This is the missing primitive identified by issue #1377: after a BUILD
    session left ``.worktrees/{slug}/`` on ``session/{slug}``, a subsequent
    MERGE dev session reusing the same path could end up running with the
    wrong branch context, silently producing no output until the startup
    watchdog killed it.

    Behavior:
      * Matching branch — return silently (no log noise).
      * Mismatch + clean working tree — run ``git checkout <expected_branch>``
        and log at INFO with slug/from/to. This is the "operator-friendly"
        recovery path; the alternative would be to refuse every reused
        worktree and force a manual cleanup.
      * Mismatch + dirty working tree — raise :class:`WorktreeBranchMismatchError`
        with the dirty file list, preserving uncommitted work for inspection.
      * Underlying git failure (detached HEAD that cannot be parsed, corrupted
        worktree, etc.) — raise :class:`WorktreeBranchMismatchError` with the
        git stderr as the cause.

    Args:
        worktree_path: Absolute path to the worktree to check.
        expected_branch: The branch name the caller expects (e.g. ``"main"``
            or ``"session/sdlc-1377"``).

    Raises:
        TypeError: If ``worktree_path`` is None.
        ValueError: If ``expected_branch`` is empty / whitespace-only.
        WorktreeBranchMismatchError: If the worktree path is missing, the
            branch differs and the worktree is dirty, or the underlying git
            command fails.
    """
    if worktree_path is None:
        raise TypeError("worktree_path must not be None")
    if not isinstance(expected_branch, str) or not expected_branch.strip():
        raise ValueError("expected_branch must be a non-empty string")

    worktree_path = Path(worktree_path)
    if not worktree_path.exists():
        raise WorktreeBranchMismatchError(
            worktree_path,
            expected_branch,
            "",
            cause=f"worktree path does not exist: {worktree_path}",
        )

    try:
        head = subprocess.run(
            ["git", "-C", str(worktree_path), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
        )
    except subprocess.CalledProcessError as e:
        raise WorktreeBranchMismatchError(
            worktree_path,
            expected_branch,
            "",
            cause=f"git rev-parse failed: {e.stderr.strip() if e.stderr else e}",
        ) from e
    except FileNotFoundError as e:  # pragma: no cover - git always installed
        raise WorktreeBranchMismatchError(
            worktree_path,
            expected_branch,
            "",
            cause=f"git executable not found: {e}",
        ) from e

    actual_branch = head.stdout.strip()
    if actual_branch == expected_branch:
        return

    # Branch differs — inspect working tree cleanliness.
    try:
        status = subprocess.run(
            ["git", "-C", str(worktree_path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
        )
    except subprocess.CalledProcessError as e:
        raise WorktreeBranchMismatchError(
            worktree_path,
            expected_branch,
            actual_branch,
            cause=f"git status failed: {e.stderr.strip() if e.stderr else e}",
        ) from e

    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    if dirty:
        raise WorktreeBranchMismatchError(
            worktree_path,
            expected_branch,
            actual_branch,
            dirty_files=dirty,
        )

    # Pre-check: refuse early if expected_branch is locked by another worktree
    # (issue #1412). Without this guard, `git checkout` below fails with a raw
    # "'<branch>' is already used by worktree at '<path>'" stderr leak.
    try:
        common_dir = subprocess.run(
            ["git", "-C", str(worktree_path), "rev-parse", "--git-common-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
        )
    except subprocess.CalledProcessError as e:
        raise WorktreeBranchMismatchError(
            worktree_path,
            expected_branch,
            actual_branch,
            cause=f"git rev-parse --git-common-dir failed: {e.stderr.strip() if e.stderr else e}",
        ) from e

    # --git-common-dir returns the .git directory of the main repo (may be a
    # path relative to the worktree, e.g. just ".git" for a standalone repo);
    # resolve it against the worktree before taking the parent.
    common_dir_raw = Path(common_dir.stdout.strip())
    if not common_dir_raw.is_absolute():
        common_dir_raw = worktree_path / common_dir_raw
    main_repo_root = common_dir_raw.resolve().parent
    holding_path = _find_worktree_for_branch(main_repo_root, expected_branch)
    if holding_path is not None and Path(holding_path).resolve() != worktree_path.resolve():
        raise WorktreeBranchMismatchError(
            worktree_path,
            expected_branch,
            actual_branch,
            cause=(
                f"cannot checkout '{expected_branch}': already used by worktree at '{holding_path}'"
            ),
        )

    # Clean — auto-recover by checking out the expected branch.
    try:
        subprocess.run(
            ["git", "-C", str(worktree_path), "checkout", expected_branch],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
        )
    except subprocess.CalledProcessError as e:
        raise WorktreeBranchMismatchError(
            worktree_path,
            expected_branch,
            actual_branch,
            cause=f"git checkout failed: {e.stderr.strip() if e.stderr else e}",
        ) from e

    slug = worktree_path.name
    logger.info(
        "[worktree-branch-recovery] slug=%s path=%s from=%s to=%s "
        "(auto-checkout of clean worktree, issue #1377)",
        slug,
        worktree_path,
        actual_branch,
        expected_branch,
    )


def worktree_busy_check(repo_root: Path, slug: str) -> tuple[str, str] | None:
    """Check whether any non-terminal AgentSession references this worktree.

    Walks the AgentSession table looking for rows whose ``working_dir`` lives
    inside ``.worktrees/{slug}/`` (or is exactly that directory) and whose
    ``status`` is not in ``TERMINAL_STATUSES``. The first match wins.

    Imports of ``models.agent_session`` and ``models.session_lifecycle`` are
    deferred to function body to keep ``worktree_manager.py``'s import-time
    graph unchanged (worktree_manager is loaded by tooling that should not
    pay the Popoto bootstrap cost just to validate slugs).

    Path comparison normalizes both sides via ``os.path.normpath`` (no symlink
    resolution) then matches the worktree's path components segment-by-segment
    against the session's. ``.worktrees/sdlc-1218`` matches ``.worktrees/
    sdlc-1218/subdir`` but not ``.worktrees/sdlc-1218-other`` (Risk 5).

    Failure mode: if the Popoto query raises (Redis unavailable, malformed
    row, anything), the helper logs a WARNING and returns ``None`` (fail-open).
    Refusing every worktree removal because Redis hiccupped would cause more
    operational pain than the busy guard prevents.

    Args:
        repo_root: Path to the main repository.
        slug: Work item slug whose worktree we want to remove.

    Returns:
        ``(session_id, agent_session_id)`` of the first live session
        referencing the worktree, or ``None`` if clear.
    """
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES
    except Exception as e:  # pragma: no cover - import-time failure
        logger.warning("worktree_busy_check: model imports failed (%s); fail-open", e)
        return None

    worktree_dir = (repo_root / WORKTREES_DIR / slug).resolve()
    worktree_norm = os.path.normpath(str(worktree_dir))
    worktree_parts = Path(worktree_norm).parts

    try:
        sessions = AgentSession.query.all()
    except Exception as e:
        logger.warning("worktree_busy_check: AgentSession query failed (%s); fail-open", e)
        return None

    for session in sessions:
        try:
            wd = getattr(session, "working_dir", None)
            if not wd:
                continue
            status = getattr(session, "status", None)
            if not status or status in TERMINAL_STATUSES:
                continue

            # Normalize without resolving symlinks (Risk 5: realpath could
            # amplify the match into unrelated directories).
            try:
                # Try resolving an absolute path; fall back to normpath for
                # relative working_dir values like ".worktrees/sdlc-1218".
                if os.path.isabs(wd):
                    session_norm = os.path.normpath(wd)
                else:
                    session_norm = os.path.normpath(str((repo_root / wd).resolve()))
            except Exception:
                session_norm = os.path.normpath(str(wd))

            session_parts = Path(session_norm).parts
            # Segment-aware containment: the worktree's parts must be a prefix
            # of the session's parts. This rejects ".worktrees/sdlc-1218-other"
            # while accepting ".worktrees/sdlc-1218/subdir".
            if (
                len(session_parts) >= len(worktree_parts)
                and session_parts[: len(worktree_parts)] == worktree_parts
            ):
                session_id = getattr(session, "session_id", "") or ""
                agent_session_id = getattr(session, "agent_session_id", "") or ""
                return (session_id, agent_session_id)
        except Exception as e:
            logger.debug("worktree_busy_check: skipping session row (%s)", e)
            continue

    return None


def validate_workspace(
    path: Path | str | None,
    allowed_root: Path,
    is_worktree: bool = False,
) -> Path:
    """Validate a workspace directory before agent launch.

    Enforces three safety invariants:
    1. CWD existence - the directory must exist and be a directory
    2. Path containment - must be within allowed_root
    3. Slug sanitization - worktree path components must match VALID_SLUG_RE

    On any violation, logs a warning and falls back to allowed_root.

    Args:
        path: The workspace path to validate. None/empty falls back.
        allowed_root: The root directory that path must be contained within.
        is_worktree: If True, validates worktree-specific path components.

    Returns:
        The validated (resolved) path, or allowed_root as fallback.
    """
    if path is None or str(path).strip() == "":
        logger.warning(f"Workspace validation: empty path provided, falling back to {allowed_root}")
        return allowed_root

    try:
        # Expand ~ before resolve(): Path.resolve() does NOT expand ~ and would
        # concatenate it under cwd, producing nonsense like
        # "/Users/<user>/src/ai/~/src/cuttlefish". projects.json may store
        # working_directory as the literal string "~/src/foo" (load_config()
        # in bridge/routing.py expands it, but out-of-band session creators
        # — test skills, manual valor_session calls — may bypass that funnel).
        # Expanding here makes the worker chokepoint resilient.
        resolved = Path(path).expanduser().resolve()
    except OSError as e:
        logger.warning(
            f"Workspace validation: failed to resolve '{path}': {e}, falling back to {allowed_root}"
        )
        return allowed_root

    # Invariant 1: CWD existence
    if not resolved.exists() or not resolved.is_dir():
        logger.warning(
            f"Workspace validation: '{resolved}' does not exist or is not a directory, "
            f"falling back to {allowed_root}"
        )
        return allowed_root

    # Invariant 2: Path containment
    try:
        resolved.relative_to(allowed_root.resolve())
    except ValueError:
        logger.warning(
            f"Workspace validation: '{resolved}' is outside allowed root '{allowed_root}', "
            f"falling back to {allowed_root}"
        )
        return allowed_root

    # Invariant 3: Slug sanitization (worktree paths only)
    if is_worktree:
        worktrees_dir = WORKTREES_DIR
        resolved_parts = resolved.parts
        # Find the .worktrees component and validate the slug after it
        for i, part in enumerate(resolved_parts):
            if part == worktrees_dir and i + 1 < len(resolved_parts):
                slug = resolved_parts[i + 1]
                if not VALID_SLUG_RE.match(slug):
                    logger.warning(
                        f"Workspace validation: worktree slug '{slug}' "
                        f"contains invalid characters, "
                        f"falling back to {allowed_root}"
                    )
                    return allowed_root

    return resolved


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
        timeout=settings.timeouts.git_subprocess_s,
    )

    if result.returncode != 0:
        raise ValueError(
            f"Path {file_path} is not inside a git repository. git error: {result.stderr.strip()}"
        )

    return Path(result.stdout.strip())


def resolve_main_repo_root(file_path: str | Path) -> Path:
    """Determine the main repository root for a file, even when inside a worktree.

    Use this when you need the main repo root regardless of whether
    ``file_path`` is in a worktree. From inside a linked worktree,
    ``git rev-parse --show-toplevel`` (used by :func:`resolve_repo_root`)
    returns the *worktree's* path — which is the wrong base when you need
    to reach into ``.worktrees/{slug}/`` for cleanup. This helper instead
    uses ``git rev-parse --git-common-dir``, which always points at the
    main repo's ``.git`` directory regardless of which worktree you are in.
    The parent of that ``.git`` directory is the main repo root.

    Contrast with :func:`resolve_repo_root`, which returns the worktree
    path when called from inside one. Prefer ``resolve_repo_root`` when
    you intentionally want the local checkout (e.g. plan documents); use
    this helper when you need to manipulate sibling worktrees.

    Args:
        file_path: Absolute or relative path to a file or directory.
            If a directory is given, it is used directly. If a file is
            given, its parent directory is used.

    Returns:
        Absolute Path to the main git repository root.

    Raises:
        FileNotFoundError: If the file or its parent directory does not exist.
        ValueError: If the path is not inside any git repository.
    """
    path = Path(file_path).resolve()

    # Use the directory containing the file, or the path itself if it's a dir
    search_dir = path if path.is_dir() else path.parent

    if not search_dir.exists():
        raise FileNotFoundError(
            f"Cannot resolve main repo root: directory {search_dir} does not exist"
        )

    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=search_dir,
        capture_output=True,
        text=True,
        timeout=settings.timeouts.git_subprocess_s,
    )

    if result.returncode != 0:
        raise ValueError(
            f"Path {file_path} is not inside a git repository. git error: {result.stderr.strip()}"
        )

    common_dir = Path(result.stdout.strip())
    # ``--git-common-dir`` may return a relative path (relative to ``cwd``)
    # or an absolute path depending on the git version and worktree layout.
    # Normalize before taking ``.parent``.
    if common_dir.is_absolute():
        git_dir = common_dir.resolve()
    else:
        git_dir = (search_dir / common_dir).resolve()

    return git_dir.parent


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
        timeout=settings.timeouts.git_subprocess_s,
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
        timeout=settings.timeouts.git_subprocess_s,
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

    Path-containment invariant: this helper refuses to operate on any path
    that is not strictly under ``repo_root / WORKTREES_DIR``. The precondition
    follows the same shape as :func:`validate_workspace` (compute → check →
    log-and-raise). The 2026-04-10 incident (issue #880) showed that trusting
    the caller's path and falling back to a silent recursive delete can
    recursively destroy the main repository when a session branch gets
    checked out in the main working tree. The guard is the primary defense;
    the fail-loud fallback is the secondary defense.

    Args:
        repo_root: Path to the main repository.
        branch_name: The branch name locked by the stale worktree.
        worktree_path: Path string from ``git worktree list``.

    Raises:
        RuntimeError: If ``worktree_path`` resolves to ``repo_root`` itself
            or any path outside ``repo_root / WORKTREES_DIR``. This is a
            loud failure by design -- see issue #880 for the incident that
            motivated the guard.
    """
    wt = Path(worktree_path).resolve()
    worktrees_root = (repo_root / WORKTREES_DIR).resolve()

    # Path-containment guard. The ``wt == repo_root.resolve()`` clause is
    # kept explicit for grep discoverability of the 2026-04-10 incident
    # case (the bug was exactly ``wt == repo_root``); logically the
    # ``is_relative_to(worktrees_root)`` clause alone would reject it.
    # The ``logger.critical`` call MUST fire BEFORE the ``raise`` so
    # crash-tracker correlation and log audits see the event even if a
    # caller catches ``RuntimeError`` upstream.
    if wt == repo_root.resolve() or not wt.is_relative_to(worktrees_root):
        logger.critical(
            f"Refusing to clean up worktree at {wt}: path is not under "
            f"{worktrees_root}. branch={branch_name} repo_root={repo_root}. "
            f"See issue #880 for the incident that motivated this guard."
        )
        raise RuntimeError(
            f"Refusing to clean up worktree at {wt}: path is not under "
            f"{worktrees_root}. Branch={branch_name}, repo_root={repo_root}."
        )

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
            timeout=settings.timeouts.git_subprocess_s,
        )
        logger.info(f"Removed stale worktree: {worktree_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to remove stale worktree {worktree_path}: {e.stderr}")
        # Fire logger.critical BEFORE prune_worktrees so a prune exception
        # cannot swallow the critical log (C4). Order MUST be:
        # logger.error -> logger.critical -> prune_worktrees -> rmtree.
        logger.critical(
            f"Fallback rmtree for stale worktree {wt} after git worktree "
            f"remove failed. branch={branch_name} repo_root={repo_root}. "
            f"See issue #880."
        )
        # As a last resort, prune and manually remove the directory.
        prune_worktrees(repo_root)
        if wt.exists():
            # Error suppression is intentionally absent here (see #880):
            # silent partial destruction was the primary bug class. If this
            # rmtree fails, we want the exception to propagate so the
            # failure is surfaced, logged, and crash-tracked instead of
            # leaving the repository in a half-destroyed state.
            shutil.rmtree(wt)
            logger.info(f"Manually removed stale worktree directory: {worktree_path}")
            # Prune again to clean up the now-missing reference.
            prune_worktrees(repo_root)


# Success marker for per-worktree venv provisioning (issue #2052). Written
# inside the worktree's .venv only AFTER `uv sync` exits 0 -- `pyvenv.cfg` is
# written near the START of env creation (before packages install), so its
# existence cannot distinguish a complete env from one interrupted mid-sync.
# The reuse path in create_worktree keys re-provisioning on this marker.
PROVISIONED_MARKER = ".provisioned"


def provision_worktree_venv(worktree_dir: Path) -> bool:
    """Provision a complete worktree-local venv from the lockfile (issue #2052).

    Runs ``uv sync --all-extras`` with the worktree as cwd, ``VIRTUAL_ENV``
    stripped (agent shells export the shared repo-root venv; uv would ignore
    it with a warning, and legacy uv versions could honor it destructively),
    and ``UV_PROJECT_ENVIRONMENT`` pinned to the absolute
    ``<worktree>/.venv`` so the target env never depends on uv's project
    discovery. uv hardlinks packages from its global cache, so the per-
    worktree disk cost is incremental, not a full copy.

    On success, touches ``.venv/.provisioned`` (see ``PROVISIONED_MARKER``).

    Fail-open by design: any failure (uv missing, sync error, timeout,
    marker write error) logs a WARNING tagged ``[worktree-venv-provision-
    failed]`` (greppable by log-scanning reflections / checking-system-logs)
    and returns False -- the lane still works against the shared env, and
    the #2050 ``uv sync`` guard keeps blocking there because no worktree-
    local ``.venv`` exists (a partial ``.venv`` remains repairable: the
    guard allows ``uv sync`` against it, and the marker-keyed reuse path
    re-provisions on next ``create_worktree`` call).

    Args:
        worktree_dir: Absolute path to the worktree to provision.

    Returns:
        True if the env is provisioned (marker present), False otherwise.
    """
    worktree_dir = Path(worktree_dir)
    venv_dir = worktree_dir / ".venv"
    marker = venv_dir / PROVISIONED_MARKER

    if marker.exists():
        return True
    if not worktree_dir.is_dir():
        logger.warning(
            "[worktree-venv-provision-failed] worktree dir does not exist: %s",
            worktree_dir,
        )
        return False

    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir)

    try:
        subprocess.run(
            ["uv", "sync", "--all-extras"],
            cwd=worktree_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=settings.timeouts.uv_sync_s,
        )
    except subprocess.CalledProcessError as e:
        stderr_tail = (e.stderr or "")[-500:]
        logger.warning(
            "[worktree-venv-provision-failed] uv sync failed for %s: %s",
            worktree_dir,
            stderr_tail,
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning(
            "[worktree-venv-provision-failed] uv sync timed out after %ss for %s",
            settings.timeouts.uv_sync_s,
            worktree_dir,
        )
        return False
    except FileNotFoundError as e:
        logger.warning(
            "[worktree-venv-provision-failed] uv executable not found for %s: %s",
            worktree_dir,
            e,
        )
        return False

    try:
        marker.touch()
    except OSError as e:
        logger.warning(
            "[worktree-venv-provision-failed] could not write %s marker for %s: %s",
            PROVISIONED_MARKER,
            worktree_dir,
            e,
        )
        return False

    logger.info("Provisioned isolated worktree venv: %s", venv_dir)
    return True


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
        # Retroactive healing (issue #2052, intentional scope addition): a
        # pre-existing lane created before per-worktree isolation shipped --
        # or one whose provisioning sync was interrupted (marker absent even
        # though `.venv/` exists) -- gets (re-)provisioned on reuse.
        if not (worktree_dir / ".venv" / PROVISIONED_MARKER).exists():
            provision_worktree_venv(worktree_dir)
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
        timeout=settings.timeouts.git_subprocess_s,
    )

    # Copy settings.local.json if it exists (not tracked by git)
    local_settings = repo_root / ".claude" / "settings.local.json"
    if local_settings.exists():
        target_dir = worktree_dir / ".claude"
        target_dir.mkdir(exist_ok=True)
        shutil.copy2(local_settings, target_dir / "settings.local.json")

    # Per-worktree venv isolation (issue #2052): eagerly provision a complete
    # worktree-local environment so lane commands never depend on -- or
    # mutate -- the shared repo-root .venv. Fail-open: a provisioning failure
    # logs loudly but never fails worktree creation.
    provision_worktree_venv(worktree_dir)

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


def remove_worktree(
    repo_root: Path,
    slug: str,
    delete_branch: bool = True,
    force: bool = False,
) -> bool | tuple[Literal["blocked"], str]:
    """Remove a git worktree and optionally its branch.

    Refuses removal if a non-terminal ``AgentSession`` still references the
    worktree as ``working_dir`` (issue #1357). This prevents the macOS
    cwd-vanished wedge documented in investigation #1246: deleting a
    directory out from under a running SDK subprocess does not signal that
    subprocess; ``getcwd(3)`` starts returning ENOENT, the harness hangs
    forever in ``proc.communicate()``, and the AgentSession row sits at
    ``status=running`` until a manual kill.

    If the current process CWD is inside the worktree being removed,
    this function changes CWD to repo_root first to prevent the shell
    from losing its working directory (see issue #301).

    Args:
        repo_root: Path to the main repository
        slug: Work item slug
        delete_branch: Whether to also delete the session branch
        force: If True, removes the worktree even when a live session
            references it. A WARNING is logged in that case. Use only
            when the session has already been verified dead but its row
            has not yet flipped to a terminal status.

    Returns:
        True if successfully removed, False if the worktree was missing or
        the git remove command failed, or ``("blocked", session_id)`` when
        a live session is using the worktree and ``force=False``.

    Raises:
        ValueError: If slug contains path traversal or invalid characters
    """
    _validate_slug(slug)
    worktree_dir = repo_root / WORKTREES_DIR / slug
    branch_name = f"session/{slug}"

    # Refuse-busy guard (issue #1357): ask the AgentSession table whether
    # any non-terminal session still references this worktree.
    busy = worktree_busy_check(repo_root, slug)
    if busy is not None:
        session_id, agent_session_id = busy
        if force:
            logger.warning(
                "force-removing worktree .worktrees/%s despite live "
                "session_id=%s agent_session_id=%s",
                slug,
                session_id,
                agent_session_id,
            )
        else:
            logger.warning(
                "Refusing to remove worktree .worktrees/%s: in use by "
                "session_id=%s agent_session_id=%s. Pass force=True to override.",
                slug,
                session_id,
                agent_session_id,
            )
            return ("blocked", session_id)

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
            timeout=settings.timeouts.git_subprocess_s,
        )
        logger.info(f"Removed worktree: {worktree_dir}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to remove worktree {worktree_dir}: {e.stderr}")
        return False

    if delete_branch:
        branch_result = safe_delete_branch(
            str(repo_root),
            branch_name,
            predicate=merged_via_tree,
            force=True,
        )
        if branch_result["deleted"]:
            logger.info(f"Deleted branch: {branch_name}")
        elif branch_result["skipped_unmerged"]:
            logger.warning(
                "[unmerged-branch-guard] branch '%s' preserved — work not yet merged to main",
                branch_name,
            )
        else:
            logger.warning(
                "Failed to delete branch %s: %s", branch_name, branch_result.get("error")
            )

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
        timeout=settings.timeouts.git_subprocess_s,
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
        timeout=settings.timeouts.git_subprocess_s,
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
                timeout=settings.timeouts.git_subprocess_s,
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
                timeout=settings.timeouts.git_subprocess_s,
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
                timeout=settings.timeouts.git_subprocess_s,
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
        timeout=settings.timeouts.git_subprocess_s,
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
                timeout=settings.timeouts.git_subprocess_s,
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

    The branch deletion is guarded by the unmerged-branch guard (issue #1646):
    safe_delete_branch verifies the merged precondition (via merged_via_tree) before
    deleting. If the branch has unmerged commits, it is preserved and
    skipped_unmerged=True is set in the result.

    Safe to call in any state:
    - Worktree exists + branch exists: removes both (if merged)
    - Worktree already removed + branch exists: deletes branch (if merged)
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
        - skipped_unmerged: True if branch was preserved due to unmerged commits
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
        "skipped_unmerged": False,
        "already_clean": False,
        "errors": [],
    }

    had_worktree = worktree_dir.exists()
    had_branch = _branch_exists(repo_root, branch_name)

    # Step 1: Remove worktree if it exists
    if had_worktree:
        removed = remove_worktree(repo_root, slug, delete_branch=False)
        # Issue #1357: remove_worktree returns ("blocked", session_id) when a
        # live AgentSession references the worktree. Surface that into the
        # result dict so post_merge_cleanup.py can exit 2 and the operator
        # knows which session to investigate.
        if isinstance(removed, tuple) and removed and removed[0] == "blocked":
            session_id = removed[1]
            result["worktree_removed"] = False
            result["blocked_by_session"] = session_id
            msg = f"blocked: worktree in use by session_id={session_id}"
            result["errors"].append(msg)
            logger.warning(f"Post-merge: {msg}")
        else:
            result["worktree_removed"] = bool(removed)
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
    # Branch deletion is guarded: safe_delete_branch verifies merged precondition (issue #1646)
    if had_branch or _branch_exists(repo_root, branch_name):
        branch_del = safe_delete_branch(
            str(repo_root),
            branch_name,
            predicate=merged_via_tree,
            force=True,
        )
        if branch_del["deleted"]:
            result["branch_deleted"] = True
            logger.info(f"Post-merge: deleted local branch {branch_name}")
        elif branch_del["skipped_unmerged"]:
            result["skipped_unmerged"] = True
            msg = (
                f"[unmerged-branch-guard] branch '{branch_name}'"
                " preserved — work not yet merged to main"
            )
            result["errors"].append(msg)
            logger.warning(f"Post-merge: {msg}")
        else:
            msg = f"Failed to delete branch {branch_name}: {branch_del.get('error', 'unknown')}"
            result["errors"].append(msg)
            logger.warning(f"Post-merge: {msg}")
    else:
        logger.info(f"Post-merge: branch {branch_name} already gone")

    # already_clean means nothing *needed* cleanup (not that cleanup failed)
    if not had_worktree and not had_branch:
        result["already_clean"] = True
        logger.info(f"Post-merge: nothing to clean up for {slug}")

    # Issue #2050: warn-only backstop for the SHARED repo-root .venv. Since
    # issue #2052, lanes get isolated per-worktree envs, but the main checkout
    # still runs on the shared env -- a `uv sync` that slipped past the
    # PreToolUse guard from an unprovisioned worktree (e.g. via an exotic
    # shell chain, or a legacy uv honoring VIRTUAL_ENV) silently strips it.
    # Never raise here -- a missing extra is loud (logged) but must not block
    # lane exit / merge cleanup.
    try:
        from tools.venv_health import check_health

        missing = check_health()
        if missing:
            logger.warning(
                f"Post-merge: shared .venv is missing extras after {slug}: "
                f"{', '.join(missing)} -- see docs/features/uv-sync-worktree-guard.md"
            )
    except Exception as e:
        logger.warning(f"Post-merge: venv-health check itself failed: {e}")

    return result
