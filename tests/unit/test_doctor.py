"""Unit tests for tools/doctor.py health check CLI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.doctor import (
    CheckResult,
    format_json,
    format_text,
    get_checks,
    install_pre_push_hook,
    main,
    run_checks,
)

# ---------------------------------------------------------------------------
# CheckResult dataclass
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_to_dict_basic(self):
        r = CheckResult(name="test", category="Env", passed=True, message="ok")
        d = r.to_dict()
        assert d["name"] == "test"
        assert d["category"] == "Env"
        assert d["passed"] is True
        assert d["message"] == "ok"
        assert "fix" not in d

    def test_to_dict_with_fix(self):
        r = CheckResult(name="x", category="C", passed=False, message="bad", fix="do this")
        d = r.to_dict()
        assert d["fix"] == "do this"

    def test_to_dict_no_fix_when_none(self):
        r = CheckResult(name="x", category="C", passed=True, message="ok", fix=None)
        d = r.to_dict()
        assert "fix" not in d


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


class TestFormatText:
    def test_all_pass(self):
        results = [
            CheckResult("a", "Cat1", True, "good"),
            CheckResult("b", "Cat1", True, "fine"),
        ]
        text = format_text(results)
        assert "[PASS]" in text
        assert "[FAIL]" not in text
        assert "2/2 passed" in text
        assert "All checks passed." in text

    def test_with_failure(self):
        results = [
            CheckResult("a", "Cat1", True, "good"),
            CheckResult("b", "Cat2", False, "bad", fix="fix it"),
        ]
        text = format_text(results)
        assert "[PASS]" in text
        assert "[FAIL]" in text
        assert "1/2 passed" in text
        assert "Fix: fix it" in text

    def test_groups_by_category(self):
        results = [
            CheckResult("a", "Environment", True, "ok"),
            CheckResult("b", "Services", True, "ok"),
            CheckResult("c", "Environment", True, "ok"),
        ]
        text = format_text(results)
        assert "--- Environment ---" in text
        assert "--- Services ---" in text

    def test_empty_results(self):
        text = format_text([])
        assert "0/0 passed" in text


class TestFormatJson:
    def test_valid_json(self):
        results = [
            CheckResult("a", "Cat", True, "ok"),
            CheckResult("b", "Cat", False, "fail", fix="do x"),
        ]
        output = format_json(results)
        data = json.loads(output)
        assert data["passed"] is False
        assert data["summary"]["total"] == 2
        assert data["summary"]["passed"] == 1
        assert data["summary"]["failed"] == 1
        assert len(data["checks"]) == 2

    def test_all_pass_json(self):
        results = [CheckResult("a", "Cat", True, "ok")]
        data = json.loads(format_json(results))
        assert data["passed"] is True

    def test_empty_results_json(self):
        data = json.loads(format_json([]))
        assert data["passed"] is True
        assert data["summary"]["total"] == 0


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------


class TestGetChecks:
    def test_default_checks_count(self):
        checks = get_checks()
        # Should have checks for env, services, auth, resources (no quality)
        assert len(checks) >= 10

    def test_quality_adds_checks(self):
        default = get_checks()
        with_quality = get_checks(quality=True)
        # Quality adds ruff_lint, ruff_format, pytest
        assert len(with_quality) == len(default) + 3

    def test_quick_flag_passed_through(self):
        # Just verify it doesn't crash with quick=True
        checks = get_checks(quick=True)
        assert len(checks) >= 10


# ---------------------------------------------------------------------------
# run_checks resilience
# ---------------------------------------------------------------------------


class TestRunChecks:
    def test_single_check_crash_does_not_stop_others(self):
        """A crashing check should produce a failed result, not crash the run."""

        def crashing_check():
            raise RuntimeError("boom")

        def passing_check():
            return CheckResult("ok", "Test", True, "fine")

        with patch("tools.doctor.get_checks", return_value=[crashing_check, passing_check]):
            results = run_checks()

        assert len(results) == 2
        assert results[0].passed is False
        assert "boom" in results[0].message
        assert results[1].passed is True

    def test_check_returning_list(self):
        """Checks that return list[CheckResult] should be flattened."""

        def multi_check():
            return [
                CheckResult("a", "T", True, "ok"),
                CheckResult("b", "T", False, "bad"),
            ]

        with patch("tools.doctor.get_checks", return_value=[multi_check]):
            results = run_checks()

        assert len(results) == 2


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------


class TestMain:
    def test_exit_code_zero_when_all_pass(self):
        with patch("tools.doctor.run_checks") as mock_run:
            mock_run.return_value = [CheckResult("a", "T", True, "ok")]
            code = main(["--quick"])
        assert code == 0

    def test_exit_code_one_when_any_fail(self):
        with patch("tools.doctor.run_checks") as mock_run:
            mock_run.return_value = [
                CheckResult("a", "T", True, "ok"),
                CheckResult("b", "T", False, "bad"),
            ]
            code = main(["--quick"])
        assert code == 1

    def test_json_flag(self, capsys):
        with patch("tools.doctor.run_checks") as mock_run:
            mock_run.return_value = [CheckResult("a", "T", True, "ok")]
            code = main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["passed"] is True
        assert code == 0

    def test_quick_flag_passed_to_run_checks(self):
        with patch("tools.doctor.run_checks") as mock_run:
            mock_run.return_value = []
            main(["--quick"])
        mock_run.assert_called_once_with(quick=True, quality=False)

    def test_quality_flag_passed_to_run_checks(self):
        with patch("tools.doctor.run_checks") as mock_run:
            mock_run.return_value = []
            main(["--quality"])
        mock_run.assert_called_once_with(quick=False, quality=True)


# ---------------------------------------------------------------------------
# Git hook installer
# ---------------------------------------------------------------------------


class TestInstallHook:
    def test_install_creates_hook_file(self, tmp_path):
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)

        with patch("tools.doctor.PROJECT_DIR", tmp_path):
            ok = install_pre_push_hook()

        assert ok is True
        hook = git_dir / "pre-push"
        assert hook.exists()
        assert hook.stat().st_mode & 0o111  # executable
        content = hook.read_text()
        assert "tools.doctor" in content
        assert "--quick" in content

    def test_install_fails_without_git_dir(self, tmp_path):
        with patch("tools.doctor.PROJECT_DIR", tmp_path):
            ok = install_pre_push_hook()
        assert ok is False

    def test_install_hook_via_cli(self, tmp_path):
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)

        with patch("tools.doctor.PROJECT_DIR", tmp_path):
            code = main(["--install-hook"])

        assert code == 0
        assert (git_dir / "pre-push").exists()


# ---------------------------------------------------------------------------
# Individual check wrappers (import error resilience)
# ---------------------------------------------------------------------------


class TestCheckWrapperResilience:
    """Test that check wrappers handle import failures gracefully."""

    def test_redis_check_handles_import_error(self):
        with patch.dict("sys.modules", {"popoto": None, "popoto.redis_db": None}):
            # Should not crash
            from tools.doctor import _check_redis

            result = _check_redis()
            assert isinstance(result, CheckResult)
            assert result.category == "Services"

    def test_telegram_session_quick_mode(self, tmp_path):
        """Quick mode just checks for session files, no Telethon import."""
        from tools.doctor import _check_telegram_session

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch("tools.doctor.PROJECT_DIR", tmp_path):
            result = _check_telegram_session(quick=True)

        assert result.passed is False
        assert "0 session" in result.message or "No session" in result.message

        # Now create a session file
        (data_dir / "test.session").touch()
        with patch("tools.doctor.PROJECT_DIR", tmp_path):
            result = _check_telegram_session(quick=True)

        assert result.passed is True


# ---------------------------------------------------------------------------
# worktree interpreter drift check (#2572, #2617)
# ---------------------------------------------------------------------------


def _fake_checkout(root, pin="3.14", main_venv="3.14.3"):
    """Build a fake main checkout with an optional pin and .venv."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    if pin is not None:
        (root / ".python-version").write_text(f"{pin}\n")
    if main_venv is not None:
        venv = root / ".venv"
        venv.mkdir(exist_ok=True)
        (venv / "pyvenv.cfg").write_text(f"version_info = {main_venv}\n")
    return root


def _fake_worktree_venv(root, relative, version):
    venv = root / relative / ".venv"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(f"version_info = {version}\n")
    return venv


class TestCheckWorktreeInterpreters:
    """The check measures every venv against the committed pin (#2617).

    #2572 compared worktrees against the main checkout's venv, so a checkout
    whose own venv had drifted off the pin reported all-clear while every
    environment on the machine was wrong.
    """

    def _run(self, root):
        from tools.doctor import _check_worktree_interpreters

        with patch("tools.doctor.PROJECT_DIR", root):
            return _check_worktree_interpreters()

    def test_passes_when_everything_is_on_the_pin(self, tmp_path):
        root = _fake_checkout(tmp_path)
        _fake_worktree_venv(root, ".worktrees/lane-a", "3.14.3")
        _fake_worktree_venv(root, ".claude/worktrees/agent-x", "3.14")
        result = self._run(root)
        assert result.passed is True, result.message
        assert "3.14" in result.message

    def test_fails_on_a_worktree_off_the_pin(self, tmp_path):
        root = _fake_checkout(tmp_path)
        _fake_worktree_venv(root, ".worktrees/lane-a", "3.15.0")
        result = self._run(root)
        assert result.passed is False
        assert "lane-a" in result.message
        assert "3.15" in result.message

    def test_fails_on_a_harness_worktree_off_the_pin(self, tmp_path):
        """`.claude/worktrees/` venvs are unprovisioned by design — and drift."""
        root = _fake_checkout(tmp_path)
        _fake_worktree_venv(root, ".claude/worktrees/agent-x", "3.13.2")
        result = self._run(root)
        assert result.passed is False
        assert "agent-x" in result.message

    def test_fails_when_the_main_checkout_venv_is_off_the_pin(self, tmp_path):
        root = _fake_checkout(tmp_path, pin="3.14", main_venv="3.13.2")
        result = self._run(root)
        assert result.passed is False
        assert "main checkout" in result.message

    def test_unpinned_repo_falls_back_to_the_checkout_venv(self, tmp_path):
        root = _fake_checkout(tmp_path, pin=None, main_venv="3.13.2")
        _fake_worktree_venv(root, ".worktrees/lane-a", "3.14.3")
        result = self._run(root)
        assert result.passed is False
        assert "lane-a" in result.message

    def test_reports_when_no_reference_is_resolvable(self, tmp_path):
        root = _fake_checkout(tmp_path, pin=None, main_venv=None)
        result = self._run(root)
        assert result.passed is False
        assert result.fix


# ---------------------------------------------------------------------------
# CLAUDE_CODE_OAUTH_TOKEN check
# ---------------------------------------------------------------------------


class TestCheckClaudeOauthToken:
    """Tests for the CLAUDE_CODE_OAUTH_TOKEN presence+prefix health check.

    The check is warning-only (passed=True with a fix message) for absent/malformed
    tokens — it never hard-fails the run, because the token is optional on
    non-interactive machines.
    """

    def test_valid_token_passes(self):
        """Token present with correct prefix → check passes, no fix needed."""
        from tools.doctor import _check_claude_oauth_token

        with patch.dict(
            "os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-abc123"}, clear=False
        ):
            result = _check_claude_oauth_token()

        assert result.passed is True
        assert result.name == "claude_oauth_token"
        assert result.category == "Auth"
        assert result.fix is None

    def test_token_absent_warns_with_remediation(self):
        """Token absent → warning (passed=True) with 'claude setup-token' remediation."""
        from tools.doctor import _check_claude_oauth_token

        env_without_token = {
            k: v for k, v in __import__("os").environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"
        }
        with patch.dict("os.environ", env_without_token, clear=True):
            result = _check_claude_oauth_token()

        assert result.passed is True  # warning, not failure
        assert result.fix is not None
        assert "claude setup-token" in result.fix

    def test_wrong_prefix_warns_with_remediation(self):
        """Token present but wrong prefix → warning with prefix note and remediation."""
        from tools.doctor import _check_claude_oauth_token

        with patch.dict(
            "os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-api03-wrongformat"}, clear=False
        ):
            result = _check_claude_oauth_token()

        assert result.passed is True  # warning, not failure
        assert result.fix is not None
        assert "claude setup-token" in result.fix
        # Should mention the malformed prefix
        assert (
            "sk-ant-oat01-" in result.fix
            or "prefix" in result.fix.lower()
            or "malformed" in result.fix.lower()
        )


# ---------------------------------------------------------------------------
# claude-binary-attribution check (issue #2100)
# ---------------------------------------------------------------------------


class TestCheckClaudeBinaryAttribution:
    """Tests for the _check_claude_binary_attribution advisory check.

    Always passes (advisory), renders the resolved binary display + realpath,
    and raises a warning-level note (via the fix field) when the binary basename
    is a bare version number (macOS shows the child process as that version). The
    per-session TLS-streak SCAN is stubbed so the check never touches real Redis.
    """

    def _mock_redis_empty(self):
        """A Redis stub whose streak SCAN returns nothing."""
        mock_r = MagicMock()
        mock_r.scan.return_value = (0, [])
        mock_r.get.return_value = None
        return mock_r

    def test_bare_version_basename_passes_but_warns(self):
        """A /versions/2.1.202 realpath → passes, but fix note flags the version."""
        from tools.doctor import _check_claude_binary_attribution

        with (
            patch(
                "agent.session_runner.harness.claude_diagnostics.shutil.which",
                lambda cmd: "/Users/x/.local/bin/claude",
            ),
            patch(
                "agent.session_runner.harness.claude_diagnostics.os.path.realpath",
                lambda p: "/Users/x/.local/share/claude/versions/2.1.202",
            ),
            patch("popoto.redis_db.POPOTO_REDIS_DB", self._mock_redis_empty()),
        ):
            result = _check_claude_binary_attribution()

        assert result.passed is True
        assert result.name == "claude_binary_attribution"
        assert result.category == "Auth"
        # The advisory note fires for a bare-version basename.
        assert result.fix is not None
        assert "2.1.202" in result.fix
        # Message renders the Claude Code attribution + realpath.
        assert "Claude Code CLI 2.1.202" in result.message

    def test_normal_basename_passes_cleanly(self):
        """A normal basename (claude) → passes with no warning note."""
        from tools.doctor import _check_claude_binary_attribution

        with (
            patch(
                "agent.session_runner.harness.claude_diagnostics.shutil.which",
                lambda cmd: "/usr/local/bin/claude",
            ),
            patch(
                "agent.session_runner.harness.claude_diagnostics.os.path.realpath",
                lambda p: "/usr/local/bin/claude",
            ),
            patch("popoto.redis_db.POPOTO_REDIS_DB", self._mock_redis_empty()),
        ):
            result = _check_claude_binary_attribution()

        assert result.passed is True
        assert result.name == "claude_binary_attribution"
        assert result.fix is None

    def test_registered_in_default_checks(self):
        """The check is wired into the default check registry."""
        from tools.doctor import _check_claude_binary_attribution, get_checks

        assert _check_claude_binary_attribution in get_checks()


# ---------------------------------------------------------------------------
# session-archive-freshness check (issue #1825)
# ---------------------------------------------------------------------------


class TestCheckSessionArchiveFreshness:
    """Tests for the session-archive-freshness doctor check (Task 4 of
    docs/plans/session-archive-sqlite.md). It delegates entirely to
    `agent.session_archive.get_archive_status()`, so tests patch that
    function's return value rather than touching a real SQLite file."""

    def _status(self, **overrides) -> dict:
        base = {
            "db_path": "/tmp/session_archive.db",
            "exists": True,
            "row_count": 5,
            "last_export_ts": 1000.0,
            "last_export_age_s": 10.0,
            "last_periodic_export_ts": 1000.0,
            "last_periodic_export_age_s": 10.0,
            "kind": "periodic",
            "healthy": True,
        }
        base.update(overrides)
        return base

    def test_healthy_archive_passes(self):
        from tools.doctor import _check_session_archive_freshness

        with patch(
            "agent.session_archive.get_archive_status",
            return_value=self._status(healthy=True, last_export_age_s=10.0),
        ):
            result = _check_session_archive_freshness()

        assert result.passed is True
        assert result.name == "session-archive-freshness"
        assert result.category == "Services"
        assert "fresh" in result.message.lower()
        assert result.fix is None

    def test_stale_archive_fails_with_fix(self):
        from tools.doctor import _check_session_archive_freshness

        with patch(
            "agent.session_archive.get_archive_status",
            return_value=self._status(
                healthy=False, last_export_age_s=99999.0, last_periodic_export_age_s=99999.0
            ),
        ):
            result = _check_session_archive_freshness()

        assert result.passed is False
        assert "stale" in result.message.lower()
        assert result.fix is not None

    def test_missing_archive_fails_with_fix(self):
        from tools.doctor import _check_session_archive_freshness

        with patch(
            "agent.session_archive.get_archive_status",
            return_value=self._status(
                exists=False,
                row_count=0,
                last_export_ts=None,
                last_export_age_s=None,
                kind=None,
                healthy=False,
            ),
        ):
            result = _check_session_archive_freshness()

        assert result.passed is False
        assert "does not exist" in result.message
        assert result.fix is not None
        assert "worker-start" in result.fix

    def test_never_exported_fails_gracefully(self):
        """exists=True but last_export_ts=None (schema created, never written)."""
        from tools.doctor import _check_session_archive_freshness

        with patch(
            "agent.session_archive.get_archive_status",
            return_value=self._status(
                healthy=False,
                last_export_ts=None,
                last_export_age_s=None,
                last_periodic_export_ts=None,
                last_periodic_export_age_s=None,
            ),
        ):
            result = _check_session_archive_freshness()

        assert result.passed is False
        assert "never" in result.message.lower()

    def test_get_checks_includes_session_archive_freshness(self):
        """The check must be wired into the default registry (Services category)."""
        from tools.doctor import get_checks

        names = [getattr(fn, "__name__", "") for fn in get_checks()]
        assert "_check_session_archive_freshness" in names


class TestCheckAgentSessionIndexDrift:
    """Tests for the AgentSession index-drift doctor check (#2086). Delegates
    entirely to `agent.index_drift.reconcile_agent_session_index()`, so tests
    patch that function's return value rather than touching real Redis."""

    def test_equal_counts_passes(self):
        from tools.doctor import _check_agentsession_index_drift

        with patch(
            "agent.index_drift.reconcile_agent_session_index",
            return_value=(5, 5, False, False),
        ):
            result = _check_agentsession_index_drift()

        assert result.passed is True
        assert result.name == "agentsession-index-drift"
        assert result.category == "Services"
        assert "5" in result.message
        assert result.fix is None

    def test_drift_fails_with_both_counts_and_fix_hint(self):
        from tools.doctor import _check_agentsession_index_drift

        with patch(
            "agent.index_drift.reconcile_agent_session_index",
            return_value=(11, 0, True, False),
        ):
            result = _check_agentsession_index_drift()

        assert result.passed is False
        assert "11" in result.message
        assert "0" in result.message
        assert result.fix is not None
        assert "repair_indexes" in result.fix

    def test_truncated_scan_fails_without_claiming_no_drift(self):
        from tools.doctor import _check_agentsession_index_drift

        with patch(
            "agent.index_drift.reconcile_agent_session_index",
            return_value=(100, 0, False, True),
        ):
            result = _check_agentsession_index_drift()

        assert result.passed is False
        assert "incomplete" in result.message.lower()
        assert result.fix is not None

    def test_reconcile_exception_yields_failing_checkresult_not_crashed_run(self):
        """A reconcile exception must be handled by run_checks' existing
        per-check try/except -- a failing CheckResult, not an aborted run."""
        from tools.doctor import _check_agentsession_index_drift, run_checks

        with (
            patch("tools.doctor.get_checks", return_value=[_check_agentsession_index_drift]),
            patch(
                "agent.index_drift.reconcile_agent_session_index",
                side_effect=RuntimeError("boom"),
            ),
        ):
            results = run_checks()

        assert len(results) == 1
        assert results[0].passed is False
        assert "boom" in results[0].message

    def test_get_checks_includes_agentsession_index_drift(self):
        """The check must be wired into the default registry (Services category)."""
        from tools.doctor import get_checks

        names = [getattr(fn, "__name__", "") for fn in get_checks()]
        assert "_check_agentsession_index_drift" in names


# ---------------------------------------------------------------------------
# Redis flush guard + ACL checks (#2645)
# ---------------------------------------------------------------------------


class TestCheckRedisFlushGuard:
    """The compensating control for Layer 1's named primary hazard.

    The boot shim swallows every exception, so a broken install is
    indistinguishable from a working one on the filesystem. This check is the
    only thing that can see the difference — which makes "the check itself is
    silently inert" the recursive version of the incident it guards against.
    These cases exist so that hardcoding the liveness probe, or dropping the
    check from `get_checks()`, cannot pass.
    """

    def _run(self, venvs, live_by_path):
        """Drive the check over fake venvs with a scripted liveness probe."""
        from tools.doctor import _check_redis_flush_guard

        def fake_subprocess_run(argv, **kwargs):
            python_bin = argv[0]
            venv = str(Path(python_bin).parent.parent)
            return SimpleNamespace(
                returncode=0,
                stdout="True\n" if live_by_path[venv] else "False\n",
                stderr="",
            )

        with (
            patch("scripts.update.redis_flush_guard_pth.discover_venvs", return_value=venvs),
            patch("agent.worktree_manager.main_checkout_venv", return_value=None),
            patch("tools.doctor.subprocess.run", side_effect=fake_subprocess_run),
        ):
            return _check_redis_flush_guard()

    @staticmethod
    def _fake_venv(root, name):
        venv = root / name
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").write_text("#!/bin/sh\n")
        return venv

    def test_fails_and_names_the_unguarded_venv(self, tmp_path):
        guarded = self._fake_venv(tmp_path, "guarded-venv")
        unguarded = self._fake_venv(tmp_path, "unguarded-venv")
        result = self._run(
            [guarded, unguarded],
            {str(guarded): True, str(unguarded): False},
        )
        assert result.passed is False
        assert str(unguarded) in result.message
        assert "1/2" in result.message
        # Per-venv remediation: an operator holding one unhealed harness
        # worktree must be able to heal just that one.
        assert "--venv" in result.fix
        assert str(unguarded) in result.fix
        assert str(guarded) not in result.fix

    def test_passes_when_every_venv_is_live(self, tmp_path):
        a = self._fake_venv(tmp_path, "a")
        b = self._fake_venv(tmp_path, "b")
        result = self._run([a, b], {str(a): True, str(b): True})
        assert result.passed is True, result.message
        assert "2 venv(s) guarded" in result.message

    def test_a_nonzero_probe_exit_counts_as_unguarded(self, tmp_path):
        """`returncode == 0 AND stdout == "True"` — not either half alone."""
        from tools.doctor import _check_redis_flush_guard

        venv = self._fake_venv(tmp_path, "crashy")
        with (
            patch("scripts.update.redis_flush_guard_pth.discover_venvs", return_value=[venv]),
            patch("agent.worktree_manager.main_checkout_venv", return_value=None),
            patch(
                "tools.doctor.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stdout="True\n", stderr="boom"),
            ),
        ):
            result = _check_redis_flush_guard()
        assert result.passed is False
        assert str(venv) in result.message


class TestRedisChecksAreRegistered:
    """Both checks correct in isolation is worth nothing if neither runs.

    Deleting a check from `get_checks()` (or breaking the `checks.insert`
    anchor `_check_gws_auth` is registered against) left the whole doctor
    suite green before this case existed.
    """

    def test_both_redis_checks_are_in_get_checks(self):
        from tools.doctor import _check_gws_auth, _check_redis_flush_guard

        registered = get_checks()
        assert _check_redis_flush_guard in registered
        assert _check_gws_auth in registered


class TestCheckGwsAuth:
    """`_check_gws_auth` (#2845) — the retrieval half of the `gws-auth`
    warn_state suppression: lazy-import the `scripts.update.*` module, degrade
    a missing module or an unreachable service to `passed=True`, never crash."""

    def _run(self, gws_result=None, side_effect=None):
        from tools.doctor import _check_gws_auth

        with patch(
            "scripts.update.gws_auth.configure_gws_auth",
            return_value=gws_result,
            side_effect=side_effect,
        ):
            return _check_gws_auth()

    def test_needs_auth_fails(self):
        result = self._run(
            SimpleNamespace(
                success=True,
                action="needs_auth",
                detail="gws auth setup --login   (or: gws auth setup && gws auth login)",
                error=None,
            )
        )
        assert result.passed is False
        assert "gws auth setup" in result.message
        assert result.fix is not None

    def test_already_ok_passes(self):
        result = self._run(
            SimpleNamespace(success=True, action="already_ok", detail="authenticated", error=None)
        )
        assert result.passed is True
        assert "authenticated" in result.message

    def test_skipped_is_a_passing_skip(self):
        result = self._run(
            SimpleNamespace(success=True, action="skipped", detail="gws not on PATH", error=None)
        )
        assert result.passed is True
        assert "skipped" in result.message

    def test_raising_call_is_a_passing_skip(self):
        """`configure_gws_auth()` itself raising — the call-level except."""
        result = self._run(side_effect=RuntimeError("gws binary crashed"))
        assert result.passed is True
        assert "could not run" in result.message

    def test_raising_import_is_a_passing_skip(self):
        """A missing `scripts.update.gws_auth` module — the import-level
        except. A machine without the module degrades, never crashes."""
        import sys

        from tools.doctor import _check_gws_auth

        with patch.dict(sys.modules, {"scripts.update.gws_auth": None}):
            result = _check_gws_auth()
        assert result.passed is True
        assert "not available yet" in result.message

    def test_check_gws_auth_absent_from_quick_checks(self):
        from tools.doctor import _check_gws_auth

        assert _check_gws_auth not in get_checks(quick=True)
        assert _check_gws_auth in get_checks(quick=False)


# ---------------------------------------------------------------------------
# LLM routing section (#3410)
# ---------------------------------------------------------------------------


class TestCheckLLMRouting:
    """The "LLM routing" section: every declared site, routes, cache state, daemon."""

    @pytest.fixture(autouse=True)
    def _cold_and_offline(self, monkeypatch):
        from tools import improvement_eligibility

        improvement_eligibility._clear_cache()

        def _no_gh(*args, **kwargs):
            raise FileNotFoundError("gh")

        monkeypatch.setattr(improvement_eligibility.subprocess, "run", _no_gh)
        yield
        improvement_eligibility._clear_cache()

    def test_llm_routing_section_cold_client_key_sync(self, monkeypatch):
        """Synchronous, like ``run_checks``: no loop, so a cold client key is a miss."""
        from agent.llm.tasks import Backend, declared_sites
        from tools import doctor
        from tools import improvement_eligibility as elig

        monkeypatch.setattr(doctor, "_ollama_status", lambda: None)

        results = doctor._check_llm_routing(client_key="client-thing")

        assert results and all(r.category == "LLM routing" for r in results)
        by_name = {r.name: r for r in results}
        cache = by_name["eligibility_cache"]
        assert "valor=pinned eligible" in cache.message
        assert "client-thing=miss (no loop; refresh not scheduled)" in cache.message
        assert cache.passed is True
        assert elig._REFRESHING == set(), "the sync path must schedule nothing"

        sites = declared_sites()
        for declared in sites:
            row = by_name[declared.task.site]
            assert f"kind={declared.task.kind.value}" in row.message
            assert f"backend={declared.task.backend.value}" in row.message
            assert "client->anthropic" in row.message
            local = declared.task.backend is not Backend.ANTHROPIC
            if local and declared.task.kind.value != "thinking" and not declared.task.client_only:
                assert f"valor->{declared.task.backend.value} (fallback anthropic)" in row.message
            else:
                assert "valor->anthropic" in row.message

        daemon = by_name["ollama_daemon"]
        ollama_sites = [d.task.site for d in sites if d.task.backend is Backend.OLLAMA]
        assert daemon.passed is (not ollama_sites)
        assert "local_typed_hard_s=" in daemon.message
        for site in ollama_sites:
            assert site in daemon.message

    def test_daemon_row_reports_the_loaded_model_and_keep_alive(self, monkeypatch):
        from config.models import OLLAMA_CLASSIFIER_MODEL
        from config.settings import settings
        from tools import doctor

        monkeypatch.setattr(
            doctor,
            "_ollama_status",
            lambda: {
                "pulled": [OLLAMA_CLASSIFIER_MODEL, "other:1b"],
                "loaded": {OLLAMA_CLASSIFIER_MODEL: "2099-01-01T00:00:00Z"},
            },
        )
        results = doctor._check_llm_routing(client_key="client-thing")
        daemon = {r.name: r for r in results}["ollama_daemon"]
        assert daemon.passed is True
        assert f"loaded={OLLAMA_CLASSIFIER_MODEL}" in daemon.message
        assert "expires_at=2099-01-01T00:00:00Z" in daemon.message
        assert f"local_typed_hard_s={settings.timeouts.local_typed_hard_s}" in daemon.message

    def test_a_cached_client_answer_renders_as_a_hit(self, monkeypatch):
        from tools import doctor
        from tools import improvement_eligibility as elig

        elig._CACHE["client-thing"] = (False, float("inf"))
        monkeypatch.setattr(doctor, "_ollama_status", lambda: None)
        results = doctor._check_llm_routing(client_key="client-thing")
        cache = {r.name: r for r in results}["eligibility_cache"]
        assert "client-thing=cached private" in cache.message

    def test_registered_in_the_full_run_only(self):
        from tools.doctor import _check_llm_routing

        assert _check_llm_routing in get_checks(quick=False)
        assert _check_llm_routing not in get_checks(quick=True)


class TestLocalEncoderRow:
    """The ``local_encoder`` row (#3420): extra, weights by sha256, one head per site."""

    @pytest.fixture(autouse=True)
    def _cold_and_offline(self, monkeypatch):
        from tools import doctor, improvement_eligibility

        improvement_eligibility._clear_cache()

        def _no_gh(*args, **kwargs):
            raise FileNotFoundError("gh")

        monkeypatch.setattr(improvement_eligibility.subprocess, "run", _no_gh)
        monkeypatch.setattr(doctor, "_ollama_status", lambda: None)
        yield
        improvement_eligibility._clear_cache()

    @pytest.fixture
    def fake_weights(self, tmp_path, monkeypatch):
        """A temp models dir whose files match patched pins; returns ``(dir, contents)``."""
        import hashlib

        from config import models as models_mod

        root = tmp_path / "encoder"
        contents = {name: name.encode() * 2 for name in models_mod.LOCAL_ENCODER_FILES}
        for name, data in contents.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
        pins = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
        monkeypatch.setattr(models_mod, "LOCAL_ENCODER_FILES", pins)
        monkeypatch.setenv("LOCAL_ENCODER_MODELS_DIR", str(root))
        return root, contents

    @pytest.fixture
    def fake_heads(self, tmp_path, monkeypatch):
        from agent.llm.backends import local_encoder

        heads = tmp_path / "heads"
        heads.mkdir()
        monkeypatch.setattr(local_encoder, "HEADS_DIR", heads)
        return heads

    @staticmethod
    def _write_head(heads, site: str) -> None:
        """A head that loads through the leg's validating loader."""
        import json

        from agent.llm.backends import local_encoder
        from config.models import LOCAL_ENCODER_DIM

        head = local_encoder.Head(
            site=site,
            classes=["True", "False"],
            W=[[0.0, 0.0] for _ in range(LOCAL_ENCODER_DIM)],
            b=[0.0, 0.0],
            embedding_model="Xenova/bge-small-en-v1.5",
            embedding_revision="ea104dacec62c0de699686887e3f920caeb4f3e3",
            embedding_sha256=local_encoder.LOCAL_ENCODER_FILES["onnx/model_int8.onnx"],
            run_id="run-doctor",
            n_train=1,
            n_train_real=0,
            reference_model="claude-haiku-test",
            created_at="2026-09-21T00:00:00Z",
        )
        (heads / f"{site}.json").write_text(json.dumps(head.to_dict()))

    @staticmethod
    def _declare(monkeypatch, *sites: str):
        """Pretend ``sites`` are declared ``LOCAL_ENCODER`` classification sites."""
        from agent.llm.tasks import Backend, DeclaredSite, LLMTask, TaskKind
        from tools import doctor

        real = doctor.declared_sites_for_routing()
        extra = [
            DeclaredSite(
                task=LLMTask(
                    site=site, kind=TaskKind.CLASSIFICATION, backend=Backend.LOCAL_ENCODER
                ),
                path="tests/fake.py",
                name="FAKE",
                lineno=1,
            )
            for site in sites
        ]
        monkeypatch.setattr(doctor, "declared_sites_for_routing", lambda: real + extra)

    @staticmethod
    def _row(client_key="client-thing"):
        from tools import doctor

        results = doctor._check_llm_routing(client_key=client_key)
        by_name = {r.name: r for r in results}
        assert by_name["ollama_daemon"].category == "LLM routing"
        return by_name["local_encoder"]

    def test_local_encoder_passes_as_info_when_no_site_declares_the_backend(
        self, fake_weights, fake_heads, monkeypatch
    ):
        from tools import doctor

        monkeypatch.setattr(doctor, "_local_encoder_extra_importable", lambda: False)
        row = self._row()
        assert row.passed is True
        assert row.category == "LLM routing"
        assert "extra=missing" in row.message
        assert "LOCAL_ENCODER sites: none" in row.message
        assert row.fix is None

    def test_local_encoder_passes_with_extra_weights_and_a_head_per_site(
        self, fake_weights, fake_heads, monkeypatch
    ):
        from tools import doctor

        monkeypatch.setattr(doctor, "_local_encoder_extra_importable", lambda: True)
        self._declare(monkeypatch, "fake.a", "fake.b")
        self._write_head(fake_heads, "fake.a")
        self._write_head(fake_heads, "fake.b")
        row = self._row()
        assert row.passed is True, row.message
        assert "extra=installed" in row.message
        assert "weights=ok (2 files verified" in row.message
        assert "heads=ok (2 verified)" in row.message
        assert "LOCAL_ENCODER sites: fake.a, fake.b" in row.message

    def test_local_encoder_missing_extra_fails_naming_uv_sync(
        self, fake_weights, fake_heads, monkeypatch
    ):
        from tools import doctor

        monkeypatch.setattr(doctor, "_local_encoder_extra_importable", lambda: False)
        self._declare(monkeypatch, "fake.a")
        self._write_head(fake_heads, "fake.a")
        row = self._row()
        assert row.passed is False
        assert "fall back to Anthropic on every call" in row.message
        assert "uv sync --all-extras" in row.fix

    def test_local_encoder_missing_weights_file_fails_naming_the_script(
        self, fake_weights, fake_heads, monkeypatch
    ):
        from tools import doctor

        root, _ = fake_weights
        (root / "tokenizer.json").unlink()
        monkeypatch.setattr(doctor, "_local_encoder_extra_importable", lambda: True)
        self._declare(monkeypatch, "fake.a")
        self._write_head(fake_heads, "fake.a")
        row = self._row()
        assert row.passed is False
        assert "weights=missing: tokenizer.json" in row.message
        assert "scripts/download_local_encoder_models.py" in row.fix

    def test_local_encoder_mismatched_weights_file_fails_naming_the_script(
        self, fake_weights, fake_heads, monkeypatch
    ):
        from tools import doctor

        root, contents = fake_weights
        flipped = bytearray(contents["onnx/model_int8.onnx"])
        flipped[0] ^= 0x01
        (root / "onnx/model_int8.onnx").write_bytes(bytes(flipped))
        monkeypatch.setattr(doctor, "_local_encoder_extra_importable", lambda: True)
        self._declare(monkeypatch, "fake.a")
        self._write_head(fake_heads, "fake.a")
        row = self._row()
        assert row.passed is False
        assert "weights=mismatch: onnx/model_int8.onnx" in row.message
        assert "scripts/download_local_encoder_models.py" in row.fix

    def test_local_encoder_missing_head_fails_naming_the_head(
        self, fake_weights, fake_heads, monkeypatch
    ):
        from tools import doctor

        monkeypatch.setattr(doctor, "_local_encoder_extra_importable", lambda: True)
        self._declare(monkeypatch, "fake.a", "fake.b")
        self._write_head(fake_heads, "fake.a")
        row = self._row()
        assert row.passed is False
        assert "heads=missing: fake.b" in row.message
        assert "agent/llm/backends/heads/fake.b.json" in row.fix

    def test_local_encoder_unreadable_head_fails_naming_the_head_and_the_error(
        self, fake_weights, fake_heads, monkeypatch
    ):
        """A head that exists but fails the leg's loader (a stale embedding
        digest here) reads as broken, not ``heads=ok``: the leg falls back to
        Anthropic on every call exactly as it would with no head."""
        import json

        from tools import doctor

        monkeypatch.setattr(doctor, "_local_encoder_extra_importable", lambda: True)
        self._declare(monkeypatch, "fake.a", "fake.b")
        self._write_head(fake_heads, "fake.a")
        self._write_head(fake_heads, "fake.b")
        stale = json.loads((fake_heads / "fake.b.json").read_text())
        stale["embedding_sha256"] = "0" * 64
        (fake_heads / "fake.b.json").write_text(json.dumps(stale))
        row = self._row()
        assert row.passed is False
        assert "heads=unreadable (" in row.message and ": fake.b" in row.message
        heads_part = row.message.split("heads=")[1].split("LOCAL_ENCODER")[0]
        assert "refit the head" in heads_part and "fake.a" not in heads_part
        assert "agent/llm/backends/heads/fake.b.json" in row.fix

    def test_local_encoder_ollama_daemon_row_is_untouched_by_the_encoder_state(
        self, fake_weights, fake_heads, monkeypatch
    ):
        from agent.llm.tasks import Backend, declared_sites
        from tools import doctor

        monkeypatch.setattr(doctor, "_local_encoder_extra_importable", lambda: False)
        self._declare(monkeypatch, "fake.a")
        results = doctor._check_llm_routing(client_key="client-thing")
        daemon = {r.name: r for r in results}["ollama_daemon"]
        ollama_sites = [d.task.site for d in declared_sites() if d.task.backend is Backend.OLLAMA]
        assert daemon.passed is (not ollama_sites)
        assert "fake.a" not in daemon.message
