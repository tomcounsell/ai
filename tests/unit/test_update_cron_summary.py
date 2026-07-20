"""Unit tests for the cron-mode summary builder in ``scripts/update/run.py``.

Regression coverage for the ``up to date at unknown`` bug: on the
``remote-update.sh`` path the shell wrapper does the ``git pull`` itself and
invokes the orchestrator with ``--no-pull``, so ``UpdateResult.git_result`` is
never assigned and stays ``None``. The summary must still print the live HEAD
short SHA (via the standalone ``git.get_short_sha``) rather than the literal
word ``unknown``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.update.run as run_mod
from scripts.update.run import UpdateResult


@pytest.fixture(autouse=True)
def _reset_log_globals():
    """Isolate the module-level log-buffer globals mutated by ``main()``."""
    saved_buffer = list(run_mod._log_buffer)
    saved_flag = run_mod._log_to_buffer
    try:
        yield
    finally:
        run_mod._log_buffer = saved_buffer
        run_mod._log_to_buffer = saved_flag


def _run_main_cron(result: UpdateResult, tmp_path: Path, sha_return, extra_argv=()):
    """Drive ``main()`` down the ``--cron --no-pull`` summary path.

    ``run_update`` is stubbed to (a) push a line into the buffer so the summary
    block's non-empty ``_log_buffer`` guard passes, and (b) return the supplied
    result. ``git.get_short_sha`` is stubbed via ``sha_return`` (a value to
    return, or an ``Exception`` instance to raise).
    """

    def _fake_run_update(project_dir, config):
        run_mod._log_buffer.append("[update] simulated run")
        return result

    def _fake_get_short_sha(project_dir, sha="HEAD"):
        if isinstance(sha_return, Exception):
            raise sha_return
        return sha_return

    argv = ["run.py", "--cron", "--no-pull", "--project-dir", str(tmp_path), *extra_argv]
    with (
        patch.object(run_mod, "run_update", _fake_run_update),
        patch.object(run_mod.git, "get_short_sha", _fake_get_short_sha),
        patch("sys.argv", argv),
    ):
        return run_mod.main()


def test_no_pull_summary_shows_real_sha(tmp_path, capsys):
    """git_result is None (the --no-pull path) yet the real HEAD SHA is shown."""
    result = UpdateResult(success=True, warnings=["gws auth not configured"])
    rc = _run_main_cron(result, tmp_path, sha_return="abc1234")
    out = capsys.readouterr().out

    assert rc == 0
    assert "up to date at abc1234" in out
    assert "unknown" not in out


def test_no_pull_summary_falls_back_to_unknown_on_git_failure(tmp_path, capsys):
    """If the git call itself fails, the SHA gracefully falls back to 'unknown'."""
    result = UpdateResult(success=True, warnings=["gws auth not configured"])
    rc = _run_main_cron(result, tmp_path, sha_return=RuntimeError("git exploded"))
    out = capsys.readouterr().out

    assert rc == 0
    assert "up to date at unknown" in out


def test_pull_path_reports_updated_with_commit_count(tmp_path, capsys):
    """When the orchestrator did the pull, a non-zero commit count reads 'updated to'."""
    git_result = run_mod.git.GitPullResult(
        success=True,
        before_sha="0000000",
        after_sha="def5678",
        commit_count=3,
        commits=["a", "b", "c"],
        stashed=False,
        stash_restored=False,
    )
    result = UpdateResult(
        success=True,
        git_result=git_result,
        warnings=["gws auth not configured"],
    )
    rc = _run_main_cron(result, tmp_path, sha_return="def5678")
    out = capsys.readouterr().out

    assert rc == 0
    assert "updated to def5678" in out
