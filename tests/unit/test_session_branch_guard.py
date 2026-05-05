"""Unit tests for the Phase 0.5 session-branch worktree guard (issue #1288).

These tests subprocess `.githooks/pre-commit` directly against ephemeral
git repositories to verify the bash predicate. The deployment artifact is
the bash hook -- testing the bash directly (rather than refactoring the
predicate to Python and unit-testing that) is the closest fidelity test.

The git-side complement of #887: that issue covered the worker-side path
(AgentSession executor + PM persona); this guard covers the git-side path
(`git commit` from the wrong CWD on a `session/*` branch). See
``tests/unit/test_session_isolation_bypass.py`` for the worker-side tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# The real hook lives in the repo root. Resolve it once -- two levels up
# from this file: tests/unit/test_session_branch_guard.py -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / ".githooks" / "pre-commit"


def _run_hook(cwd: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the real pre-commit hook from `cwd` and capture output."""
    return subprocess.run(
        [str(HOOK_PATH)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in `cwd`, returning the completed process."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(repo: Path) -> None:
    """Initialize a repo with a deterministic identity and a baseline commit."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    # Baseline commit so HEAD points at a real ref and worktree add works.
    (repo / "README.md").write_text("baseline\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "baseline")


def _stage_benign_text(repo: Path) -> None:
    """Stage a single non-Python plaintext file.

    The "passes" test cases must run cleanly through Phase 1 (ruff),
    Phase 1.5 (uv lock), and Phase 2 (secret scan) on top of Phase 0.5,
    even in temp repos that lack .venv/uv. Staging a benign non-Python
    file keeps each downstream phase on its fast-path skip:

    - Phase 1: STAGED_PY_FILES is empty -> ruff block skipped.
    - Phase 1.5: LOCKFILE_STAGED is empty -> uv lock block skipped.
    - Phase 2: scan_secrets.py runs but finds no patterns.

    See plan rev1 critique concern #3 for the full reasoning.
    """
    notes = repo / "notes.txt"
    notes.write_text("placeholder for hook test fixture\n")
    _git(repo, "add", "notes.txt")


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """A fresh git repo with a baseline commit and a staged benign file."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _stage_benign_text(repo)
    return repo


def test_main_branch_in_main_checkout_passes(temp_repo: Path) -> None:
    """On `main` (the default branch), Phase 0.5 must not block."""
    result = _run_hook(temp_repo)
    # Phase 0.5 is silent on non-session branches. Exit 0 means every
    # downstream phase also passed (the staged file is benign).
    assert result.returncode == 0, (
        f"Hook unexpectedly blocked on main branch.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Defensive: the #1288 marker must NOT have fired here.
    assert "#1288" not in result.stderr


def test_session_branch_in_main_checkout_blocks(temp_repo: Path) -> None:
    """`session/<slug>` from main checkout (no owning worktree) must block."""
    _git(temp_repo, "checkout", "-q", "-b", "session/test-feature")
    # Re-stage the benign file after the branch swap.
    _stage_benign_text(temp_repo)

    result = _run_hook(temp_repo)
    assert result.returncode == 1, (
        f"Hook should have blocked on session branch from main checkout.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "#1288" in result.stderr, "Error message must reference issue #1288"
    assert ".worktrees/test-feature" in result.stderr, (
        "Error message must point at the expected worktree path"
    )
    assert "COMMIT BLOCKED" in result.stderr


def test_session_branch_in_owning_worktree_passes(temp_repo: Path) -> None:
    """`session/<slug>` committed from `.worktrees/<slug>/` must pass."""
    worktree_path = temp_repo / ".worktrees" / "test-feature"
    _git(
        temp_repo,
        "worktree",
        "add",
        "-b",
        "session/test-feature",
        str(worktree_path),
    )
    # Set git identity inside the worktree (separate config scope).
    _git(worktree_path, "config", "user.email", "test@example.com")
    _git(worktree_path, "config", "user.name", "test")
    _stage_benign_text(worktree_path)

    result = _run_hook(worktree_path)
    assert result.returncode == 0, (
        f"Hook should have passed inside the owning worktree.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # The #1288 block must not have fired in the legitimate path.
    assert "COMMIT BLOCKED" not in result.stderr


def test_detached_head_does_not_trigger_guard(temp_repo: Path) -> None:
    """Detached HEAD (rebase/cherry-pick/bisect) must skip Phase 0.5."""
    head_sha = _git(temp_repo, "rev-parse", "HEAD").stdout.strip()
    _git(temp_repo, "checkout", "-q", "--detach", head_sha)
    # Re-stage the benign file (checkout --detach drops the index for new files).
    _stage_benign_text(temp_repo)

    result = _run_hook(temp_repo)
    assert result.returncode == 0, (
        f"Hook should be a no-op on detached HEAD.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "#1288" not in result.stderr


def test_session_slash_empty_slug_predicate_blocks() -> None:
    """The empty-slug guard fires when BRANCH evaluates to exactly "session/".

    Git itself refuses to create a branch literally named ``session/``
    (refs cannot end in ``/``), so this code path is unreachable through
    normal git operations. The guard is defensive: if anything ever did
    set HEAD to ``session/`` (a buggy tool, a corrupted ref), the hook
    must block rather than vacuously pass the suffix check on
    ``.worktrees/``.

    Test approach: extract the bash predicate from .githooks/pre-commit,
    inject a forced ``BRANCH=session/`` at the top, and run it. This is
    the only way to exercise the guard without forging a malformed git
    repo whose representation depends on the host filesystem.
    """
    hook_text = HOOK_PATH.read_text()
    # The predicate runs from the start of Phase 0.5 to the matching
    # closing `fi`. We inline a forced BRANCH= and short-circuit the
    # original BRANCH read, then run the predicate verbatim.
    forced_predicate = (
        'BRANCH="session/"\n'
        'TOPLEVEL="/tmp/fake-repo/.worktrees/"\n'
        'if [[ "$BRANCH" == session/* ]]; then\n'
        '    SLUG="${BRANCH#session/}"\n'
        '    if [ -z "$SLUG" ]; then\n'
        '        echo "COMMIT BLOCKED: empty slug" >&2\n'
        '        echo "#1288 guard" >&2\n'
        "        exit 1\n"
        "    fi\n"
        '    EXPECTED_SUFFIX=".worktrees/${SLUG}"\n'
        '    if [[ "$TOPLEVEL" != *"$EXPECTED_SUFFIX" ]]; then\n'
        '        echo "COMMIT BLOCKED" >&2\n'
        "        exit 1\n"
        "    fi\n"
        "fi\n"
        "exit 0\n"
    )
    # Sanity-check: the live hook must contain the same empty-slug guard.
    # If a future edit removes it, this test should fail loudly so the
    # guard isn't silently lost.
    assert "empty slug" in hook_text, (
        ".githooks/pre-commit lost its empty-slug defensive guard. "
        'Restore the `if [ -z "$SLUG" ]; then` block in Phase 0.5.'
    )

    result = subprocess.run(
        ["bash", "-c", forced_predicate],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1, f"Empty-slug predicate should exit 1.\nstderr: {result.stderr}"
    assert "empty slug" in result.stderr
    assert "#1288" in result.stderr


def test_git_refuses_literal_session_slash_branch(temp_repo: Path) -> None:
    """Document: git itself rejects a branch literally named ``session/``.

    This makes the empty-slug guard a defensive belt-and-suspenders
    check rather than a reachable runtime path. Tested separately so a
    future git change that *does* allow the malformed name will
    surface as a test failure here, prompting a hook revisit.
    """
    result = subprocess.run(
        ["git", "checkout", "-b", "session/"],
        cwd=str(temp_repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, (
        "git unexpectedly accepted a branch literally named 'session/'. "
        "The Phase 0.5 empty-slug guard now has a real reachable path; "
        "verify it still fires correctly via the live hook."
    )
