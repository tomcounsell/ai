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

# The guard's headline, verbatim from scripts/pytest-clean.sh. "usable" is
# load-bearing: since 72ea67bba the guard keys on `.venv/bin/pytest`, so a
# `.venv` left behind by a failed `uv sync` (or one without `--extra dev`) is
# refused too. The negative assertions below pin this exact string so a reword
# of the script fails them instead of letting them pass vacuously.
GUARD_HEADLINE = "worktree has no usable .venv of its own"


def _fake_checkout(tmp_path, *, linked_worktree: bool, has_venv: bool) -> Path:
    """A directory shaped like a pytest rootdir.

    A linked worktree has `.git` as a FILE (a gitdir pointer); the primary
    checkout has it as a directory. That is the distinction the guard keys on.
    `has_venv` provisions what the guard actually requires, an executable
    `.venv/bin/pytest`, so the with-venv case exercises the guard's pass
    branch rather than tripping on a bare directory.
    """
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = ''\n")
    if linked_worktree:
        (root / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
    else:
        (root / ".git").mkdir()
    if has_venv:
        fake_pytest = root / ".venv" / "bin" / "pytest"
        fake_pytest.parent.mkdir(parents=True)
        fake_pytest.write_text("#!/bin/sh\necho 'pytest 0.0 (fake)'\n")
        fake_pytest.chmod(0o755)
    return root


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(SCRIPT), "--version"], cwd=str(root), capture_output=True, text=True)


class TestGuardFires:
    """The guard must be able to fail — one nobody proved could fire is not a
    guard (#2658)."""

    def test_worktree_without_venv_aborts(self, tmp_path):
        result = _run(_fake_checkout(tmp_path, linked_worktree=True, has_venv=False))
        assert result.returncode != 0, "a venv-less worktree run must fail closed"
        assert GUARD_HEADLINE in result.stderr

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
        # The explanation wraps across echo lines; compare on collapsed whitespace
        # so the assertion pins the words, not the line breaks.
        assert "resolves from the PRIMARY checkout" in " ".join(result.stderr.split())

    def test_no_tests_run_when_the_guard_fires(self, tmp_path):
        result = _run(_fake_checkout(tmp_path, linked_worktree=True, has_venv=False))
        assert "passed" not in result.stdout


class TestGuardDoesNotOverreach:
    """Scoped to linked worktrees missing a venv, and nothing else."""

    def test_worktree_with_venv_passes_the_guard(self, tmp_path):
        """Whatever the later interpreter-pin check makes of the fake venv, the
        worktree guard itself must stay silent once `.venv/bin/pytest` exists."""
        result = _run(_fake_checkout(tmp_path, linked_worktree=True, has_venv=True))
        assert GUARD_HEADLINE not in result.stderr

    def test_worktree_with_incomplete_venv_is_refused(self, tmp_path):
        """A bare `.venv` directory with no `bin/pytest` is what a failed
        `uv sync` leaves behind; it must be refused, and the message must name
        the missing `bin/pytest` rather than the directory."""
        root = _fake_checkout(tmp_path, linked_worktree=True, has_venv=False)
        (root / ".venv").mkdir()
        result = _run(root)
        assert result.returncode != 0
        assert GUARD_HEADLINE in result.stderr
        assert str(root / ".venv" / "bin" / "pytest") in result.stderr

    def test_primary_checkout_without_venv_is_not_blocked_by_this_guard(self, tmp_path):
        """`.git` as a directory means primary checkout: it falls through to the
        PATH pytest as before, rather than being newly refused."""
        result = _run(_fake_checkout(tmp_path, linked_worktree=False, has_venv=False))
        assert GUARD_HEADLINE not in result.stderr

    def test_real_primary_checkout_still_runs(self):
        result = subprocess.run(
            [str(SCRIPT), "--version"], cwd=str(REPO_ROOT), capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert GUARD_HEADLINE not in result.stderr
