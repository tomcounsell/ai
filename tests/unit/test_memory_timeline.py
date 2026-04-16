"""Tests for memory timeline feature.

Tests the timeline() function and get_memories_in_time_range() retrieval helper.
Uses real Redis following the project's testing philosophy.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tools.memory_search import forget, save, timeline

# Unique project key for test isolation
TEST_PROJECT_KEY = f"test-timeline-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _cleanup_test_memories():
    """Clean up any memories created during tests."""
    created_ids: list[str] = []
    yield created_ids
    for mid in created_ids:
        try:
            forget(mid)
        except Exception:
            pass


def _save_and_track(cleanup_list, content, **kwargs):
    """Save a memory and track its ID for cleanup."""
    result = save(content, project_key=TEST_PROJECT_KEY, **kwargs)
    if result and result.get("memory_id"):
        cleanup_list.append(result["memory_id"])
    return result


class TestTimeline:
    """Tests for the timeline() function."""

    def test_timeline_empty_project(self):
        """Timeline for empty project returns empty results."""
        empty_key = f"empty-{uuid.uuid4().hex[:8]}"
        result = timeline(project_key=empty_key)
        assert isinstance(result, dict)
        assert result["results"] == []
        assert result["error"] is None

    def test_timeline_returns_dict_structure(self, _cleanup_test_memories):
        """Timeline always returns dict with results, error, and summary keys."""
        result = timeline(project_key=TEST_PROJECT_KEY)
        assert isinstance(result, dict)
        assert "results" in result
        assert "error" in result
        assert "summary" in result

    def test_timeline_with_records(self, _cleanup_test_memories):
        """Timeline returns saved memories."""
        _save_and_track(_cleanup_test_memories, "timeline test memory alpha", importance=6.0)
        _save_and_track(_cleanup_test_memories, "timeline test memory beta", importance=6.0)

        result = timeline(project_key=TEST_PROJECT_KEY)
        assert result["error"] is None
        assert len(result["results"]) >= 2

    def test_timeline_filters_superseded(self, _cleanup_test_memories):
        """Timeline excludes superseded records."""
        from models.memory import Memory

        saved = _save_and_track(_cleanup_test_memories, "will be superseded", importance=6.0)
        assert saved is not None
        mid = saved["memory_id"]

        # Mark as superseded
        record = Memory.query.filter(memory_id=mid).first()
        if record:
            record.superseded_by = "some-other-id"
            record.save()

        result = timeline(project_key=TEST_PROJECT_KEY)
        # The superseded record should not appear
        result_ids = [r["memory_id"] for r in result["results"]]
        assert mid not in result_ids

    def test_timeline_since_parameter(self, _cleanup_test_memories):
        """Timeline with since parameter filters by time."""
        _save_and_track(_cleanup_test_memories, "recent timeline memory", importance=6.0)

        # Query with since=1 hour ago should include recent records
        since = datetime.now(UTC) - timedelta(hours=1)
        result = timeline(project_key=TEST_PROJECT_KEY, since=since)
        assert result["error"] is None
        # Should find the recently created record
        assert len(result["results"]) >= 1

    def test_timeline_until_parameter(self, _cleanup_test_memories):
        """Timeline with until parameter filters by time."""
        _save_and_track(_cleanup_test_memories, "timeline until test", importance=6.0)

        # Query with until=now should include the record
        until = datetime.now(UTC)
        result = timeline(project_key=TEST_PROJECT_KEY, until=until)
        assert result["error"] is None

    def test_timeline_category_filter(self, _cleanup_test_memories):
        """Timeline with category filter returns only matching categories."""
        from models.memory import Memory

        saved = Memory.safe_save(
            content="correction memory for timeline",
            importance=6.0,
            source="agent",
            project_key=TEST_PROJECT_KEY,
            agent_id=TEST_PROJECT_KEY,
            metadata={"category": "correction", "tags": ["test"]},
        )
        if saved:
            _cleanup_test_memories.append(saved.memory_id)

        saved2 = Memory.safe_save(
            content="pattern memory for timeline",
            importance=6.0,
            source="agent",
            project_key=TEST_PROJECT_KEY,
            agent_id=TEST_PROJECT_KEY,
            metadata={"category": "pattern", "tags": ["test"]},
        )
        if saved2:
            _cleanup_test_memories.append(saved2.memory_id)

        result = timeline(project_key=TEST_PROJECT_KEY, category="correction")
        assert result["error"] is None
        for r in result["results"]:
            meta = r.get("metadata", {})
            assert meta.get("category") == "correction"

    def test_timeline_group_by_day(self, _cleanup_test_memories):
        """Timeline with group_by='day' returns grouped results."""
        _save_and_track(_cleanup_test_memories, "grouped timeline memory", importance=6.0)

        result = timeline(project_key=TEST_PROJECT_KEY, group_by="day")
        assert result["error"] is None
        assert "groups" in result

    def test_timeline_limit(self, _cleanup_test_memories):
        """Timeline respects limit parameter."""
        for i in range(5):
            _save_and_track(
                _cleanup_test_memories,
                f"limit test memory {i}",
                importance=6.0,
            )

        result = timeline(project_key=TEST_PROJECT_KEY, limit=2)
        assert result["error"] is None
        assert len(result["results"]) <= 2

    def test_timeline_summary(self, _cleanup_test_memories):
        """Timeline includes a summary with counts."""
        _save_and_track(_cleanup_test_memories, "summary test memory", importance=6.0)

        result = timeline(project_key=TEST_PROJECT_KEY)
        assert result["error"] is None
        summary = result["summary"]
        assert "total" in summary
        assert summary["total"] >= 1


class TestGetMemoriesInTimeRange:
    """Tests for the get_memories_in_time_range retrieval helper."""

    def test_returns_list(self, _cleanup_test_memories):
        """get_memories_in_time_range returns a list."""
        from agent.memory_retrieval import get_memories_in_time_range

        result = get_memories_in_time_range(TEST_PROJECT_KEY)
        assert isinstance(result, list)

    def test_empty_project(self):
        """Empty project returns empty list."""
        from agent.memory_retrieval import get_memories_in_time_range

        empty_key = f"empty-{uuid.uuid4().hex[:8]}"
        result = get_memories_in_time_range(empty_key)
        assert result == []

    def test_returns_memory_instances(self, _cleanup_test_memories):
        """Returned items are Memory model instances."""
        from agent.memory_retrieval import get_memories_in_time_range
        from models.memory import Memory

        _save_and_track(_cleanup_test_memories, "time range retrieval test", importance=6.0)

        result = get_memories_in_time_range(TEST_PROJECT_KEY)
        assert len(result) >= 1
        for r in result:
            assert isinstance(r, Memory)

    def test_excludes_superseded(self, _cleanup_test_memories):
        """Superseded records are filtered out."""
        from agent.memory_retrieval import get_memories_in_time_range
        from models.memory import Memory

        saved = _save_and_track(
            _cleanup_test_memories, "superseded time range test", importance=6.0
        )
        assert saved is not None
        mid = saved["memory_id"]

        record = Memory.query.filter(memory_id=mid).first()
        if record:
            record.superseded_by = "some-replacement"
            record.save()

        result = get_memories_in_time_range(TEST_PROJECT_KEY)
        result_ids = [getattr(r, "memory_id", "") for r in result]
        assert mid not in result_ids


class TestTimelineCLI:
    """Tests for the timeline CLI subcommand."""

    def test_cli_timeline_help(self):
        """CLI timeline --help exits 0."""
        result = subprocess.run(
            [sys.executable, "-m", "tools.memory_search", "timeline", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "timeline" in result.stdout.lower()

    def test_cli_timeline_json_output(self):
        """CLI timeline --json outputs valid JSON."""
        import json

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.memory_search",
                "timeline",
                "--json",
                "--project",
                f"empty-{uuid.uuid4().hex[:8]}",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "results" in parsed
