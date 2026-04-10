"""Unit tests for tools/doctor.py health check CLI."""

from __future__ import annotations

import json
from unittest.mock import patch

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
