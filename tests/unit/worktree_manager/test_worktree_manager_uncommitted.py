"""Unit tests for agent/worktree_manager.py — preserving uncommitted worktree changes."""

import logging
from pathlib import Path
from unittest.mock import patch


def _init_git_repo(tmp_path: Path, name: str = "repo") -> Path:
    """Create a real git repo with an initial commit on ``main``."""
    import subprocess as _sp

    repo = tmp_path / name
    repo.mkdir()
    _sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "seed.txt").write_text("seed\n")
    _sp.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
    _sp.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    return repo


def _add_linked_worktree(repo: Path, slug: str) -> Path:
    """Add a linked worktree under ``.worktrees/{slug}`` on ``session/{slug}``."""
    import subprocess as _sp

    wt = repo / ".worktrees" / slug
    _sp.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", f"session/{slug}", str(wt)],
        check=True,
    )
    return wt


def _dirty(wt: Path) -> None:
    """Introduce staged + unstaged + untracked changes in a worktree."""
    import subprocess as _sp

    # Modify a tracked file (unstaged), stage a second edit, and add an untracked file.
    (wt / "seed.txt").write_text("seed\nunstaged change\n")
    (wt / "staged.txt").write_text("staged content\n")
    _sp.run(["git", "-C", str(wt), "add", "staged.txt"], check=True)
    (wt / "untracked.txt").write_text("untracked content\n")


def _git(wt: Path, *args: str):
    import subprocess as _sp

    return _sp.run(["git", "-C", str(wt), *args], capture_output=True, text=True)


class TestPreserveUncommittedChanges:
    """TDD (issue #2137): auto-preserve uncommitted worktree work before teardown."""

    def test_dirty_tree_preserved_in_named_ref_and_wip_commit(self, tmp_path, caplog):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-2137t"
        wt = _add_linked_worktree(repo, slug)
        head_before = _git(wt, "rev-parse", "HEAD").stdout.strip()
        _dirty(wt)

        with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
            result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is True
        assert result["was_clean"] is False
        sha = result["sha"]
        assert sha and sha != head_before
        assert result["ref"] == f"refs/session-wip/{slug}"

        # Durable named ref resolves in the common ref store to the WIP commit.
        ref_sha = _git(repo, "rev-parse", f"refs/session-wip/{slug}").stdout.strip()
        assert ref_sha == sha
        # HEAD of the worktree advanced to the WIP commit; tree is now clean.
        assert _git(wt, "rev-parse", "HEAD").stdout.strip() == sha
        assert _git(wt, "status", "--porcelain").stdout.strip() == ""

        # Recovery pointer logged with slug, ref, and sha.
        joined = " ".join(r.message for r in caplog.records)
        assert "worktree-wip-preserved" in joined
        assert slug in joined
        assert f"refs/session-wip/{slug}" in joined
        assert sha[:7] in joined

    def test_clean_tree_is_noop_and_creates_no_ref(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-clean"
        wt = _add_linked_worktree(repo, slug)

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is False
        assert result["was_clean"] is True
        # No ref created.
        assert _git(repo, "rev-parse", "--verify", f"refs/session-wip/{slug}").returncode != 0

    def test_untracked_only_is_preserved(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-untr"
        wt = _add_linked_worktree(repo, slug)
        (wt / "brand-new.txt").write_text("only untracked\n")

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is True
        sha = result["sha"]
        # The untracked file is captured in the WIP commit tree.
        listing = _git(repo, "ls-tree", "-r", "--name-only", sha).stdout
        assert "brand-new.txt" in listing

    def test_git_failure_returns_error_dict_and_never_raises(self, tmp_path, caplog):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-fail"
        wt = _add_linked_worktree(repo, slug)
        _dirty(wt)

        # Simulate a git plumbing failure: every git call raises.
        with patch(
            "agent.worktree_manager.subprocess.run",
            side_effect=OSError("git exploded"),
        ):
            with caplog.at_level(logging.ERROR, logger="agent.worktree_manager"):
                result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is False
        assert result.get("errors")
        joined = " ".join(r.message for r in caplog.records)
        assert "worktree-wip-preserve-failed" in joined

    def test_remove_worktree_preserves_dirty_tree_before_force_remove(self, tmp_path):
        from agent import worktree_manager

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-remove"
        wt = _add_linked_worktree(repo, slug)
        _dirty(wt)

        with patch.object(worktree_manager, "worktree_busy_check", return_value=None):
            ok = worktree_manager.remove_worktree(repo, slug, delete_branch=False)

        assert ok is True
        # Worktree directory is gone (force-removed) ...
        assert not wt.exists()
        # ... but the uncommitted work survives in the durable ref.
        assert _git(repo, "rev-parse", "--verify", f"refs/session-wip/{slug}").returncode == 0
