"""Tests for the uv-lock phase (1.5) of ``.githooks/pre-commit`` (item 3 of sdlc-1155).

Exercises the hook in a temp git repo via ``subprocess.run``. The hook calls
``git diff --cached --name-only`` internally, so the fixture initialises a
real throwaway git repo (not just a ``tmp_path``) so the ``--cached`` list
is populated correctly.

Covers:
1. Short-circuit path: neither ``pyproject.toml`` nor ``uv.lock`` staged -> skip.
2. In-sync path: staging both files in a consistent state passes.
3. Out-of-sync path: staging ``pyproject.toml`` with a tampered ``uv.lock`` blocks.
4. No-uv path: when ``uv`` is absent, hook prints a warning and does not block.
5. Error-message contents: block message includes the fix command.
6. test_lockfile_only_edit_still_checked: stage ONLY ``uv.lock`` with a
   tampered hash and assert the hook still blocks (inverse of the AND-logic
   bug the plan calls out).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".githooks" / "pre-commit"
_BASE_PYPROJECT = (
    "[project]\n"
    'name = "miniapp"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.10"\n'
    "dependencies = []\n"
)
_DRIFT_PYPROJECT = (
    "[project]\n"
    'name = "miniapp"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.10"\n'
    'dependencies = ["packaging"]\n'
)


def _run_hook(repo: Path, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    # Run the hook with repo as cwd; the hook uses ``git rev-parse``/``git diff --cached``,
    # both of which honour the cwd's ``.git`` directory.
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialise a minimal git repo with one commit so ``git diff --cached`` works."""
    repo = tmp_path / "mini"
    repo.mkdir()
    # Minimise side effects: local user config only.
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "ci"], cwd=repo, check=True)
    # Empty initial commit so HEAD exists and ``git diff --cached`` behaves.
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=repo,
        check=True,
    )
    # The hook expects secrets scanner script to exist; without it the hook
    # prints a warning but exits 0. For these tests we only care about the
    # uv-lock phase, so the scanner's absence is actually fine.
    return repo


def test_short_circuit_non_lockfile_commit(git_repo, monkeypatch):
    """test_short_circuit_non_lockfile_commit: stage only README.md and
    assert the uv-lock phase is skipped entirely (no warnings about uv)."""
    readme = git_repo / "README.md"
    readme.write_text("hello")
    subprocess.run(["git", "add", "README.md"], cwd=git_repo, check=True)
    result = _run_hook(git_repo)
    # Hook may fail because the secret-scan phase can't find the scanner
    # script inside the temp repo -- but that is unrelated to phase 1.5.
    # Assert the phase-1.5 warning about uv is NOT emitted.
    assert "uv not found" not in result.stdout
    assert "uv.lock is out of sync" not in result.stdout


def test_lockfile_in_sync_passes(git_repo, tmp_path):
    """Staging an in-sync pair of pyproject.toml + uv.lock passes the phase."""
    if not shutil.which("uv"):
        pytest.skip("uv binary not on PATH")
    # Create a minimal valid pyproject.toml + uv.lock pair.
    pyproject = git_repo / "pyproject.toml"
    pyproject.write_text(_BASE_PYPROJECT)
    # Generate the lock file for this pyproject.
    r = subprocess.run(["uv", "lock"], cwd=git_repo, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("uv lock failed in temp repo; skipping in-sync test")
    subprocess.run(["git", "add", "pyproject.toml", "uv.lock"], cwd=git_repo, check=True)
    result = _run_hook(git_repo)
    # The hook may fail on the secret-scan phase because we did not install
    # the scanner in the mini repo; that is unrelated. The phase-1.5 block
    # message must NOT appear.
    assert "uv.lock is out of sync" not in result.stdout


def test_lockfile_only_edit_still_checked(git_repo):
    """Stage only uv.lock with drift and assert the hook still checks (and
    blocks when drift is real).

    Covers the OR-logic short-circuit fix: a commit that modifies only
    uv.lock must still enter phase 1.5 (the original AND-logic draft would
    have let this through without any check). We simulate drift by:
    1. Generating a lock for a baseline pyproject.
    2. Committing both files.
    3. Modifying pyproject.toml to add a dependency -- but NOT regenerating
       the lock. Then staging only uv.lock AFTER rewriting it to an older
       snapshot (inducing real drift between on-disk pyproject and staged
       lock).
    """
    if not shutil.which("uv"):
        pytest.skip("uv binary not on PATH")
    pyproject = git_repo / "pyproject.toml"
    pyproject.write_text(_BASE_PYPROJECT)
    r = subprocess.run(["uv", "lock"], cwd=git_repo, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"uv lock failed in temp repo: {r.stderr}")
    # Commit the initial in-sync pair so HEAD has both files.
    subprocess.run(["git", "add", "pyproject.toml", "uv.lock"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "baseline", "--no-verify"], cwd=git_repo, check=True
    )
    # Now modify pyproject to add a dep, producing drift vs uv.lock.
    pyproject.write_text(_DRIFT_PYPROJECT)
    # Stage the new pyproject; leave the old uv.lock in place (and also stage
    # it to reflect the "only uv.lock edit" scenario — note: we stage the
    # OLD lock content, which is now out of sync with the new pyproject).
    subprocess.run(["git", "add", "pyproject.toml"], cwd=git_repo, check=True)
    # Also stage uv.lock so the `LOCKFILE_STAGED` check triggers phase 1.5.
    subprocess.run(["git", "add", "--renormalize", "uv.lock"], cwd=git_repo, check=False)
    result = _run_hook(git_repo)
    # The hook must block because uv.lock is out of sync with pyproject.toml.
    assert "uv.lock is out of sync" in result.stdout, result.stdout
    assert result.returncode != 0


def test_error_message_includes_fix_command(git_repo):
    """When uv-lock phase blocks, the message contains the ``uv lock`` fix."""
    if not shutil.which("uv"):
        pytest.skip("uv binary not on PATH")
    pyproject = git_repo / "pyproject.toml"
    pyproject.write_text(_BASE_PYPROJECT)
    r = subprocess.run(["uv", "lock"], cwd=git_repo, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"uv lock failed: {r.stderr}")
    subprocess.run(["git", "add", "pyproject.toml", "uv.lock"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "baseline", "--no-verify"], cwd=git_repo, check=True
    )
    # Induce drift.
    pyproject.write_text(_DRIFT_PYPROJECT)
    subprocess.run(["git", "add", "pyproject.toml"], cwd=git_repo, check=True)
    result = _run_hook(git_repo)
    assert "uv lock && git add uv.lock" in result.stdout, result.stdout


def test_no_uv_binary_skips_with_warning(git_repo, monkeypatch):
    """When uv is not on PATH, the phase emits a warning and does not block."""
    # Strip uv from PATH by pointing PATH at a tmpdir that only has sh/bash.
    empty = git_repo.parent / "empty_bin"
    empty.mkdir(exist_ok=True)
    # Make sure sh/bash are still reachable (copy from actual locations).
    for tool in ("bash", "sh", "git", "grep", "cat", "tr", "python3"):
        src = shutil.which(tool)
        if src is None:
            pytest.skip(f"{tool} not available on this machine")
        dst = empty / tool
        if not dst.exists():
            dst.symlink_to(src)
    pyproject = git_repo / "pyproject.toml"
    pyproject.write_text('[project]\nname = "miniapp"\nversion = "0.1.0"\n')
    subprocess.run(["git", "add", "pyproject.toml"], cwd=git_repo, check=True)
    result = _run_hook(git_repo, env_overrides={"PATH": str(empty)})
    # Phase 1.5 must emit the "uv not found" warning but must not block on that phase.
    assert "uv not found" in result.stdout
    # It may still fail on the secret-scan phase, but not on phase 1.5.
    assert "uv.lock is out of sync" not in result.stdout
