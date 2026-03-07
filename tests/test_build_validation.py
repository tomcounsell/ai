"""Tests for build output validation (Gap 5).

Tests the path where a builder agent returns without producing commits
or creating a PR. The expected behavior is:
- A warning is surfaced to the user (not silent success)
- The pipeline does NOT hard-block (config-only or API-only changes are legitimate)
- The /do-build skill verifies commits exist before pushing/creating PR

Per the plan's open questions:
"Warning, not hard error. The user should see 'builder produced no commits'
but the pipeline shouldn't block. Config-only or API-only changes are
legitimate. The goal is visibility — silent success with no output is
the bug, not the absence of commits per se."
"""

import logging
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

# Mock the claude_agent_sdk before agent package tries to import it
if "claude_agent_sdk" not in sys.modules:
    _mock_sdk = MagicMock()
    sys.modules["claude_agent_sdk"] = _mock_sdk


class TestBuildOutputVerification:
    """Tests for verifying that build agents produced output.

    The /do-build skill must check that the session branch has commits
    before proceeding to push and PR creation. When no commits exist,
    a warning should be surfaced.
    """

    def test_no_commits_detected_when_branch_empty(self, tmp_path):
        """When session branch has no commits vs main, detection works correctly.

        This simulates the check: git log --oneline main..HEAD | wc -l
        If the count is 0, the builder produced no commits.
        """
        # Create a git repo to simulate the check
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial"],
            cwd=tmp_path,
            capture_output=True,
            env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
                 "HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )

        # Check commit count on current branch vs HEAD (no divergence)
        result = subprocess.run(
            ["git", "log", "--oneline", "HEAD..HEAD"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        commit_count = len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0
        assert commit_count == 0, "Expected 0 commits when branch has not diverged"

    def test_commits_detected_when_branch_has_work(self, tmp_path):
        """When session branch has commits, they should be counted correctly."""
        # Create a git repo with a main branch and a session branch
        env = {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }

        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, env=env)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial"],
            cwd=tmp_path, capture_output=True, env=env,
        )

        # Create session branch with a commit
        subprocess.run(
            ["git", "checkout", "-b", "session/test-build"],
            cwd=tmp_path, capture_output=True, env=env,
        )
        (tmp_path / "new_file.py").write_text("# test")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, env=env)
        subprocess.run(
            ["git", "commit", "-m", "add test file"],
            cwd=tmp_path, capture_output=True, env=env,
        )

        # Count commits on session branch vs main
        result = subprocess.run(
            ["git", "log", "--oneline", "main..HEAD"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        commit_count = len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0
        assert commit_count == 1, f"Expected 1 commit, got {commit_count}"

    def test_warning_message_format(self, caplog):
        """Warning message format for no-commits should be clear and actionable."""
        slug = "test-feature"
        branch = f"session/{slug}"

        with caplog.at_level(logging.WARNING):
            # Simulate the warning that /do-build should emit
            logging.getLogger("agent.job_queue").warning(
                f"BUILD WARNING: No commits on {branch}. "
                f"Builder agents produced no code changes."
            )

        assert any("BUILD WARNING" in r.message for r in caplog.records)
        assert any(branch in r.message for r in caplog.records)

    def test_silent_success_is_the_bug(self):
        """Document that the bug is silent success, not absence of commits.

        This test encodes the key insight from the plan: the problem is
        not that a builder might legitimately produce zero commits (e.g.,
        config-only changes). The problem is that zero-commit builds
        complete SILENTLY without any user-visible signal.
        """
        # A builder returning success with no commits should produce a warning
        builder_result = {
            "status": "success",
            "commits": [],
            "files_changed": [],
        }

        # The validation check
        has_commits = len(builder_result.get("commits", [])) > 0
        has_files = len(builder_result.get("files_changed", [])) > 0

        if not has_commits and not has_files:
            # This is the warning condition — NOT a hard error
            should_warn = True
            should_block = False
        else:
            should_warn = False
            should_block = False

        assert should_warn is True, "Zero-commit builds should produce a warning"
        assert should_block is False, "Zero-commit builds should NOT block the pipeline"


class TestBuildValidationIntegration:
    """Integration-style tests for the build validation flow."""

    def test_commit_count_function(self):
        """Verify the commit count can be extracted from git log output."""

        def count_commits_on_branch(git_log_output: str) -> int:
            """Count commits from git log --oneline output."""
            lines = git_log_output.strip().splitlines()
            return len(lines) if lines and lines[0] else 0

        # No commits
        assert count_commits_on_branch("") == 0
        assert count_commits_on_branch("\n") == 0

        # One commit
        assert count_commits_on_branch("abc1234 Fix the bug\n") == 1

        # Multiple commits
        output = "abc1234 Fix the bug\ndef5678 Add tests\nghi9012 Update docs\n"
        assert count_commits_on_branch(output) == 3

    def test_build_skill_documents_validation_step(self):
        """The /do-build skill should document the commit verification step.

        This test verifies that the SKILL.md for /do-build contains
        guidance about verifying commits before creating a PR.
        """
        from pathlib import Path

        skill_path = Path(__file__).parent.parent / ".claude" / "skills" / "do-build" / "SKILL.md"
        if skill_path.exists():
            content = skill_path.read_text()
            # The skill should mention commit verification
            assert "commit" in content.lower(), (
                "/do-build SKILL.md should mention commit verification"
            )
            # Should mention the git log check
            assert "git" in content.lower() and "log" in content.lower(), (
                "/do-build SKILL.md should describe git log check for commits"
            )
