"""Smoke tests for the reflections/ package.

Tests each callable in the reflections/ package by:
- Verifying it imports cleanly
- Calling it with mocked Redis/filesystem dependencies
- Asserting it returns a valid dict with required keys

All Redis and network interactions are mocked. These tests run fast
and do not require Redis, GitHub CLI, or Anthropic API access.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

# --- Shared helpers ---


def run_async(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def assert_valid_result(result: dict, expected_status: str = "ok") -> None:
    """Assert the result dict has required keys and valid values."""
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "status" in result, f"Missing 'status' key in {result}"
    assert "findings" in result, f"Missing 'findings' key in {result}"
    assert "summary" in result, f"Missing 'summary' key in {result}"
    assert result["status"] in ("ok", "error", "skipped"), f"Invalid status: {result['status']}"
    assert isinstance(result["findings"], list), "'findings' must be a list"
    assert isinstance(result["summary"], str), "'summary' must be a str"


# ============================================================
# reflections.utils
# ============================================================


class TestReflectionsUtils:
    """Smoke tests for reflections/utils.py."""

    def test_load_local_projects_returns_list(self, tmp_path):
        """load_local_projects() returns a list (possibly empty)."""
        from reflections.utils import load_local_projects

        config = {"projects": {"test": {"working_directory": str(tmp_path)}}}
        import json

        config_file = tmp_path / "projects.json"
        config_file.write_text(json.dumps(config))

        def _env_get(k, d=None):
            return str(config_file) if k == "PROJECTS_CONFIG_PATH" else d

        with (
            patch("reflections.utils.AI_ROOT", tmp_path),
            patch("os.environ.get", side_effect=_env_get),
        ):
            projects = load_local_projects()
        assert isinstance(projects, list)

    def test_is_ignored_match(self):
        """is_ignored() returns True when pattern matches an ignore entry."""
        from reflections.utils import is_ignored

        entries = [{"pattern": "redis connection", "ignored_until": "", "reason": ""}]
        assert is_ignored("redis connection timeout", entries) is True

    def test_is_ignored_no_match(self):
        """is_ignored() returns False when pattern doesn't match."""
        from reflections.utils import is_ignored

        entries = [{"pattern": "redis connection", "ignored_until": "", "reason": ""}]
        assert is_ignored("unrelated bug pattern", entries) is False

    def test_is_high_confidence_true(self):
        """is_high_confidence() returns True for code_bug with pattern and prevention."""
        from reflections.utils import is_high_confidence

        r = {
            "category": "code_bug",
            "pattern": "this is a long enough pattern",
            "prevention": "fix it",
        }
        assert is_high_confidence(r) is True

    def test_is_high_confidence_false(self):
        """is_high_confidence() returns False when fewer than 2 criteria met."""
        from reflections.utils import is_high_confidence

        r = {"category": "misunderstanding", "pattern": "short", "prevention": ""}
        assert is_high_confidence(r) is False

    def test_load_ignore_entries_empty_redis(self):
        """load_ignore_entries() returns empty list when model unavailable."""
        from reflections.utils import load_ignore_entries

        with patch("reflections.utils.logger"):
            with patch("models.reflections.ReflectionIgnore") as mock_ri:
                mock_ri.get_active.side_effect = Exception("redis down")
                result = load_ignore_entries()
        assert result == []


# ============================================================
# reflections.maintenance
# ============================================================


class TestMaintenanceCallables:
    """Smoke tests for reflections/maintenance.py."""

    def test_run_legacy_code_scan_returns_valid(self):
        """run_legacy_code_scan() returns valid dict."""
        from reflections.maintenance import run_legacy_code_scan

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = run_async(run_legacy_code_scan())
        assert_valid_result(result)

    def test_run_redis_ttl_cleanup_returns_valid(self):
        """run_redis_ttl_cleanup() returns valid dict with mocked models."""
        from reflections.maintenance import run_redis_ttl_cleanup

        with (
            patch("models.telegram.TelegramMessage") as mock_tm,
            patch("models.link.Link") as mock_link,
            patch("models.chat.Chat") as mock_chat,
            patch("models.agent_session.AgentSession") as mock_as,
            patch("models.bridge_event.BridgeEvent") as mock_be,
            patch("models.reflections.ReflectionIgnore") as mock_ri,
        ):
            mock_tm.cleanup_expired.return_value = 0
            mock_link.cleanup_expired.return_value = 0
            mock_chat.cleanup_expired.return_value = 0
            mock_as.cleanup_expired.return_value = 0
            mock_be.cleanup_old.return_value = 0
            mock_ri.cleanup_expired.return_value = 0

            result = run_async(run_redis_ttl_cleanup())
        assert_valid_result(result)

    def test_run_redis_data_quality_empty_data(self):
        """run_redis_data_quality() handles empty querysets without error."""
        from reflections.maintenance import run_redis_data_quality

        with (
            patch("models.link.Link") as mock_link,
            patch("models.chat.Chat") as mock_chat,
            patch("models.agent_session.AgentSession") as mock_as,
            patch("models.telegram.TelegramMessage") as mock_tm,
        ):
            mock_link.query.all.return_value = []
            mock_chat.query.all.return_value = []
            mock_as.query.all.return_value = []
            mock_tm.query.all.return_value = []

            result = run_async(run_redis_data_quality())
        assert_valid_result(result)

    def test_run_disk_space_check_returns_valid(self):
        """run_disk_space_check() returns valid dict."""

        from reflections.maintenance import run_disk_space_check

        mock_usage = MagicMock()
        mock_usage.free = 20 * (1024**3)  # 20 GB free
        mock_usage.total = 100 * (1024**3)  # 100 GB total

        with patch("shutil.disk_usage", return_value=mock_usage):
            result = run_async(run_disk_space_check())
        assert_valid_result(result)
        assert result["status"] == "ok"
        assert result["findings"] == []  # No low-space finding

    def test_run_disk_space_check_low_space(self):
        """run_disk_space_check() adds finding when space is low."""
        from reflections.maintenance import run_disk_space_check

        mock_usage = MagicMock()
        mock_usage.free = 5 * (1024**3)  # 5 GB — below 10 GB threshold
        mock_usage.total = 100 * (1024**3)

        with patch("shutil.disk_usage", return_value=mock_usage):
            result = run_async(run_disk_space_check())
        assert_valid_result(result)
        assert len(result["findings"]) == 1
        assert "Low disk space" in result["findings"][0]

    def test_run_analytics_rollup_returns_valid(self):
        """run_analytics_rollup() returns valid dict."""
        from reflections.maintenance import run_analytics_rollup

        with patch("analytics.rollup.rollup_daily") as mock_rollup:
            mock_rollup.return_value = {"aggregated_days": 1, "purged_rows": 5}
            result = run_async(run_analytics_rollup())
        assert_valid_result(result)

    def test_run_analytics_rollup_error(self):
        """run_analytics_rollup() returns error dict on failure."""
        from reflections.maintenance import run_analytics_rollup

        with patch("analytics.rollup.rollup_daily", side_effect=ImportError("no module")):
            result = run_async(run_analytics_rollup())
        assert result["status"] == "error"


# ============================================================
# reflections.auditing
# ============================================================


class TestAuditingCallables:
    """Smoke tests for reflections/auditing.py."""

    def test_run_log_review_no_projects(self):
        """run_log_review() returns valid dict with no projects."""
        from reflections.auditing import run_log_review

        with (
            patch("reflections.auditing.load_local_projects", return_value=[]),
            patch("models.bridge_event.BridgeEvent") as mock_be,
        ):
            mock_be.query.filter.return_value = []
            result = run_async(run_log_review())
        assert_valid_result(result)

    def test_run_documentation_audit_returns_valid(self):
        """run_documentation_audit() returns valid dict."""
        from reflections.auditing import run_documentation_audit

        mock_summary = MagicMock()
        mock_summary.skipped = False
        mock_summary.skip_reason = ""
        mock_summary.kept = ["doc.md"]
        mock_summary.updated = []
        mock_summary.deleted = []

        with patch("scripts.docs_auditor.DocsAuditor") as mock_da:
            mock_instance = MagicMock()
            mock_instance.run.return_value = mock_summary
            mock_da.return_value = mock_instance
            result = run_async(run_documentation_audit())
        assert_valid_result(result)

    def test_run_skills_audit_no_script(self):
        """run_skills_audit() returns ok when script not found."""
        from reflections.auditing import run_skills_audit

        with patch("reflections.auditing.PROJECT_ROOT", MagicMock()):
            # Patch audit_script.exists() to return False
            with patch("pathlib.Path.exists", return_value=False):
                result = run_async(run_skills_audit())
        assert_valid_result(result)

    def test_run_hooks_audit_no_log(self, tmp_path):
        """run_hooks_audit() returns valid dict when hooks.log doesn't exist."""
        from reflections.auditing import run_hooks_audit

        with patch("reflections.auditing.PROJECT_ROOT", tmp_path):
            result = run_async(run_hooks_audit())
        assert_valid_result(result)

    def test_run_feature_docs_audit_no_dir(self, tmp_path):
        """run_feature_docs_audit() returns valid dict when docs/features doesn't exist."""
        from reflections.auditing import run_feature_docs_audit

        with patch("reflections.auditing.PROJECT_ROOT", tmp_path):
            result = run_async(run_feature_docs_audit())
        assert_valid_result(result)

    def test_run_pr_review_audit_no_projects(self):
        """run_pr_review_audit() returns valid dict with no projects."""
        from reflections.auditing import run_pr_review_audit

        with (
            patch("reflections.auditing.load_local_projects", return_value=[]),
            patch("models.reflections.PRReviewAudit") as mock_pra,
        ):
            mock_pra.last_successful_run.return_value = None
            result = run_async(run_pr_review_audit())
        assert_valid_result(result)


# ============================================================
# reflections.task_management
# ============================================================


class TestTaskManagementCallables:
    """Smoke tests for reflections/task_management.py."""

    def test_run_task_management_no_projects(self):
        """run_task_management() returns valid dict with no projects."""
        from reflections.task_management import run_task_management

        with patch("reflections.task_management.load_local_projects", return_value=[]):
            result = run_async(run_task_management())
        assert_valid_result(result)

    def test_run_principal_staleness_missing_file(self, tmp_path):
        """run_principal_staleness() flags missing PRINCIPAL.md."""
        from reflections.task_management import run_principal_staleness

        with patch("reflections.task_management.PROJECT_ROOT", tmp_path):
            result = run_async(run_principal_staleness())
        assert_valid_result(result)
        assert "does not exist" in result["summary"]

    def test_run_principal_staleness_fresh(self, tmp_path):
        """run_principal_staleness() returns ok for fresh file."""
        from reflections.task_management import run_principal_staleness

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        principal = config_dir / "PRINCIPAL.md"
        principal.write_text("# Principal\n\nStrategic context.")

        with patch("reflections.task_management.PROJECT_ROOT", tmp_path):
            result = run_async(run_principal_staleness())
        assert_valid_result(result)
        assert result["findings"] == []  # Fresh file, no warning


# ============================================================
# reflections.session_intelligence
# ============================================================


class TestSessionIntelligenceCallable:
    """Smoke tests for reflections/session_intelligence.py."""

    def test_run_no_sessions(self):
        """run() returns valid dict when no sessions exist."""
        from reflections.session_intelligence import run

        with (
            patch("models.agent_session.AgentSession") as mock_as,
            patch("models.bridge_event.BridgeEvent") as mock_be,
            patch("reflections.session_intelligence.load_local_projects", return_value=[]),
        ):
            mock_as.query.all.return_value = []
            mock_be.query.filter.return_value = []
            result = run_async(run())
        assert_valid_result(result)

    def test_run_with_mocked_sessions(self):
        """run() handles sessions without crashing."""
        from reflections.session_intelligence import run

        mock_session = MagicMock()
        mock_session.started_at = None  # skip by date filter
        mock_session.session_id = "test123"

        with (
            patch("models.agent_session.AgentSession") as mock_as,
            patch("models.bridge_event.BridgeEvent") as mock_be,
            patch("reflections.session_intelligence.load_local_projects", return_value=[]),
        ):
            mock_as.query.all.return_value = [mock_session]
            mock_be.query.filter.return_value = []
            result = run_async(run())
        assert_valid_result(result)


# ============================================================
# reflections.behavioral_learning
# ============================================================


class TestBehavioralLearningCallable:
    """Smoke tests for reflections/behavioral_learning.py."""

    def test_run_skips_when_cyclic_episode_missing(self):
        """run() returns skipped result when models.cyclic_episode is unavailable."""
        import sys

        from reflections.behavioral_learning import run

        with patch.dict(sys.modules, {"models.cyclic_episode": None}):
            result = run_async(run())
        assert_valid_result(result)
        assert "skipped" in result["summary"]


# ============================================================
# reflections.daily_report
# ============================================================


class TestDailyReportCallable:
    """Smoke tests for reflections/daily_report.py."""

    def test_run_no_reflections(self):
        """run() returns valid dict when no reflections have run today."""
        from reflections.daily_report import run

        with (
            patch("models.reflection.Reflection") as mock_ref,
            patch("reflections.daily_report.load_local_projects", return_value=[]),
        ):
            mock_ref.query.all.return_value = []
            result = run_async(run())
        assert_valid_result(result)
