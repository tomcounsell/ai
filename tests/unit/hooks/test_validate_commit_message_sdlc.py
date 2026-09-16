"""Behavioral suite for the commit-message SDLC guard (#3259).

No test file existed for this hook, which is why a fleet-deployed guard that
answered about the wrong checkout survived. The hook resolves git state from
the directory the intercepted command will ACTUALLY run in; every test here
builds real git repos on disk rather than mocking subprocess, because the
defect lived in what git actually reports from a linked worktree -- something
a mock would have been written to agree with.

The two-checkout tests are the load-bearing ones. They must fail against the
pre-fix hook in BOTH directions: false block (worktree on a feature branch,
main checkout on `main` with a staged `.py`) and false allow (worktree on
`main` with a staged `.py`, main checkout on a feature branch).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SDLC_HOOKS_DIR = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "sdlc"
if str(SDLC_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(SDLC_HOOKS_DIR))

import sdlc_context  # noqa: E402
import validate_commit_message_sdlc as hook  # noqa: E402

GIT_IDENTITY = [
    "-c",
    "user.email=guardtest@localhost",
    "-c",
    "user.name=guardtest",
]


def git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *GIT_IDENTITY, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def make_repo(root: Path, name: str) -> Path:
    """A real git repo named `name` with one commit on a branch named `main`.

    The branch is pinned with symbolic-ref before the first commit rather than
    trusting `init.defaultBranch`, which is still `master` on many hosts.
    """
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    (repo / "README.md").write_text("base\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "base")
    return repo


def add_worktree(repo: Path, path: Path, branch: str) -> Path:
    """Add a linked worktree at `path` checked out on a NEW branch `branch`."""
    git(repo, "worktree", "add", "-b", branch, str(path), "main")
    return path


def worktree_on_main(repo: Path, path: Path) -> Path:
    """A linked worktree checked out on `main`, with the primary checkout
    moved to a feature branch first.

    git refuses to check out a branch in two places at once, so the ordering
    is forced -- and it is also the exact shape the defect lives in: the lane
    is on main, the shared checkout is not.
    """
    git(repo, "checkout", "-q", "-b", "feature/x")
    git(repo, "worktree", "add", str(path), "main")
    return path


def stage_py(directory: Path, filename: str) -> None:
    (directory / filename).write_text("x = 1\n")
    git(directory, "add", filename)


# ---------------------------------------------------------------------------
# effective_git_dir: the four-rung precedence
# ---------------------------------------------------------------------------


class TestEffectiveGitDirPrecedence:
    def test_git_dash_c_wins_over_the_payload_cwd(self, tmp_path):
        assert sdlc_context.effective_git_dir(
            f"git -C {tmp_path / 'lane'} commit -m x", str(tmp_path)
        ) == str(tmp_path / "lane")

    def test_git_dash_c_is_read_only_from_the_committing_simple_command(self, tmp_path):
        # The `-C` belongs to a `git status` earlier in the chain; the commit
        # itself is unscoped, so the payload cwd is the honest answer.
        cmd = f"git -C {tmp_path / 'other'} status && git commit -m x"
        assert sdlc_context.effective_git_dir(cmd, str(tmp_path)) == str(tmp_path)

    def test_leading_cd_wins_over_the_payload_cwd(self, tmp_path):
        assert sdlc_context.effective_git_dir(
            f"cd {tmp_path / 'lane'} && git commit -m x", str(tmp_path)
        ) == str(tmp_path / "lane")

    def test_a_relative_cd_resolves_against_the_payload_cwd(self, tmp_path):
        assert sdlc_context.effective_git_dir(
            "cd .worktrees/lane-a && git commit -m x", str(tmp_path)
        ) == str(tmp_path / ".worktrees" / "lane-a")

    def test_the_payload_cwd_is_used_when_the_command_names_no_directory(self, tmp_path):
        assert sdlc_context.effective_git_dir("git commit -m x", str(tmp_path)) == str(tmp_path)

    def test_process_cwd_is_the_last_resort_when_the_payload_carries_no_cwd(self):
        assert sdlc_context.effective_git_dir("git commit -m x", "") == os.getcwd()


class TestEffectiveGitDirNeverRaises:
    def test_empty_inputs_return_a_usable_directory(self):
        assert sdlc_context.effective_git_dir("", "") == os.getcwd()

    def test_unbalanced_quotes_fall_through_instead_of_propagating(self, tmp_path):
        # shlex.split raises ValueError on this; the rung is skipped.
        assert sdlc_context.effective_git_dir(
            'cd "unterminated && git commit -m x', str(tmp_path)
        ) == str(tmp_path)

    def test_cd_with_no_argument_is_skipped_not_indexed_into(self, tmp_path):
        assert sdlc_context.effective_git_dir("cd && git commit -m x", str(tmp_path)) == str(
            tmp_path
        )

    def test_git_dash_c_with_no_argument_is_skipped_not_indexed_into(self, tmp_path):
        assert sdlc_context.effective_git_dir("git commit -m x -C", str(tmp_path)) == str(tmp_path)


class TestUnexpandedShellConstructsFallThrough:
    """An unexpanded construct used as a directory sends every git call into
    its fail-open handler, which ALLOWS. The payload cwd is a real directory
    and is the correct answer for exactly this command shape.
    """

    @pytest.mark.parametrize(
        "command",
        [
            'cd "$(git rev-parse --show-toplevel)/.worktrees/lane-a" && git commit -m x',
            "git -C $REPO_ROOT commit -m x",
            'git -C "${REPO_ROOT}/.worktrees/lane-a" commit -m x',
            "cd `pwd`/lane && git commit -m x",
        ],
    )
    def test_the_token_is_rejected_and_the_payload_cwd_wins(self, command, tmp_path):
        assert sdlc_context.effective_git_dir(command, str(tmp_path)) == str(tmp_path)


# ---------------------------------------------------------------------------
# Repo identity
# ---------------------------------------------------------------------------


class TestRepoIdentityIsWorktreeCorrect:
    def test_a_worktree_of_popoto_is_identified_as_popoto(self, tmp_path):
        repo = make_repo(tmp_path, "popoto")
        wt = add_worktree(repo, tmp_path / "wt", "lane-a")
        assert hook.get_repo_name(str(wt)) == "popoto"

    def test_the_worktree_basename_is_not_the_answer(self, tmp_path):
        """The regression this whole fix exists for: the worktree root's
        basename is the lane slug, and using it would clear the `!= popoto`
        gate and allow.
        """
        repo = make_repo(tmp_path, "popoto")
        wt = add_worktree(repo, tmp_path / "definitely-not-popoto", "lane-a")
        assert hook.get_repo_name(str(wt)) != "definitely-not-popoto"

    def test_a_plain_checkout_still_resolves(self, tmp_path):
        repo = make_repo(tmp_path, "popoto")
        assert hook.get_repo_name(str(repo)) == "popoto"

    def test_old_git_rung_resolves_a_relative_common_dir_from_a_worktree(
        self, tmp_path, monkeypatch
    ):
        """Force only `--path-format=absolute` to fail, as git < 2.31 does,
        leaving the bare probe to return a path that may be relative.
        """
        repo = make_repo(tmp_path, "popoto")
        wt = add_worktree(repo, tmp_path / "wt", "lane-a")
        real_git = hook._git

        def no_path_format(args, cwd):
            if "--path-format=absolute" in args:
                return None
            return real_git(args, cwd)

        monkeypatch.setattr(hook, "_git", no_path_format)
        assert hook.get_repo_name(str(wt)) == "popoto"

    def test_a_non_repo_directory_yields_unknown_identity(self, tmp_path):
        assert hook.get_repo_name(str(tmp_path)) is None


class TestUnknownIdentityTakesTheRestrictiveBranch:
    def test_probe_failure_inside_a_worktree_on_main_does_not_degrade_to_allow(
        self, tmp_path, monkeypatch
    ):
        """Both identity rungs fail while the effective directory is a linked
        worktree on `main` with a staged `.py`. Unknown identity must NOT be
        read as "not popoto, therefore fine" -- this is the test that makes
        the deleted worktree-root fallback un-reintroducible.
        """
        repo = make_repo(tmp_path, "popoto")
        wt = worktree_on_main(repo, tmp_path / "wt")
        stage_py(wt, "code.py")

        real_git = hook._git

        def identity_probes_fail(args, cwd):
            if "--git-common-dir" in args:
                return None
            return real_git(args, cwd)

        monkeypatch.setattr(hook, "_git", identity_probes_fail)
        reason = hook.commit_block_reason("git commit -m x", str(wt))
        assert reason is not None
        assert "code.py" in reason

    def test_a_non_git_directory_still_allows(self, tmp_path):
        """The restrictive branch degrades to allow only where git itself is
        unusable: get_current_branch fails first, so nothing is blocked.
        """
        assert hook.commit_block_reason("git commit -m x", str(tmp_path)) is None


# ---------------------------------------------------------------------------
# The two-checkout regression: both directions
# ---------------------------------------------------------------------------


class TestTwoCheckoutRegression:
    def test_false_allow_worktree_on_main_with_staged_code_is_blocked(self, tmp_path):
        """Worktree on `main` with a staged `.py`; the main checkout sits on a
        feature branch. Pre-fix the hook read the main checkout, saw a feature
        branch and allowed -- code landing on main through the guard.
        """
        repo = make_repo(tmp_path, "popoto")
        wt = worktree_on_main(repo, tmp_path / "wt")
        stage_py(wt, "lane_code.py")

        reason = hook.commit_block_reason("git commit -m x", str(wt))
        assert reason is not None
        assert "lane_code.py" in reason

    def test_false_block_worktree_on_a_feature_branch_is_allowed(self, tmp_path):
        """Worktree on a feature branch; the main checkout sits on `main` with
        its own stale staged `.py`. Pre-fix the hook read the main checkout
        and blocked a commit that was never on main.
        """
        repo = make_repo(tmp_path, "popoto")
        wt = add_worktree(repo, tmp_path / "wt", "lane-a")
        stage_py(repo, "main_checkout_code.py")
        stage_py(wt, "lane_code.py")

        assert hook.commit_block_reason("git commit -m x", str(wt)) is None

    def test_the_reason_names_the_effective_directorys_staged_files(self, tmp_path):
        """The user-visible half: the block must name what the lane staged,
        never what the other checkout staged.
        """
        repo = make_repo(tmp_path, "popoto")
        wt = worktree_on_main(repo, tmp_path / "wt")
        stage_py(wt, "lane_code.py")
        stage_py(repo, "other_checkout_code.py")

        reason = hook.commit_block_reason("git commit -m x", str(wt))
        assert reason is not None
        assert "lane_code.py" in reason
        assert "other_checkout_code.py" not in reason

    def test_a_cd_into_the_worktree_is_honored_from_the_main_checkout_cwd(self, tmp_path):
        """The payload cwd is the main checkout, but the command cds into the
        worktree first. The verdict must follow the cd.
        """
        repo = make_repo(tmp_path, "popoto")
        wt = worktree_on_main(repo, tmp_path / "wt")
        stage_py(wt, "lane_code.py")

        reason = hook.commit_block_reason(f"cd {wt} && git commit -m x", str(repo))
        assert reason is not None
        assert "lane_code.py" in reason


# ---------------------------------------------------------------------------
# Unprotected repos, non-code files, fail-open
# ---------------------------------------------------------------------------


class TestAllowPaths:
    def test_an_unprotected_repo_on_main_with_staged_code_is_allowed(self, tmp_path):
        repo = make_repo(tmp_path, "some-other-repo")
        stage_py(repo, "code.py")
        assert hook.commit_block_reason("git commit -m x", str(repo)) is None

    def test_a_worktree_of_an_unprotected_repo_is_allowed(self, tmp_path):
        repo = make_repo(tmp_path, "some-other-repo")
        wt = worktree_on_main(repo, tmp_path / "wt")
        stage_py(wt, "code.py")
        assert hook.commit_block_reason("git commit -m x", str(wt)) is None

    def test_non_code_files_on_main_are_allowed(self, tmp_path):
        repo = make_repo(tmp_path, "popoto")
        (repo / "notes.md").write_text("hi\n")
        git(repo, "add", "notes.md")
        assert hook.commit_block_reason("git commit -m x", str(repo)) is None

    def test_an_empty_staged_set_is_allowed(self, tmp_path):
        repo = make_repo(tmp_path, "popoto")
        assert hook.commit_block_reason("git commit -m x", str(repo)) is None

    def test_a_non_commit_command_is_allowed_on_the_fast_path(self, tmp_path):
        repo = make_repo(tmp_path, "popoto")
        stage_py(repo, "code.py")
        assert hook.commit_block_reason("git status", str(repo)) is None

    def test_a_deleted_effective_directory_fails_open(self, tmp_path):
        repo = make_repo(tmp_path, "popoto")
        gone = tmp_path / "gone"
        assert hook.commit_block_reason(f"cd {gone} && git commit -m x", str(repo)) is None


# ---------------------------------------------------------------------------
# main() over real stdin: the wiring
# ---------------------------------------------------------------------------


def run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SDLC_HOOKS_DIR / "validate_commit_message_sdlc.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestMainWiring:
    def test_main_blocks_end_to_end_using_the_payload_cwd(self, tmp_path):
        repo = make_repo(tmp_path, "popoto")
        wt = worktree_on_main(repo, tmp_path / "wt")
        stage_py(wt, "lane_code.py")

        result = run_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"},
                "cwd": str(wt),
            }
        )
        assert result.returncode == 0
        assert json.loads(result.stdout)["decision"] == "block"

    def test_main_allows_silently(self, tmp_path):
        repo = make_repo(tmp_path, "popoto")
        result = run_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"},
                "cwd": str(repo),
            }
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(
        "raw",
        ["not json at all", "{}", '{"tool_name": "Bash"}', ""],
    )
    def test_malformed_stdin_exits_zero_with_empty_stdout(self, raw):
        """A PreToolUse hook under exit_policy=propagate that exits non-zero
        denies every Bash call. This path is safety-critical.
        """
        result = subprocess.run(
            [sys.executable, str(SDLC_HOOKS_DIR / "validate_commit_message_sdlc.py")],
            input=raw,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_a_non_bash_tool_is_ignored(self, tmp_path):
        result = run_hook(
            {
                "tool_name": "Write",
                "tool_input": {"command": "git commit -m x"},
                "cwd": str(tmp_path),
            }
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestCommitRecognition:
    """Deciding "is this a git commit" by substring was wrong in both
    directions, and the miss was the load-bearing one.

    `git -C <worktree> commit` is the shape a lane's commit actually takes, and
    the literal `"git commit"` never appears in it. The resolver underneath
    could be perfect and the guard would still allow every worktree commit,
    which is how the update self-check (#3259) found this.
    """

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m x",
            "git -C /some/lane commit -m x",
            "git -C /some/lane -c user.email=a@b commit -m x",
            "git --git-dir=/r/.git --work-tree=/r commit -m x",
            "GIT_AUTHOR_NAME=x git commit -m x",
            "/usr/bin/git commit -m x",
            "git status && git -C /lane commit -m x",
            "git commit --amend --no-edit",
        ],
    )
    def test_recognized_as_a_commit(self, command):
        assert hook.is_git_commit(command) is True

    @pytest.mark.parametrize(
        "command",
        [
            "",
            "git status",
            "git -C /lane status",
            "git log --oneline -1",
            'echo "run git commit next"',
            "grep -r 'git commit' docs/",
            "git -C /lane push",
        ],
    )
    def test_not_recognized_as_a_commit(self, command):
        assert hook.is_git_commit(command) is False

    def test_dash_c_worktree_commit_blocks_end_to_end(self, tmp_path):
        """The regression the self-check caught, driven through the real
        decision function against a real repo -- not through the recognizer in
        isolation, because the two were individually correct and jointly wrong.
        """
        repo = make_repo(tmp_path, "popoto")
        lane = worktree_on_main(repo, tmp_path / "lane-a")
        (lane / "mod.py").write_text("x = 1\n")
        git(lane, "add", "mod.py")

        reason = hook.commit_block_reason(f"git -C {lane} commit -m wip", str(tmp_path))

        assert reason is not None
        assert "mod.py" in reason
