"""Tests for branch management functionality."""

import pytest
import subprocess
from pathlib import Path
import tempfile
import shutil

# Direct import to avoid SDK dependency
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from agent.branch_manager import (
    sanitize_branch_name,
    should_create_branch,
    get_current_branch,
)


class TestBranchNaming:
    """Tests for branch name sanitization."""

    def test_sanitize_simple_request(self):
        """Test simple request becomes clean branch name."""
        result = sanitize_branch_name("update readme and add docs")
        assert result == "update-readme-and-add-docs"

    def test_sanitize_with_special_chars(self):
        """Test special characters are removed."""
        result = sanitize_branch_name("Fix bug in auth module!!!")
        assert result == "fix-bug-in-auth-module"

    def test_sanitize_long_request(self):
        """Test long requests are truncated."""
        long_request = "a" * 100
        result = sanitize_branch_name(long_request)
        assert len(result) <= 50

    def test_sanitize_multiple_spaces(self):
        """Test multiple spaces become single hyphen."""
        result = sanitize_branch_name("add    multiple     spaces")
        assert result == "add-multiple-spaces"

    def test_sanitize_mixed_case(self):
        """Test mixed case becomes lowercase."""
        result = sanitize_branch_name("Update README And Fix Tests")
        assert result == "update-readme-and-fix-tests"


class TestBranchDecision:
    """Tests for should_create_branch logic."""

    def test_simple_request_no_branch(self):
        """Test simple one-word request doesn't need branch."""
        assert should_create_branch("typo") == False
        assert should_create_branch("fix typo in readme") == False

    def test_multi_step_needs_branch(self):
        """Test multi-step work needs branch."""
        assert should_create_branch("update readme and add docs") == True
        assert should_create_branch("implement feature then add tests") == True

    def test_long_request_needs_branch(self):
        """Test detailed request needs branch."""
        long_request = "a" * 150
        assert should_create_branch(long_request) == True

    def test_complex_keywords_need_branch(self):
        """Test complex keywords trigger branching."""
        assert should_create_branch("refactor authentication module") == True
        assert should_create_branch("build new API endpoint") == True


class TestGitOperations:
    """Tests for git operations (requires git to be available)."""

    def test_get_current_branch_fallback(self):
        """Test get_current_branch returns fallback on error."""
        # Use a non-existent directory
        fake_dir = Path("/nonexistent/path")
        result = get_current_branch(fake_dir)
        assert result == "main"  # Fallback value

    def test_get_current_branch_real_repo(self):
        """Test get_current_branch on real repo."""
        # Test on this repo
        repo_root = Path(__file__).parent.parent
        result = get_current_branch(repo_root)
        # Should return a valid branch name (not empty)
        assert result
        assert len(result) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
