"""Tests for the destructive-git-in-shared-checkout PreToolUse guard (issue #2448).

Mirrors the structure of ``test_validate_no_destructive_git_in_worktree.py``:
a pure, injectable ``find_violation(command, cwd, *, repo_root, in_worktree)``
core (classification injected -- no git calls) plus a thin smoke test for the
live-resolution wrapper ``find_violation_from_hook_input``, plus the
JSON-stdin ``_run_hook`` contract exercised via subprocess for fail-open
coverage.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

VALIDATOR = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "hooks"
    / "validators"
    / "validate_no_destructive_git_in_shared_checkout.py"
)

sys.path.insert(0, str(VALIDATOR.parent))
import validate_no_destructive_git_in_shared_checkout as guard  # noqa: E402

# The guard's positive-identity bind resolves `_THIS_REPO_ROOT` via git at
# import time (this repo, wherever this test file's own checkout is). Tests
# that want to simulate "cwd IS the shared checkout" must compare against
# that real value -- an arbitrary literal like "/repo" would never match and
# every "BLOCK" case would silently degrade into a false "foreign repo"
# ALLOW, which is exactly the kind of regression this guard exists to catch.
REPO_ROOT = guard._THIS_REPO_ROOT
assert REPO_ROOT, "guard._THIS_REPO_ROOT failed to resolve; is this running inside a git repo?"
FOREIGN_ROOT = "/other/repo"


class TestFindViolationBlocks:
    """Whole-tree destructive shapes in the shared checkout (this repo,
    not a worktree) are blocked."""

    @pytest.mark.parametrize(
        "command",
        [
            "git reset --hard",
            "git reset --hard HEAD",
            "git reset --hard origin/main",
            "git clean -fd",
            "git clean -f",
            "git clean -fdx",
            "git clean --force",
            "git checkout origin/main -- .",
            "git checkout .",
            "git checkout -- .",
            "git restore .",
        ],
    )
    def test_blocks_whole_tree_shapes_in_shared_checkout(self, command):
        reason = guard.find_violation(command, REPO_ROOT, repo_root=REPO_ROOT, in_worktree=False)
        assert reason is not None, f"expected block for: {command}"


class TestFindViolationAllowsInWorktree:
    """Build-failing if any of these block: the guard must NEVER fire
    inside a `.worktrees/{slug}/` session, regardless of shape."""

    @pytest.mark.parametrize(
        "command",
        [
            "git reset --hard",
            "git reset --hard HEAD",
            "git clean -fd",
            "git clean -f",
            "git checkout origin/main -- .",
            "git checkout .",
            "git checkout -- .",
            "git restore .",
        ],
    )
    def test_allows_whole_tree_shapes_in_worktree(self, command):
        reason = guard.find_violation(
            command, "/repo/.worktrees/slug", repo_root=REPO_ROOT, in_worktree=True
        )
        assert reason is None, f"must never block in a worktree, but blocked: {command}"


class TestFindViolationAllowsForeignRepo:
    """Build-failing if this blocks: a foreign repo's toplevel must never
    match, even with a destructive whole-tree shape."""

    @pytest.mark.parametrize(
        "command",
        ["git reset --hard", "git clean -fd", "git checkout .", "git restore ."],
    )
    def test_allows_foreign_repo_toplevel(self, command):
        reason = guard.find_violation(
            command, FOREIGN_ROOT, repo_root=FOREIGN_ROOT, in_worktree=False
        )
        assert reason is None, f"must never block a foreign repo, but blocked: {command}"

    def test_allows_non_repo_cwd(self):
        # repo_root=None models "not a git repo / git error" classification.
        reason = guard.find_violation(
            "git reset --hard", "/tmp/not-a-repo", repo_root=None, in_worktree=False
        )
        assert reason is None


class TestFindViolationAllowsPathScoped:
    """Path-scoped variants stay allowed in the shared checkout."""

    @pytest.mark.parametrize(
        "command",
        [
            "git checkout origin/main -- docs/plans/x.md",
            "git restore docs/plans/x.md",
            "git checkout -- file.py",
            "git restore path/",
        ],
    )
    def test_allows_path_scoped(self, command):
        reason = guard.find_violation(command, REPO_ROOT, repo_root=REPO_ROOT, in_worktree=False)
        assert reason is None, f"path-scoped command must be allowed: {command}"


class TestFindViolationAllowsFalsePositives:
    """Command-position anchoring: a destructive-looking substring in a
    non-command position (e.g. a commit message) must not trigger."""

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m 'reset --hard notes'",
            "git status",
            "git checkout -b new-branch",
            "echo 'git reset --hard'",
        ],
    )
    def test_allows_false_positives(self, command):
        reason = guard.find_violation(command, REPO_ROOT, repo_root=REPO_ROOT, in_worktree=False)
        assert reason is None, f"must not block: {command}"


class TestFindViolationAllowsStash:
    """The shared-checkout guard's deny-list is five whole-tree shapes only
    -- it must NOT inherit the sibling worktree guard's stash-blocking
    behavior."""

    @pytest.mark.parametrize(
        "command",
        ["git stash", "git stash push", "git stash push -m 'wip'"],
    )
    def test_allows_stash_in_shared_checkout(self, command):
        reason = guard.find_violation(command, REPO_ROOT, repo_root=REPO_ROOT, in_worktree=False)
        assert reason is None, (
            f"stash must never be blocked by the shared-checkout guard: {command}"
        )


class TestFindViolationAllowsOverrideAndEdgeCases:
    def test_allows_with_override_token(self):
        reason = guard.find_violation(
            "git reset --hard  # allow-destructive-git",
            REPO_ROOT,
            repo_root=REPO_ROOT,
            in_worktree=False,
        )
        assert reason is None

    def test_empty_inputs_return_none(self):
        assert guard.find_violation("", REPO_ROOT, repo_root=REPO_ROOT, in_worktree=False) is None
        assert (
            guard.find_violation("git reset --hard", "", repo_root=REPO_ROOT, in_worktree=False)
            is None
        )


class TestBlockMessage:
    """The block reason points at the worktree-add alternative and carries
    no em-dashes."""

    def test_reason_mentions_worktree_add_and_override(self):
        reason = guard.find_violation(
            "git reset --hard", REPO_ROOT, repo_root=REPO_ROOT, in_worktree=False
        )
        assert reason is not None
        assert "git worktree add --detach" in reason
        assert "_merge_baseline_main" in reason
        assert "# allow-destructive-git" in reason
        assert "—" not in reason  # no em-dashes in the user-facing string


class TestSharedHelperImportIdentity:
    """Guards against future drift: both validator modules must import the
    same shape-detection functions from the shared hook_utils module, not
    hand-duplicated copies."""

    def test_shared_predicate_functions_are_the_same_objects(self):
        import validate_no_destructive_git_in_worktree as sibling
        from hook_utils import destructive_git_shapes

        assert sibling._effective_dir is destructive_git_shapes.effective_dir
        assert sibling._is_worktree_path is destructive_git_shapes.is_worktree_path
        assert sibling._split_simple_commands is destructive_git_shapes._split_simple_commands
        assert guard._effective_dir is destructive_git_shapes.effective_dir
        assert guard._is_worktree_path is destructive_git_shapes.is_worktree_path
        assert guard._split_simple_commands is destructive_git_shapes._split_simple_commands
        assert (
            guard._is_destructive_git_shared_checkout
            is destructive_git_shapes.is_destructive_git_shared_checkout
        )


class TestFindViolationFromHookInputSmoke:
    """Thin smoke test for the live-resolution wrapper against a real
    non-git tempdir (hits the 'not a git repo' fail-open ALLOW branch
    without needing a real git repo with commits)."""

    def test_non_git_tempdir_allows(self):
        with tempfile.TemporaryDirectory() as tmp:
            reason = guard.find_violation_from_hook_input("git reset --hard", tmp)
            assert reason is None

    def test_worktree_path_segment_allows_without_git_call(self):
        reason = guard.find_violation_from_hook_input(
            "git reset --hard", "/repo/.worktrees/some-slug"
        )
        assert reason is None


class TestCdChainResolution:
    """Success Criterion 5: `cd .worktrees/slug && git reset --hard` is
    ALLOWED (resolves into a worktree); `cd <repo-root> && git reset --hard`
    is BLOCKED. Exercised against a real throwaway git repo + worktree via
    `find_violation_from_hook_input`'s live cd-prefix and
    `git rev-parse --show-toplevel` resolution.

    The positive-identity bind can never recognize a throwaway repo as
    "this repo" on its own (by design -- see the module docstring), so
    `guard._THIS_REPO_ROOT` is monkeypatched to the throwaway repo's root
    for the duration of each test.
    """

    def _init_repo_with_worktree(self, tmp_path: Path) -> tuple[Path, Path]:
        def g(*args, cwd):
            subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

        repo = tmp_path / "repo"
        repo.mkdir()
        g("init", "-q", "-b", "main", cwd=repo)
        g("config", "user.email", "t@example.com", cwd=repo)
        g("config", "user.name", "T", cwd=repo)
        (repo / "seed.txt").write_text("seed\n")
        g("add", "seed.txt", cwd=repo)
        g("commit", "-q", "-m", "seed", cwd=repo)
        wt = repo / ".worktrees" / "slug"
        g("worktree", "add", "-q", "-b", "session/slug", str(wt), cwd=repo)
        return repo, wt

    def test_cd_into_worktree_allows(self, tmp_path, monkeypatch):
        repo, wt = self._init_repo_with_worktree(tmp_path)
        monkeypatch.setattr(guard, "_THIS_REPO_ROOT", str(repo))
        reason = guard.find_violation_from_hook_input(f"cd {wt} && git reset --hard", str(repo))
        assert reason is None

    def test_cd_into_repo_root_blocks(self, tmp_path, monkeypatch):
        repo, wt = self._init_repo_with_worktree(tmp_path)
        monkeypatch.setattr(guard, "_THIS_REPO_ROOT", str(repo))
        reason = guard.find_violation_from_hook_input(f"cd {repo} && git reset --hard", str(wt))
        assert reason is not None


class TestGitDirFlagOverrideResolution:
    """Regression coverage for the review finding on PR #2501: `-C`/
    `--git-dir` flags on the git invocation itself must retarget the
    effective directory, the same way a `cd <path>` prefix does --
    otherwise `git -C <main-root> reset --hard` issued from an unrelated
    cwd (a worktree, `/tmp`, anywhere) bypasses the guard while still
    destroying the shared main checkout. Exercised against a real
    throwaway git repo + worktree, mirroring `TestCdChainResolution`.
    """

    def _init_repo_with_worktree(self, tmp_path: Path) -> tuple[Path, Path]:
        def g(*args, cwd):
            subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

        repo = tmp_path / "repo"
        repo.mkdir()
        g("init", "-q", "-b", "main", cwd=repo)
        g("config", "user.email", "t@example.com", cwd=repo)
        g("config", "user.name", "T", cwd=repo)
        (repo / "seed.txt").write_text("seed\n")
        g("add", "seed.txt", cwd=repo)
        g("commit", "-q", "-m", "seed", cwd=repo)
        wt = repo / ".worktrees" / "slug"
        g("worktree", "add", "-q", "-b", "session/slug", str(wt), cwd=repo)
        return repo, wt

    def test_dash_c_main_root_from_worktree_blocks(self, tmp_path, monkeypatch):
        repo, wt = self._init_repo_with_worktree(tmp_path)
        monkeypatch.setattr(guard, "_THIS_REPO_ROOT", str(repo))
        reason = guard.find_violation_from_hook_input(f"git -C {repo} reset --hard", str(wt))
        assert reason is not None

    def test_dash_c_main_root_from_tmp_blocks(self, tmp_path, monkeypatch):
        repo, wt = self._init_repo_with_worktree(tmp_path)
        monkeypatch.setattr(guard, "_THIS_REPO_ROOT", str(repo))
        with tempfile.TemporaryDirectory() as elsewhere:
            reason = guard.find_violation_from_hook_input(f"git -C {repo} reset --hard", elsewhere)
        assert reason is not None

    def test_dash_c_into_worktree_still_allows(self, tmp_path, monkeypatch):
        repo, wt = self._init_repo_with_worktree(tmp_path)
        monkeypatch.setattr(guard, "_THIS_REPO_ROOT", str(repo))
        reason = guard.find_violation_from_hook_input(f"git -C {wt} reset --hard", str(repo))
        assert reason is None

    def test_git_dir_flag_main_root_blocks(self, tmp_path, monkeypatch):
        repo, wt = self._init_repo_with_worktree(tmp_path)
        monkeypatch.setattr(guard, "_THIS_REPO_ROOT", str(repo))
        reason = guard.find_violation_from_hook_input(
            f"git --git-dir={repo}/.git reset --hard", str(wt)
        )
        assert reason is not None


class TestRunHookFailOpen:
    """The JSON-stdin hook contract must fail open on malformed input/errors."""

    def _run(self, stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(VALIDATOR)],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_malformed_json_exits_zero_no_block(self):
        res = self._run("this is not json{{{")
        assert res.returncode == 0
        assert res.stdout.strip() == ""

    def test_non_bash_tool_is_ignored(self):
        res = self._run(json.dumps({"tool_name": "Read", "tool_input": {}}))
        assert res.returncode == 0
        assert res.stdout.strip() == ""

    def test_unparseable_command_fails_open(self):
        res = self._run(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "git reset --hard 'unterminated"},
                    "cwd": "/repo",
                }
            )
        )
        assert res.returncode == 0
        assert res.stdout.strip() == ""

    def test_nonexistent_cwd_fails_open(self):
        res = self._run(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "git reset --hard"},
                    "cwd": "/nonexistent/path/that/does/not/exist",
                }
            )
        )
        assert res.returncode == 0
        assert res.stdout.strip() == ""
