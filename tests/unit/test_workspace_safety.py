"""Tests for workspace safety invariants (issue #306).

Validates:
1. CWD existence check
2. Path containment within allowed root
3. Slug sanitization for worktree paths
4. Graceful fallback behavior on violations
"""

import logging
from pathlib import Path

import pytest

from agent.worktree_manager import validate_workspace


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace directory structure."""
    workspace = tmp_path / "src" / "project"
    workspace.mkdir(parents=True)
    worktree = tmp_path / "src" / "project" / ".worktrees" / "valid-slug"
    worktree.mkdir(parents=True)
    return tmp_path / "src"


class TestCWDExistence:
    """Invariant 1: CWD must exist and be a directory."""

    def test_valid_directory(self, tmp_workspace):
        project = tmp_workspace / "project"
        result = validate_workspace(project, tmp_workspace)
        assert result == project.resolve()

    def test_nonexistent_path_falls_back(self, tmp_workspace, caplog):
        nonexistent = tmp_workspace / "does-not-exist"
        with caplog.at_level(logging.WARNING):
            result = validate_workspace(nonexistent, tmp_workspace)
        assert result == tmp_workspace
        assert "does not exist" in caplog.text

    def test_file_not_directory_falls_back(self, tmp_workspace, caplog):
        file_path = tmp_workspace / "afile.txt"
        file_path.touch()
        with caplog.at_level(logging.WARNING):
            result = validate_workspace(file_path, tmp_workspace)
        assert result == tmp_workspace
        assert "not a directory" in caplog.text

    def test_none_path_falls_back(self, tmp_workspace, caplog):
        with caplog.at_level(logging.WARNING):
            result = validate_workspace(None, tmp_workspace)
        assert result == tmp_workspace
        assert "empty path" in caplog.text

    def test_empty_string_falls_back(self, tmp_workspace, caplog):
        with caplog.at_level(logging.WARNING):
            result = validate_workspace("", tmp_workspace)
        assert result == tmp_workspace
        assert "empty path" in caplog.text

    def test_whitespace_only_falls_back(self, tmp_workspace, caplog):
        with caplog.at_level(logging.WARNING):
            result = validate_workspace("   ", tmp_workspace)
        assert result == tmp_workspace
        assert "empty path" in caplog.text


class TestPathContainment:
    """Invariant 2: Path must be within allowed root."""

    def test_path_within_root(self, tmp_workspace):
        project = tmp_workspace / "project"
        result = validate_workspace(project, tmp_workspace)
        assert result == project.resolve()

    def test_path_outside_root_falls_back(self, tmp_path, caplog):
        outside = tmp_path / "outside"
        outside.mkdir()
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        with caplog.at_level(logging.WARNING):
            result = validate_workspace(outside, allowed)
        assert result == allowed
        assert "outside allowed root" in caplog.text

    def test_traversal_attack_falls_back(self, tmp_workspace, caplog):
        # Create a path that uses .. to escape
        project = tmp_workspace / "project"
        traversal = project / ".." / ".." / ".."
        with caplog.at_level(logging.WARNING):
            result = validate_workspace(traversal, tmp_workspace)
        # After resolution, the path will be outside allowed root
        assert result == tmp_workspace
        assert "outside allowed root" in caplog.text


class TestSlugSanitization:
    """Invariant 3: Worktree slugs must match VALID_SLUG_RE."""

    def test_valid_worktree_slug(self, tmp_workspace):
        wt = tmp_workspace / "project" / ".worktrees" / "valid-slug"
        result = validate_workspace(wt, tmp_workspace, is_worktree=True)
        assert result == wt.resolve()

    def test_invalid_slug_with_spaces_falls_back(self, tmp_workspace, caplog):
        bad_wt = tmp_workspace / "project" / ".worktrees" / "bad slug"
        bad_wt.mkdir(parents=True)
        with caplog.at_level(logging.WARNING):
            result = validate_workspace(bad_wt, tmp_workspace, is_worktree=True)
        assert result == tmp_workspace
        assert "invalid characters" in caplog.text

    def test_invalid_slug_with_special_chars_falls_back(self, tmp_workspace, caplog):
        bad_wt = tmp_workspace / "project" / ".worktrees" / "slug@bad!"
        bad_wt.mkdir(parents=True)
        with caplog.at_level(logging.WARNING):
            result = validate_workspace(bad_wt, tmp_workspace, is_worktree=True)
        assert result == tmp_workspace
        assert "invalid characters" in caplog.text

    def test_non_worktree_path_skips_slug_check(self, tmp_workspace):
        """Regular project paths should NOT have slug validation applied."""
        project = tmp_workspace / "project"
        # is_worktree=False means no slug check
        result = validate_workspace(project, tmp_workspace, is_worktree=False)
        assert result == project.resolve()

    def test_slug_starting_with_dot_falls_back(self, tmp_workspace, caplog):
        bad_wt = tmp_workspace / "project" / ".worktrees" / ".hidden-slug"
        bad_wt.mkdir(parents=True)
        with caplog.at_level(logging.WARNING):
            result = validate_workspace(bad_wt, tmp_workspace, is_worktree=True)
        assert result == tmp_workspace
        assert "invalid characters" in caplog.text


class TestFallbackBehavior:
    """Verify that all violations fall back gracefully, never crash."""

    def test_returns_path_object(self, tmp_workspace):
        result = validate_workspace(tmp_workspace / "project", tmp_workspace)
        assert isinstance(result, Path)

    def test_fallback_returns_allowed_root(self, tmp_workspace):
        result = validate_workspace(None, tmp_workspace)
        assert result == tmp_workspace

    def test_string_path_accepted(self, tmp_workspace):
        result = validate_workspace(str(tmp_workspace / "project"), tmp_workspace)
        assert result == (tmp_workspace / "project").resolve()

    def test_broken_symlink_falls_back(self, tmp_workspace, caplog):
        broken = tmp_workspace / "broken-link"
        broken.symlink_to(tmp_workspace / "nonexistent-target")
        with caplog.at_level(logging.WARNING):
            result = validate_workspace(broken, tmp_workspace)
        assert result == tmp_workspace
        assert "does not exist" in caplog.text


class TestTildeExpansion:
    """Invariant 4: Paths beginning with ~ must be expanded before resolution.

    Regression guard for the cuttlefish hotfix: projects.json may store
    working_directory as the literal string "~/src/cuttlefish" and pass it
    untouched to the worker. Without expansion, Path.resolve() concatenates
    the tilde under cwd ("/Users/<user>/src/ai/~/src/cuttlefish"), which
    fails containment and falls back to allowed_root with a noisy warning.
    """

    def test_tilde_path_expanded_before_validation(self, tmp_path, monkeypatch):
        """A "~/foo" string must resolve to <home>/foo, not cwd/~/foo."""
        # Pretend HOME is tmp_path so we can construct a real "~/foo" target
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "foo"
        target.mkdir()
        result = validate_workspace("~/foo", tmp_path)
        # Must resolve under the expanded home, not cwd/~/foo
        assert result == target.resolve()
        assert "~" not in str(result)

    def test_tilde_path_passed_as_path_object_expanded(self, tmp_path, monkeypatch):
        """Path("~/foo") must also be expanded; Path() does not auto-expand."""
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "foo"
        target.mkdir()
        result = validate_workspace(Path("~/foo"), tmp_path)
        assert result == target.resolve()
        assert "~" not in str(result)

    def test_tilde_path_does_not_warn_about_missing_directory(self, tmp_path, monkeypatch, caplog):
        """Project dict with working_directory="~/foo" must not produce a
        'does not exist' warning concatenating ~ under cwd. This is the
        regression scenario from the cuttlefish brief."""
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "foo"
        target.mkdir()
        with caplog.at_level(logging.WARNING):
            result = validate_workspace("~/foo", tmp_path)
        assert result == target.resolve()
        # The literal "~" must NOT appear in any warning text — that is the
        # exact symptom from the cuttlefish run.
        for record in caplog.records:
            assert "~" not in record.getMessage(), f"Tilde leaked into log: {record.getMessage()}"
