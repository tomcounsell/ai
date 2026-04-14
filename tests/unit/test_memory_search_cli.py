"""Unit tests for tools.memory_search status subcommand.

Covers: happy path, Redis-down, empty project, --json, --deep, --project scoping.
"""

from __future__ import annotations

import argparse
import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch


def _make_memory_record(
    category: str | None = None,
    superseded_by: str = "",
    confidence: float = 0.5,
    relevance: float = 1_000_000.0,
    source: str = "human",
) -> MagicMock:
    """Build a mock Memory record with the given attributes."""
    record = MagicMock()
    record.confidence = confidence
    record.relevance = relevance
    record.superseded_by = superseded_by
    record.source = source
    record.metadata = {"category": category} if category else {}
    return record


class TestStatusFunction:
    """Tests for tools.memory_search.status() directly."""

    def test_happy_path_returns_healthy(self):
        """status() returns healthy=True with correct aggregate fields."""
        from tools.memory_search import status

        records = [
            _make_memory_record(category="correction", relevance=2_000_000.0),
            _make_memory_record(category="pattern", relevance=1_500_000.0),
            _make_memory_record(),  # uncategorized → other
        ]

        with (
            patch("tools.memory_search._fetch_all_records", return_value=records),
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
        ):
            mock_redis.ping.return_value = True
            result = status(project_key="test-project")

        assert result["healthy"] is True
        assert result["total"] == 3
        assert result["superseded"] == 0
        assert "correction" in result["by_category"]
        assert "pattern" in result["by_category"]
        assert "other" in result["by_category"]
        assert result["embedding_field"] in ("configured", "not_configured")
        assert "last_write" in result

    def test_redis_down_returns_unhealthy(self):
        """status() returns healthy=False with error key when Redis is unreachable."""
        from tools.memory_search import status

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.ping.side_effect = ConnectionError("Connection refused")
            result = status(project_key="test-project")

        assert result["healthy"] is False
        assert "error" in result
        assert "Redis unreachable" in result["error"]

    def test_empty_project_returns_zero_counts(self):
        """status() with no memories returns zero counts, not an error."""
        from tools.memory_search import status

        with (
            patch("tools.memory_search._fetch_all_records", return_value=[]),
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
        ):
            mock_redis.ping.return_value = True
            result = status(project_key="empty-project")

        assert result["healthy"] is True
        assert result["total"] == 0
        assert result["superseded"] == 0
        assert result["avg_confidence"] == 0.0
        assert result["last_write"] is None
        assert result["by_category"] == {}

    def test_superseded_count(self):
        """status() correctly counts records where superseded_by != ''."""
        from tools.memory_search import status

        records = [
            _make_memory_record(superseded_by="some-other-id"),
            _make_memory_record(superseded_by=""),
            _make_memory_record(superseded_by="another-id"),
        ]

        with (
            patch("tools.memory_search._fetch_all_records", return_value=records),
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
        ):
            mock_redis.ping.return_value = True
            result = status(project_key="test-project")

        assert result["superseded"] == 2

    def test_deep_adds_orphan_count(self):
        """status(deep=True) includes orphan_index_count in result."""
        import sys
        import types

        from tools.memory_search import status

        records = [_make_memory_record()]

        # Inject a fake popoto_index_cleanup module into sys.modules so the
        # real sys.path.insert + import inside status() finds it.
        fake_cleanup = types.ModuleType("popoto_index_cleanup")
        fake_cleanup._count_orphans = lambda model: 5

        with (
            patch("tools.memory_search._fetch_all_records", return_value=records),
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
            patch.dict(sys.modules, {"popoto_index_cleanup": fake_cleanup}),
        ):
            mock_redis.ping.return_value = True
            result = status(project_key="test-project", deep=True)

        assert "orphan_index_count" in result
        assert "by_category_confidence" in result

    def test_deep_per_category_confidence(self):
        """status(deep=True) includes per-category confidence breakdown."""
        import sys
        import types

        from tools.memory_search import status

        records = [
            _make_memory_record(category="correction", confidence=0.8),
            _make_memory_record(category="correction", confidence=0.6),
            _make_memory_record(category="pattern", confidence=0.4),
        ]

        fake_cleanup = types.ModuleType("popoto_index_cleanup")
        fake_cleanup._count_orphans = lambda model: 0

        with (
            patch("tools.memory_search._fetch_all_records", return_value=records),
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
            patch.dict(sys.modules, {"popoto_index_cleanup": fake_cleanup}),
        ):
            mock_redis.ping.return_value = True
            result = status(project_key="test-project", deep=True)

        cat_conf = result.get("by_category_confidence", {})
        assert "correction" in cat_conf
        assert cat_conf["correction"]["count"] == 2
        assert abs(cat_conf["correction"]["avg_confidence"] - 0.7) < 0.001

    def test_project_key_scoping(self):
        """status() calls _fetch_all_records with the resolved project key."""
        from tools.memory_search import status

        with (
            patch("tools.memory_search._fetch_all_records", return_value=[]) as mock_fetch,
            patch("tools.memory_search._resolve_project_key", return_value="my-project"),
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
        ):
            mock_redis.ping.return_value = True
            result = status(project_key="my-project")

        mock_fetch.assert_called_once_with("my-project")
        assert result["project_key"] == "my-project"


class TestCmdStatus:
    """Tests for the cmd_status() CLI handler."""

    def _make_args(
        self,
        project: str | None = None,
        json_output: bool = False,
        deep: bool = False,
    ) -> argparse.Namespace:
        return argparse.Namespace(project=project, json=json_output, deep=deep)

    def test_exit_code_0_on_success(self):
        """cmd_status() exits 0 when status() returns healthy=True."""
        from tools.memory_search.cli import cmd_status

        healthy_result = {
            "healthy": True,
            "redis": {"ok": True},
            "project_key": "test",
            "total": 5,
            "by_category": {"other": 5},
            "superseded": 0,
            "avg_confidence": 0.5,
            "last_write": "2026-01-01T00:00:00",
            "embedding_field": "not_configured",
        }

        with patch("tools.memory_search.cli.status", return_value=healthy_result):
            code = cmd_status(self._make_args())

        assert code == 0

    def test_exit_code_1_on_redis_down(self):
        """cmd_status() exits 1 when Redis is unreachable."""
        from tools.memory_search.cli import cmd_status

        redis_down_result = {
            "healthy": False,
            "error": "Redis unreachable: Connection refused",
        }

        with (
            patch("tools.memory_search.cli.status", return_value=redis_down_result),
            patch("sys.stderr", new_callable=StringIO),
        ):
            code = cmd_status(self._make_args())

        assert code == 1

    def test_redis_down_error_on_stderr(self):
        """cmd_status() prints error text to stderr on Redis failure."""
        from tools.memory_search.cli import cmd_status

        redis_down_result = {
            "healthy": False,
            "error": "Redis unreachable: Connection refused",
        }

        stderr_output = StringIO()
        with (
            patch("tools.memory_search.cli.status", return_value=redis_down_result),
            patch("sys.stderr", stderr_output),
        ):
            cmd_status(self._make_args())

        assert "Redis unreachable" in stderr_output.getvalue()

    def test_json_flag_emits_valid_json(self):
        """cmd_status --json emits parseable JSON containing 'healthy'."""
        from tools.memory_search.cli import cmd_status

        healthy_result = {
            "healthy": True,
            "redis": {"ok": True},
            "project_key": "test",
            "total": 3,
            "by_category": {"other": 3},
            "superseded": 0,
            "avg_confidence": 0.5,
            "last_write": None,
            "embedding_field": "not_configured",
        }

        stdout_output = StringIO()
        with (
            patch("tools.memory_search.cli.status", return_value=healthy_result),
            patch("sys.stdout", stdout_output),
        ):
            code = cmd_status(self._make_args(json_output=True))

        assert code == 0
        parsed = json.loads(stdout_output.getvalue())
        assert "healthy" in parsed
        assert parsed["healthy"] is True

    def test_json_flag_on_redis_down_emits_json_not_traceback(self):
        """cmd_status --json with Redis down emits JSON with error field, not a traceback."""
        from tools.memory_search.cli import cmd_status

        redis_down_result = {
            "healthy": False,
            "error": "Redis unreachable: Connection refused",
        }

        stdout_output = StringIO()
        with (
            patch("tools.memory_search.cli.status", return_value=redis_down_result),
            patch("sys.stdout", stdout_output),
        ):
            code = cmd_status(self._make_args(json_output=True))

        assert code == 1
        parsed = json.loads(stdout_output.getvalue())
        assert parsed["healthy"] is False
        assert "error" in parsed

    def test_deep_flag_passed_to_status(self):
        """cmd_status --deep passes deep=True to status()."""
        from tools.memory_search.cli import cmd_status

        healthy_result = {
            "healthy": True,
            "redis": {"ok": True},
            "project_key": "test",
            "total": 0,
            "by_category": {},
            "superseded": 0,
            "avg_confidence": 0.0,
            "last_write": None,
            "embedding_field": "not_configured",
            "orphan_index_count": 0,
            "by_category_confidence": {},
        }

        with patch("tools.memory_search.cli.status", return_value=healthy_result) as mock_status:
            cmd_status(self._make_args(deep=True))

        mock_status.assert_called_once_with(project_key=None, deep=True)

    def test_project_flag_passed_to_status(self):
        """cmd_status --project <name> passes project_key to status()."""
        from tools.memory_search.cli import cmd_status

        healthy_result = {
            "healthy": True,
            "redis": {"ok": True},
            "project_key": "myproj",
            "total": 0,
            "by_category": {},
            "superseded": 0,
            "avg_confidence": 0.0,
            "last_write": None,
            "embedding_field": "not_configured",
        }

        with patch("tools.memory_search.cli.status", return_value=healthy_result) as mock_status:
            cmd_status(self._make_args(project="myproj"))

        mock_status.assert_called_once_with(project_key="myproj", deep=False)


class TestStatusSubcommandE2E:
    """End-to-end tests via argparse (simulates CLI invocation)."""

    def test_status_help_exits_0(self):
        """python -m tools.memory_search status --help exits 0."""
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", "tools.memory_search", "status", "--help"],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai/.worktrees/memory-status-cli",
        )
        assert result.returncode == 0
        assert "status" in result.stdout

    def test_status_json_contains_healthy_key(self):
        """python -m tools.memory_search status --json returns JSON with 'healthy' key."""
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", "tools.memory_search", "status", "--json"],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai/.worktrees/memory-status-cli",
        )
        # Exit 0 when Redis is up; exit 1 when down — both are valid in this test environment
        output = result.stdout.strip()
        if output:
            parsed = json.loads(output)
            assert "healthy" in parsed
