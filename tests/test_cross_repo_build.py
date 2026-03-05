"""Tests for cross-repo build support.

Tests the resolve_repo_root utility and the pipeline_state target_repo parameter.
"""

import subprocess
from pathlib import Path

import pytest

from agent.pipeline_state import initialize, load
from agent.worktree_manager import resolve_repo_root


class TestResolveRepoRoot:
    """Tests for resolve_repo_root() utility."""

    def test_resolve_current_repo(self):
        """resolve_repo_root returns the correct repo root for a file in the current repo."""
        # Use this test file as the input -- it's in the ai repo
        result = resolve_repo_root(__file__)
        # Should return the ai repo root (parent of tests/)
        assert result.is_absolute()
        assert (result / "agent" / "worktree_manager.py").exists()

    def test_resolve_from_directory(self):
        """resolve_repo_root works when given a directory path."""
        result = resolve_repo_root(Path(__file__).parent)
        assert result.is_absolute()
        assert (result / "agent" / "worktree_manager.py").exists()

    def test_resolve_nonexistent_path_raises(self):
        """resolve_repo_root raises FileNotFoundError for nonexistent paths."""
        with pytest.raises(FileNotFoundError, match="does not exist"):
            resolve_repo_root("/nonexistent/path/to/file.md")

    def test_resolve_outside_git_repo_raises(self, tmp_path):
        """resolve_repo_root raises ValueError for paths outside any git repo."""
        # tmp_path is not inside a git repo
        test_file = tmp_path / "test.md"
        test_file.write_text("test")
        with pytest.raises(ValueError, match="not inside a git repository"):
            resolve_repo_root(test_file)

    def test_resolve_different_repo(self, tmp_path):
        """resolve_repo_root correctly identifies a different git repo."""
        # Create a temporary git repo
        repo_dir = tmp_path / "other_repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )

        # Create a nested file
        plan_dir = repo_dir / "docs" / "plans"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "my_plan.md"
        plan_file.write_text("# Test Plan")

        result = resolve_repo_root(plan_file)
        assert result == repo_dir.resolve()


class TestPipelineStateTargetRepo:
    """Tests for pipeline_state target_repo parameter."""

    def test_initialize_without_target_repo(self, tmp_path, monkeypatch):
        """initialize() works without target_repo (backward compatible)."""
        # Redirect state storage to tmp
        monkeypatch.setattr("agent.pipeline_state._STATE_ROOT", tmp_path / "pipeline")

        state = initialize("test-slug", "session/test-slug", ".worktrees/test-slug")
        assert "target_repo" not in state
        assert state["slug"] == "test-slug"

    def test_initialize_with_target_repo(self, tmp_path, monkeypatch):
        """initialize() stores target_repo when provided."""
        monkeypatch.setattr("agent.pipeline_state._STATE_ROOT", tmp_path / "pipeline")

        state = initialize(
            "test-slug",
            "session/test-slug",
            "/other/repo/.worktrees/test-slug",
            target_repo="/other/repo",
        )
        assert state["target_repo"] == "/other/repo"
        assert state["worktree"] == "/other/repo/.worktrees/test-slug"

    def test_target_repo_persisted_to_disk(self, tmp_path, monkeypatch):
        """target_repo is saved and loadable from disk."""
        monkeypatch.setattr("agent.pipeline_state._STATE_ROOT", tmp_path / "pipeline")

        initialize(
            "persist-test",
            "session/persist-test",
            "/other/.worktrees/persist-test",
            target_repo="/other",
        )

        loaded = load("persist-test")
        assert loaded is not None
        assert loaded["target_repo"] == "/other"
