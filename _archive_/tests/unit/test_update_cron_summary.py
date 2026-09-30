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


# ---------------------------------------------------------------------------
# #2845: failure-branch warnings render, and the `suppressed:` trailer
# ---------------------------------------------------------------------------


def test_failed_run_with_only_warnings_still_renders_bullets(tmp_path, capsys):
    """run.py:704/:1999/:2014's shape: success=False, empty errors, warnings
    present. Without the failure-branch warnings render, the fix session gets
    an empty warning list on exactly the runs that most need one."""
    result = UpdateResult(
        success=False,
        errors=[],
        warnings=["Worker not running after install and kickstart retry", "Worker install failed"],
    )
    rc = _run_main_cron(result, tmp_path, sha_return="abc1234")
    out = capsys.readouterr().out

    assert rc == 1
    assert "update failed at abc1234" in out
    assert "⚠️ Worker not running after install and kickstart retry" in out
    assert "⚠️ Worker install failed" in out


def test_suppressed_trailer_appears_on_clean_run_with_active_state(tmp_path, capsys):
    """The modal suppressed case: nothing else wrong (empty warnings/errors),
    but a key is genuinely suppressed. The trailer must still appear — the
    `else: status = "update successful"` branch is where Risk 4 lives."""
    from scripts.update import warn_state

    (tmp_path / "data").mkdir()
    warn_state.should_emit("gws-auth", "needs_auth:none", tmp_path)

    result = UpdateResult(success=True, warnings=[])
    rc = _run_main_cron(result, tmp_path, sha_return="abc1234")
    out = capsys.readouterr().out

    assert rc == 0
    assert warn_state.SUPPRESSED_PREFIX in out
    assert "gws-auth" in out
    # data/update.txt must exist — the write gate widened to fire on a
    # non-empty suppressed map even though result.warnings is empty.
    assert (tmp_path / "data" / "update.txt").exists()
    log_text = (tmp_path / "data" / "update.txt").read_text()
    assert warn_state.SUPPRESSED_PREFIX in log_text


def test_suppressed_trailer_names_only_non_emitted_keys(tmp_path, capsys):
    """One key emitting THIS run + one genuinely suppressed key: the trailer
    must name only the second (the emission-subtraction test — Race 3's
    inverse hazard)."""
    from scripts.update import warn_state

    (tmp_path / "data").mkdir()
    # gws-auth emits THIS run (first warn).
    warn_state.should_emit("gws-auth", "needs_auth:none", tmp_path)
    # env-completeness was already suppressed from an earlier run.
    warn_state.should_emit("env-completeness", "missing:1", tmp_path)

    result = UpdateResult(
        success=True,
        warnings=["gws auth: needs setup"],
        warn_keys_emitted={"gws-auth"},
    )
    rc = _run_main_cron(result, tmp_path, sha_return="abc1234")
    out = capsys.readouterr().out

    assert rc == 0
    assert warn_state.SUPPRESSED_PREFIX in out
    assert "env-completeness" in out
    # gws-auth emitted this run — must NOT also appear in the trailer.
    trailer_line = next(
        line for line in out.splitlines() if line.strip().startswith(warn_state.SUPPRESSED_PREFIX)
    )
    assert "gws-auth" not in trailer_line


def test_suppressed_trailer_lists_exactly_the_live_keys_when_none_emitted(tmp_path, capsys):
    """Operator-visible-surface guard for issue #3004: a warn_state map
    holding only keys live callers can still produce, with a run that
    emitted none of them, must list exactly those keys in the trailer —
    positively, not by asserting the absence of the retired ACL key."""
    from scripts.update import warn_state

    (tmp_path / "data").mkdir()
    warn_state.should_emit("calendar-config", "misconfigured:1", tmp_path)
    warn_state.should_emit("env-completeness", "missing:1", tmp_path)
    warn_state.should_emit("google-token", "expired:1", tmp_path)

    result = UpdateResult(success=True, warnings=[], warn_keys_emitted=set())
    rc = _run_main_cron(result, tmp_path, sha_return="abc1234")
    out = capsys.readouterr().out

    assert rc == 0
    trailer_line = next(
        line for line in out.splitlines() if line.strip().startswith(warn_state.SUPPRESSED_PREFIX)
    )
    for key in ("calendar-config", "env-completeness", "google-token"):
        assert key in trailer_line


def test_no_trailer_when_all_active_keys_emitted_this_run(tmp_path, capsys):
    """Every entry in active() also appears in warn_keys_emitted: no
    trailer, and the write gate is not widened on that account alone."""
    from scripts.update import warn_state

    (tmp_path / "data").mkdir()
    warn_state.should_emit("gws-auth", "needs_auth:none", tmp_path)

    result = UpdateResult(
        success=True,
        warnings=["gws auth: needs setup"],
        warn_keys_emitted={"gws-auth"},
    )
    rc = _run_main_cron(result, tmp_path, sha_return="abc1234")
    out = capsys.readouterr().out

    assert rc == 0
    assert warn_state.SUPPRESSED_PREFIX not in out


def test_trailer_is_inert_to_extract_update_warnings(tmp_path, capsys):
    """The real trailer, fed through extract_update_warnings, must yield []
    — including when it follows a real (N warnings) block."""
    from bridge.update import extract_update_warnings
    from scripts.update import warn_state

    (tmp_path / "data").mkdir()
    warn_state.should_emit("gws-auth", "needs_auth:none", tmp_path)

    result = UpdateResult(success=True, warnings=["a real warning"])
    _run_main_cron(result, tmp_path, sha_return="abc1234")
    out = capsys.readouterr().out
    status_lines = [line for line in out.split("\n") if line.strip()]

    extracted = extract_update_warnings(status_lines)
    assert extracted == ["a real warning"]
