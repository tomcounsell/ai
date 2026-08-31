"""A worktree with no `.venv` must not run tests against the primary checkout (#3033).

A linked git worktree without its own `.venv` resolves imports through the
PRIMARY checkout's editable path entry. Its tests exercise `main`'s code, always
find a real module, and never raise — so the run reports green on code it never
loaded. The bias is toward green, which hides exactly the regressions the run
exists to catch. Observed in PR #3028: a confidently false "1545 unit tests pass"
in a PR body, with the one genuinely failing test surfacing only when a reviewer
forced `PYTHONPATH`.

`scripts/pytest-clean.sh` already aborts on an off-pin venv; these pin the
absent-venv case, which used to degrade silently instead of failing closed.

Reproduced live on 2026-08-31 in a real `git worktree add` with no `.venv`:
`import tools.sdlc_stage_query` resolved to `/Users/valorengels/src/ai/...`
(the primary checkout), and the pre-fix script reported "8 passed".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "pytest-clean.sh"


def _fake_checkout(tmp_path, *, linked_worktree: bool, has_venv: bool) -> Path:
    """A directory shaped like a pytest rootdir.

    A linked worktree has `.git` as a FILE (a gitdir pointer); the primary
    checkout has it as a directory. That is the distinction the guard keys on.
    """
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = ''\n")
    if linked_worktree:
        (root / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
    else:
        (root / ".git").mkdir()
    if has_venv:
        (root / ".venv").mkdir()
    return root


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(SCRIPT), "--version"], cwd=str(root), capture_output=True, text=True)


class TestGuardFires:
    """The guard must be able to fail — one nobody proved could fire is not a
    guard (#2658)."""

    def test_worktree_without_venv_aborts(self, tmp_path):
        result = _run(_fake_checkout(tmp_path, linked_worktree=True, has_venv=False))
        assert result.returncode != 0, "a venv-less worktree run must fail closed"
        assert "no .venv of its own" in result.stderr

    def test_abort_names_the_worktree_path(self, tmp_path):
        root = _fake_checkout(tmp_path, linked_worktree=True, has_venv=False)
        result = _run(root)
        assert str(root) in result.stderr

    def test_abort_names_the_missing_venv(self, tmp_path):
        root = _fake_checkout(tmp_path, linked_worktree=True, has_venv=False)
        result = _run(root)
        assert str(root / ".venv") in result.stderr

    def test_abort_names_the_remedy(self, tmp_path):
        result = _run(_fake_checkout(tmp_path, linked_worktree=True, has_venv=False))
        assert "uv sync" in result.stderr

    def test_abort_explains_why_it_matters(self, tmp_path):
        """An operator who does not know the mechanism will work around the
        guard rather than fix the worktree."""
        result = _run(_fake_checkout(tmp_path, linked_worktree=True, has_venv=False))
        assert "3033" in result.stderr
        assert "PRIMARY checkout" in result.stderr

    def test_no_tests_run_when_the_guard_fires(self, tmp_path):
        result = _run(_fake_checkout(tmp_path, linked_worktree=True, has_venv=False))
        assert "passed" not in result.stdout


class TestGuardDoesNotOverreach:
    """Scoped to linked worktrees missing a venv, and nothing else."""

    def test_worktree_with_venv_passes_the_guard(self, tmp_path):
        result = _run(_fake_checkout(tmp_path, linked_worktree=True, has_venv=True))
        assert "no .venv of its own" not in result.stderr

    def test_primary_checkout_without_venv_is_not_blocked_by_this_guard(self, tmp_path):
        """`.git` as a directory means primary checkout: it falls through to the
        PATH pytest as before, rather than being newly refused."""
        result = _run(_fake_checkout(tmp_path, linked_worktree=False, has_venv=False))
        assert "no .venv of its own" not in result.stderr

    def test_real_primary_checkout_still_runs(self):
        result = subprocess.run(
            [str(SCRIPT), "--version"], cwd=str(REPO_ROOT), capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "no .venv of its own" not in result.stderr
