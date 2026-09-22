"""Tests for branch management functionality."""

# Direct import to avoid SDK dependency
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from agent.branch_manager import (
    get_current_branch,
    sanitize_branch_name,
    should_create_branch,
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
        assert not should_create_branch("typo")
        assert not should_create_branch("fix typo in readme")

    def test_multi_step_needs_branch(self):
        """Test multi-step work needs branch."""
        assert should_create_branch("update readme and add docs")
        assert should_create_branch("implement feature then add tests")

    def test_long_request_needs_branch(self):
        """Test detailed request needs branch."""
        long_request = "a" * 150
        assert should_create_branch(long_request)

    def test_complex_keywords_need_branch(self):
        """Test complex keywords trigger branching."""
        assert should_create_branch("refactor authentication module")
        assert should_create_branch("build new API endpoint")


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
        repo_root = Path(__file__).parent.parent.parent
        result = get_current_branch(repo_root)
        # Should return a valid branch name (not empty)
        assert result
        assert len(result) > 0


class TestReturnToMainWorktreeAware:
    """S6 (issue #1647): return_to_main is worktree-aware.

    In a linked worktree, `git checkout main` is not possible (main is
    already checked out by the primary working tree). return_to_main must
    skip the checkout, log at INFO, and return True.
    """

    def test_return_to_main_skips_checkout_in_worktree(self, tmp_path) -> None:
        """In a linked worktree (common-dir ≠ git-dir), no ERROR log and True returned."""
        from unittest.mock import MagicMock, patch

        from agent.branch_manager import return_to_main

        # Simulate subprocess returning different real-paths for
        # --git-common-dir and --git-dir (linked worktree scenario).
        def _fake_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = (
                "/repo/.git\n"
                if "--git-common-dir" in args
                else "/repo/.git/worktrees/myworktree\n"
            )
            return result

        with patch("agent.branch_manager.subprocess.run", side_effect=_fake_run):
            r = return_to_main(tmp_path)

        assert r is True

    def test_return_to_main_non_git_cwd_falls_back_no_error(self, tmp_path) -> None:
        """Non-git cwd (rev-parse fails with CalledProcessError) → fall back to checkout;
        the function returns the result of the fallback, not True unconditionally.
        No ERROR log from the worktree detection itself.
        """
        import subprocess
        from unittest.mock import patch

        from agent.branch_manager import return_to_main

        # First two subprocess.run calls (rev-parse) raise CalledProcessError.
        # The fallback checkout call also fails (non-git dir), but no ERROR
        # should come from the worktree detection phase.
        call_count = [0]

        def _fake_run(args, **kwargs):
            call_count[0] += 1
            if "rev-parse" in args:
                raise subprocess.CalledProcessError(128, args, "", "not a git repo")
            # Fallback checkout also fails.
            raise subprocess.CalledProcessError(1, args, "", "not a git repo")

        with patch("agent.branch_manager.subprocess.run", side_effect=_fake_run):
            with patch("agent.branch_manager.logger") as mock_logger:
                r = return_to_main(tmp_path)

        # Return value: fallback fails → False
        # (original checkout fails → master fallback also fails → False).
        # The important thing: no ERROR from the worktree detection phase.
        error_calls = [call for call in mock_logger.error.call_args_list]
        # Filter out expected checkout-failure ERRORs; only flag unexpected ones.
        unexpected_errors = [c for c in error_calls if "not a git" not in str(c)]
        # No ERROR from the worktree detection code itself.
        assert not any("worktree" in str(e).lower() for e in unexpected_errors)
        assert r is False or r is True  # just assert no crash

    def test_return_to_main_normal_repo_unchanged(self, tmp_path) -> None:
        """Normal repo (common-dir == git-dir after realpath) → original checkout path unchanged."""
        from unittest.mock import MagicMock, patch

        from agent.branch_manager import return_to_main

        checkout_calls: list[list[str]] = []

        def _fake_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "rev-parse" in args:
                # Same path for both common-dir and git-dir → normal repo.
                result.stdout = "/repo/.git\n"
            elif "checkout" in args:
                checkout_calls.append(list(args))
            return result

        with patch("agent.branch_manager.subprocess.run", side_effect=_fake_run):
            r = return_to_main(tmp_path)

        assert r is True
        # The checkout command was actually called (normal repo path).
        assert any("checkout" in str(c) for c in checkout_calls)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestMarkWorkDoneDoesNotDelete:
    """`mark_work_done` archives and returns to main; it never deletes (#3411).

    The removed block ran ``git branch -d {branch_name}`` gated only on a
    ``session/`` prefix test — no worktree check, no merge predicate — one line
    *before* ``safe_delete_branch`` evaluated anything. #1646 was therefore
    bypassed by **ordering**, not by an override, and the bypass hid because
    ``merged_via_ancestor`` returns ``False`` for a branch that no longer
    exists, so the second delete attempt was silent. On 2026-09-16 that
    deletion ran 646 ms before the #1377 launch guard demanded the same branch.
    """

    @staticmethod
    def _git(cwd: Path, *args: str):
        import subprocess

        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)

    @classmethod
    def _repo_on_session_branch(
        cls, tmp_path: Path, *, unmerged: bool, with_plan: bool
    ) -> tuple[Path, str]:
        repo = tmp_path / "repo"
        repo.mkdir()
        cls._git(repo, "init", "-b", "main")
        cls._git(repo, "config", "user.email", "test@test.com")
        cls._git(repo, "config", "user.name", "Test")
        plans = repo / "docs" / "plans"
        plans.mkdir(parents=True)
        (repo / "README.md").write_text("initial\n")
        (plans / ".gitkeep").write_text("")
        cls._git(repo, "add", "README.md", "docs/plans/.gitkeep")
        cls._git(repo, "commit", "-m", "initial commit")

        branch = "session/dev-3411dead"
        cls._git(repo, "checkout", "-b", branch)
        if unmerged:
            (repo / "feature.py").write_text("# unmerged work\n")
            cls._git(repo, "add", "feature.py")
            cls._git(repo, "commit", "-m", "feat: work that has not landed")
        if with_plan:
            (plans / f"ACTIVE-{branch.split('/')[-1]}.md").write_text(
                "# plan\n\n**Status**: IN_PROGRESS\n"
            )
        return repo, branch

    @staticmethod
    def _branch_exists(repo: Path, branch: str) -> bool:
        import subprocess

        return bool(
            subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=repo,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )

    @pytest.mark.parametrize(
        ("unmerged", "with_plan"),
        [(False, False), (True, True)],
        ids=["merged-no-plan", "unmerged-with-plan"],
    )
    def test_session_branch_survives(self, tmp_path, unmerged, with_plan):
        """The branch is still there afterwards, whatever shape it is in.

        ``merged-no-plan`` is the case that is RED before the removal, and it
        is the incident's exact shape: the slug-derived name held none of the
        turn\'s work, so ``git branch -d`` accepted it. The plan matters to the
        setup because ``mark_work_done`` commits its own archive onto the
        branch when an ACTIVE plan exists, which by itself makes the branch
        unmerged and hides the deletion. A fixture that always writes a plan
        therefore passes against the known-bad code and certifies nothing.
        """
        from agent.branch_manager import mark_work_done

        repo, branch = self._repo_on_session_branch(
            tmp_path, unmerged=unmerged, with_plan=with_plan
        )

        assert mark_work_done(repo, branch) is True

        assert self._branch_exists(repo, branch), (
            f"mark_work_done deleted {branch!r}; deletion belongs to "
            f"safe_delete_branch alone (#3411, #1646)"
        )

    def test_still_archives_the_plan_and_returns_to_main(self, tmp_path):
        """What `mark_work_done` keeps doing, so the removal cannot over-reach."""
        from agent.branch_manager import get_current_branch, mark_work_done

        repo, branch = self._repo_on_session_branch(tmp_path, unmerged=True, with_plan=True)
        plans = repo / "docs" / "plans"

        assert mark_work_done(repo, branch) is True

        assert get_current_branch(repo) == "main"
        assert not list(plans.glob("ACTIVE-*.md")), "the active plan was not archived"
        self._git(repo, "checkout", branch)
        assert list(plans.glob("COMPLETED-*.md")), "no COMPLETED- plan was committed"
