"""Unit tests for tools/doctor.py health check CLI."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

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
