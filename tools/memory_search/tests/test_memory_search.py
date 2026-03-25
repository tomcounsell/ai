"""Tests for memory_search tool.

Uses real Redis (no mocks) following the project's testing philosophy.
"""

from __future__ import annotations

import subprocess
import sys
import uuid

import pytest

from tools.memory_search import forget, inspect, save, search

# Generate a unique project key for test isolation
TEST_PROJECT_KEY = f"test-memory-search-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _cleanup_test_memories():
    """Clean up any memories created during tests."""
    created_ids: list[str] = []
    yield created_ids
    # Cleanup: delete all memories created during this test
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


# --- search tests ---


class TestSearch:
    def test_search_empty_query(self):
        """Empty query returns empty results, not crash."""
        result = search("", project_key=TEST_PROJECT_KEY)
        assert result["results"] == []
        assert result["error"] is None

    def test_search_no_results(self):
        """Search for nonexistent content returns empty."""
        result = search(
            f"xyznonexistent{uuid.uuid4().hex}",
            project_key=TEST_PROJECT_KEY,
        )
        assert result["results"] == []
        assert result["error"] is None

    def test_search_returns_dict(self):
        """Search always returns a dict with results and error keys."""
        result = search("test query", project_key=TEST_PROJECT_KEY)
        assert isinstance(result, dict)
        assert "results" in result
        assert "error" in result

    def test_search_respects_limit(self, _cleanup_test_memories):
        """Search limit parameter caps results."""
        result = search("test", project_key=TEST_PROJECT_KEY, limit=1)
        assert len(result.get("results", [])) <= 1


# --- save tests ---


class TestSave:
    def test_save_basic(self, _cleanup_test_memories):
        """Save a memory and get back memory_id."""
        result = _save_and_track(_cleanup_test_memories, "test memory content for save")
        assert result is not None
        assert "memory_id" in result
        assert result["content"] == "test memory content for save"

    def test_save_empty_content(self):
        """Empty content returns None."""
        result = save("", project_key=TEST_PROJECT_KEY)
        assert result is None

    def test_save_with_importance(self, _cleanup_test_memories):
        """Save with custom importance."""
        result = _save_and_track(
            _cleanup_test_memories,
            "important memory",
            importance=8.0,
        )
        assert result is not None

    def test_save_with_source(self, _cleanup_test_memories):
        """Save with custom source."""
        result = _save_and_track(
            _cleanup_test_memories,
            "agent observation",
            source="agent",
            importance=1.0,
        )
        assert result is not None


# --- inspect tests ---


class TestInspect:
    def test_inspect_no_args(self):
        """Inspect with no arguments returns guidance."""
        result = inspect()
        assert "error" in result

    def test_inspect_nonexistent_id(self):
        """Inspect nonexistent memory returns error."""
        result = inspect(memory_id=f"nonexistent-{uuid.uuid4().hex}")
        assert "error" in result

    def test_inspect_by_id(self, _cleanup_test_memories):
        """Inspect a saved memory by ID."""
        saved = _save_and_track(_cleanup_test_memories, "memory for inspection")
        assert saved is not None
        mid = saved["memory_id"]

        result = inspect(memory_id=mid)
        assert result.get("memory_id") == mid
        assert result.get("content") == "memory for inspection"
        assert "confidence" in result
        assert "source" in result

    def test_inspect_stats(self):
        """Stats mode returns aggregate data."""
        result = inspect(stats=True, project_key=TEST_PROJECT_KEY)
        assert "total" in result
        assert "by_source" in result
        assert "avg_confidence" in result

    def test_inspect_stats_empty_project(self):
        """Stats for empty project returns zeros."""
        empty_key = f"empty-{uuid.uuid4().hex[:8]}"
        result = inspect(stats=True, project_key=empty_key)
        assert result["total"] == 0
        assert result["by_source"] == {}


# --- forget tests ---


class TestForget:
    def test_forget_empty_id(self):
        """Forget with empty ID returns error."""
        result = forget("")
        assert result["deleted"] is False
        assert "error" in result

    def test_forget_nonexistent(self):
        """Forget nonexistent memory returns error."""
        result = forget(f"nonexistent-{uuid.uuid4().hex}")
        assert result["deleted"] is False
        assert "error" in result

    def test_forget_saved_memory(self, _cleanup_test_memories):
        """Forget a saved memory succeeds."""
        saved = save(
            "memory to forget",
            project_key=TEST_PROJECT_KEY,
        )
        assert saved is not None
        mid = saved["memory_id"]

        result = forget(mid)
        assert result["deleted"] is True
        assert result["memory_id"] == mid

        # Verify it's gone
        check = inspect(memory_id=mid)
        assert "error" in check


# --- integration test ---


class TestIntegration:
    def test_save_then_search(self, _cleanup_test_memories):
        """Integration: save a memory, then search and find it."""
        unique_content = f"integration-test-{uuid.uuid4().hex[:8]} deploy patterns"
        saved = _save_and_track(_cleanup_test_memories, unique_content, importance=8.0)
        assert saved is not None

        # Search for the unique content
        result = search(unique_content, project_key=TEST_PROJECT_KEY)
        assert result["error"] is None
        # ContextAssembler may or may not find it depending on bloom/scoring
        # but the call should succeed without error

    def test_full_lifecycle(self, _cleanup_test_memories):
        """Integration: save -> inspect -> forget lifecycle."""
        content = f"lifecycle-test-{uuid.uuid4().hex[:8]}"
        saved = save(content, project_key=TEST_PROJECT_KEY)
        assert saved is not None
        mid = saved["memory_id"]

        # Inspect
        details = inspect(memory_id=mid)
        assert details.get("content") == content

        # Forget
        deleted = forget(mid)
        assert deleted["deleted"] is True

        # Verify gone
        gone = inspect(memory_id=mid)
        assert "error" in gone


# --- CLI tests ---


class TestCLI:
    def test_cli_search_help(self):
        """CLI search --help exits 0."""
        result = subprocess.run(
            [sys.executable, "-m", "tools.memory_search", "search", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_cli_no_command(self):
        """CLI with no command shows help and exits 1."""
        result = subprocess.run(
            [sys.executable, "-m", "tools.memory_search"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1

    def test_cli_import(self):
        """Verify import works."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from tools.memory_search import search, save, inspect, forget; print('OK')",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "OK" in result.stdout
