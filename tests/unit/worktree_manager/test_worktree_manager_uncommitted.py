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


def _init_git_repo_with_dirs(tmp_path: Path, name: str, dirs: list[str]) -> Path:
    """Like ``_init_git_repo`` but with tracked top-level directories (each
    holding one tracked file, ``f1.py``) — the fixture shape needed by the
    wipe-detection tests (#3167).
    """
    import subprocess as _sp

    repo = tmp_path / name
    repo.mkdir()
    _sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    _sp.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "seed.txt").write_text("seed\n")
    for d in dirs:
        (repo / d).mkdir()
        (repo / d / "f1.py").write_text(f"# {d}/f1\n")
    _sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    _sp.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    return repo


def _gut(wt: Path, *dirs: str) -> None:
    """Delete named tracked top-level directories from disk (simulates a wipe)."""
    import shutil as _shutil

    for d in dirs:
        _shutil.rmtree(wt / d, ignore_errors=True)


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
        assert "refused" not in result
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


class TestPreserveWipeDetection:
    """Regression tests for #3167: auto-preserve must never commit a wipe."""

    def test_pure_wipe_writes_no_commit_and_no_ref(self, tmp_path, caplog):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo_with_dirs(tmp_path, "repo", ["docs", "tests"])
        slug = "sdlc-wipe1"
        wt = _add_linked_worktree(repo, slug)
        head_before = _git(wt, "rev-parse", "HEAD").stdout.strip()
        _gut(wt, "docs", "tests")

        with caplog.at_level(logging.ERROR, logger="agent.worktree_manager"):
            result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is False
        assert result["refused"] == "missing-tracked-dirs"
        assert result["errors"] == []
        assert _git(wt, "rev-parse", "HEAD").stdout.strip() == head_before
        assert _git(repo, "rev-parse", "--verify", f"refs/session-wip/{slug}").returncode != 0

        joined = " ".join(r.message for r in caplog.records)
        assert "worktree-wip-refused-wipe" in joined
        assert "docs" in joined and "tests" in joined

    def test_mixed_wipe_preserves_additions_and_drops_deletions(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo_with_dirs(tmp_path, "repo", ["docs", "tests"])
        slug = "sdlc-wipe2"
        wt = _add_linked_worktree(repo, slug)
        parent_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()

        _gut(wt, "docs")
        (wt / "tests" / "f1.py").write_text("# edited\n")
        (wt / "tests" / "f4.py").write_text("# new\n")

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is True
        assert result["refused"] == "missing-tracked-dirs"
        sha = result["sha"]
        assert sha and sha != parent_sha

        diff_stat = _git(wt, "diff", "--stat", parent_sha, sha).stdout
        assert "f1.py" in diff_stat
        assert "f4.py" in diff_stat
        # Zero deletions in the commit's diff against its parent.
        assert (
            _git(wt, "diff", "--diff-filter=D", "--name-only", parent_sha, sha).stdout.strip() == ""
        )
        # The removed directory is still tracked at the new HEAD.
        tracked_dirs = _git(wt, "ls-tree", "--name-only", "-d", "HEAD").stdout
        assert "docs" in tracked_dirs

        # Commit trailer records the refusal; subject line is unchanged.
        body = _git(wt, "log", "-1", "--format=%B", sha).stdout
        assert body.startswith("WIP: auto-preserved before teardown")
        assert "Auto-preserve declined deletions (#3167)" in body
        assert "Missing-tracked-dirs:" in body
        assert "Declined-deletions:" in body

    def test_refusal_and_git_failure_are_distinguishable_on_errors(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo_with_dirs(tmp_path, "repo", ["docs"])
        slug = "sdlc-wipe3"
        wt = _add_linked_worktree(repo, slug)
        _gut(wt, "docs")

        refusal = preserve_uncommitted_worktree_changes(repo, slug, wt)
        assert refusal["errors"] == []
        assert refusal["refused"] == "missing-tracked-dirs"

        repo2 = _init_git_repo(tmp_path, "repo2")
        slug2 = "sdlc-wipe3b"
        wt2 = _add_linked_worktree(repo2, slug2)
        _dirty(wt2)
        with patch("agent.worktree_manager.subprocess.run", side_effect=OSError("git exploded")):
            failure = preserve_uncommitted_worktree_changes(repo2, slug2, wt2)
        assert failure["errors"]
        assert "refused" not in failure

    def test_wipe_with_untracked_artifacts_still_refuses_deletions(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo_with_dirs(tmp_path, "repo", ["docs", "tests"])
        slug = "sdlc-wipe4"
        wt = _add_linked_worktree(repo, slug)
        parent_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()

        _gut(wt, "docs", "tests")
        # Untracked build-artifact-shaped junk, so `git add -A` would report
        # real insertions -- reproduces the incident's shape (spike-3).
        artifact_dir = wt / ".venv" / "lib"
        artifact_dir.mkdir(parents=True)
        for i in range(3):
            (artifact_dir / f"pkg{i}.pyc").write_text("junk\n")

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["refused"] == "missing-tracked-dirs"
        new_head = _git(wt, "rev-parse", "HEAD").stdout.strip()
        # Whatever the outcome, the wipe's own deletions never land in a commit.
        if result["preserved"]:
            assert (
                _git(
                    wt, "diff", "--diff-filter=D", "--name-only", parent_sha, new_head
                ).stdout.strip()
                == ""
            )
        else:
            assert new_head == parent_sha
        tracked_dirs = _git(wt, "ls-tree", "--name-only", "-d", "HEAD").stdout
        assert "docs" in tracked_dirs and "tests" in tracked_dirs

    def test_partially_staged_wipe_still_detected(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo_with_dirs(tmp_path, "repo", ["docs"])
        slug = "sdlc-wipe5"
        wt = _add_linked_worktree(repo, slug)
        parent_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()

        _gut(wt, "docs")
        _git(wt, "add", "-A")  # a previous pass already staged the wipe

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["refused"] == "missing-tracked-dirs"
        assert _git(wt, "rev-parse", "HEAD").stdout.strip() == parent_sha

    def test_deletions_inside_a_surviving_directory_are_preserved_normally(self, tmp_path):
        import subprocess as _sp

        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo_with_dirs(tmp_path, "repo", ["tests"])
        # A second tracked file inside tests/ so the directory survives on
        # disk after one of its files is deleted.
        (repo / "tests" / "f2.py").write_text("# f2\n")
        _sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
        _sp.run(["git", "-C", str(repo), "commit", "-q", "-m", "add f2"], check=True)

        slug = "sdlc-wipe6"
        wt = _add_linked_worktree(repo, slug)
        parent_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()

        (wt / "tests" / "f2.py").unlink()  # deletion inside a surviving directory
        (wt / "tests" / "f1.py").write_text("# edited\n")  # a genuine edit

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is True
        assert "refused" not in result
        sha = result["sha"]
        deleted = _git(wt, "diff", "--diff-filter=D", "--name-only", parent_sha, sha).stdout
        assert "tests/f2.py" in deleted

    def test_pure_wipe_carries_no_staged_deletions(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo_with_dirs(tmp_path, "repo", ["docs"])
        slug = "sdlc-wipe7"
        wt = _add_linked_worktree(repo, slug)
        _gut(wt, "docs")

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is False
        assert (
            _git(wt, "diff", "--cached", "--diff-filter=D", "--name-only", "HEAD").stdout.strip()
            == ""
        )

    def test_pre_staged_wipe_is_unstaged_and_additive_work_still_preserved(self, tmp_path):
        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo_with_dirs(tmp_path, "repo", ["docs", "tests"])
        slug = "sdlc-wipe8"
        wt = _add_linked_worktree(repo, slug)
        parent_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()

        _gut(wt, "docs")
        (wt / "tests" / "f1.py").write_text("# edited\n")
        (wt / "tests" / "f4.py").write_text("# new\n")
        _git(wt, "add", "-A")  # a previous pass already staged everything, wipe included

        result = preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is True
        sha = result["sha"]
        diff_stat = _git(wt, "diff", "--stat", parent_sha, sha).stdout
        assert "f1.py" in diff_stat
        assert "f4.py" in diff_stat
        assert (
            _git(wt, "diff", "--diff-filter=D", "--name-only", parent_sha, sha).stdout.strip() == ""
        )
        tracked_dirs = _git(wt, "ls-tree", "--name-only", "-d", "HEAD").stdout
        assert "docs" in tracked_dirs

    def test_guard_detection_failure_falls_open_and_warns(self, tmp_path, caplog):
        from agent import worktree_manager as wm

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-wipe9"
        wt = _add_linked_worktree(repo, slug)
        _dirty(wt)

        real_run = wm.subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if "ls-tree" in cmd:
                raise OSError("ls-tree exploded")
            return real_run(cmd, *args, **kwargs)

        with patch.object(wm.subprocess, "run", side_effect=fake_run):
            with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
                result = wm.preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is True
        assert "refused" not in result
        joined = " ".join(r.message for r in caplog.records)
        assert "worktree-wip-guard-failed" in joined
        assert "stage=detection" in joined

    def test_ls_files_failure_after_detection_refuses_and_never_commits(self, tmp_path, caplog):
        from agent import worktree_manager as wm

        repo = _init_git_repo_with_dirs(tmp_path, "repo", ["docs", "tests"])
        slug = "sdlc-wipe10"
        wt = _add_linked_worktree(repo, slug)
        parent_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()

        _gut(wt, "docs")
        (wt / "tests" / "f1.py").write_text("# edited\n")

        real_run = wm.subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if "ls-files" in cmd:
                raise OSError("ls-files exploded")
            return real_run(cmd, *args, **kwargs)

        with patch.object(wm.subprocess, "run", side_effect=fake_run):
            with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
                result = wm.preserve_uncommitted_worktree_changes(repo, slug, wt)

        # The head sha is the assertion that matters -- under a single shared
        # `except`, this scenario falls through to `git add -A` and commits
        # the wipe, which "did not raise" alone would not catch.
        assert _git(wt, "rev-parse", "HEAD").stdout.strip() == parent_sha
        assert _git(repo, "rev-parse", "--verify", f"refs/session-wip/{slug}").returncode != 0
        assert result["refused"] == "missing-tracked-dirs"
        assert "guard_error" in result

        joined = " ".join(r.message for r in caplog.records)
        assert "worktree-wip-guard-failed" in joined
        assert "worktree-wip-refused-wipe" in joined

    def test_branch_resolve_timeout_still_preserves_ordinary_dirty_tree(self, tmp_path, caplog):
        """Regression (#3167 review follow-up): the `rev-parse --abbrev-ref
        HEAD` read used to name the WIP ref runs on every preserve path,
        including the ordinary non-wipe one, with no narrow try/except of its
        own. A non-zero rc already falls back to the slug-derived ref; an
        exception (e.g. `TimeoutExpired`) must degrade the same way rather
        than propagate to the outer handler, which would skip `add -A`, the
        WIP commit, and the ref write entirely -- losing legitimate
        uncommitted work on a normally dirty worktree.
        """
        import subprocess as _sp

        from agent import worktree_manager as wm

        repo = _init_git_repo(tmp_path)
        slug = "sdlc-branchto"
        wt = _add_linked_worktree(repo, slug)
        head_before = _git(wt, "rev-parse", "HEAD").stdout.strip()
        _dirty(wt)

        real_run = wm.subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if "rev-parse" in cmd and "--abbrev-ref" in cmd and "HEAD" in cmd:
                raise _sp.TimeoutExpired(cmd=cmd, timeout=5)
            return real_run(cmd, *args, **kwargs)

        with patch.object(wm.subprocess, "run", side_effect=fake_run):
            with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
                result = wm.preserve_uncommitted_worktree_changes(repo, slug, wt)

        assert result["preserved"] is True
        assert result["was_clean"] is False
        sha = result["sha"]
        assert sha and sha != head_before
        # Falls back to the slug-derived ref, exactly like a non-zero rc would.
        assert result["ref"] == f"refs/session-wip/{slug}"

        ref_sha = _git(repo, "rev-parse", f"refs/session-wip/{slug}").stdout.strip()
        assert ref_sha == sha
        assert _git(wt, "rev-parse", "HEAD").stdout.strip() == sha
        assert _git(wt, "status", "--porcelain").stdout.strip() == ""

        joined = " ".join(r.message for r in caplog.records)
        assert "worktree-wip-branch-resolve-failed" in joined

    def test_ref_slug_follows_checked_out_branch(self, tmp_path):
        import subprocess as _sp

        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        wt = repo / ".worktrees" / "foo"
        _sp.run(
            ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "session/bar", str(wt)],
            check=True,
        )
        _dirty(wt)

        result = preserve_uncommitted_worktree_changes(repo, "foo", wt)

        assert result["preserved"] is True
        assert result["ref"] == "refs/session-wip/bar"
        assert _git(repo, "rev-parse", "--verify", "refs/session-wip/bar").returncode == 0

    def test_detached_head_falls_back_to_slug_argument(self, tmp_path):
        import subprocess as _sp

        from agent.worktree_manager import preserve_uncommitted_worktree_changes

        repo = _init_git_repo(tmp_path)
        head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        wt = repo / ".worktrees" / "detached-wt"
        _sp.run(
            ["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(wt), head_sha],
            check=True,
        )
        _dirty(wt)

        result = preserve_uncommitted_worktree_changes(repo, "detached-wt", wt)

        assert result["preserved"] is True
        assert result["ref"] == "refs/session-wip/detached-wt"
        assert result["ref"] != "refs/session-wip/HEAD"
