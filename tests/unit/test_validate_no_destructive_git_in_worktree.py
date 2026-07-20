"""Tests for the destructive-git-in-worktree PreToolUse guard (issue #2137).

Mirrors the structure of ``test_validate_no_uv_sync_in_worktree.py``: a pure,
injectable ``find_violation(command, cwd, is_dirty)`` core plus the JSON-stdin
``_run_hook`` contract exercised via subprocess for fail-open coverage, plus a
real-worktree dirty-detection integration path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Import the validator module under test (does not exist yet -> RED).
VALIDATOR = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "hooks"
    / "validators"
    / "validate_no_destructive_git_in_worktree.py"
)

sys.path.insert(0, str(VALIDATOR.parent))
import validate_no_destructive_git_in_worktree as guard  # noqa: E402

WT = "/repo/.worktrees/sdlc-2137"
OUTSIDE = "/repo/src"


class TestFindViolationBlocks:
    """Destructive commands in a dirty worktree cwd are blocked."""

    @pytest.mark.parametrize(
        "command",
        [
            "git reset --hard",
            "git reset --hard HEAD",
            "git reset --hard origin/main",
            "git clean -fd",
            "git clean -f",
            "git clean -fdx",
            "git checkout -- .",
            "git checkout .",
            "git restore .",
            "git stash",
            "git stash push",
            "git stash push -m 'wip'",
        ],
    )
    def test_blocks_destructive_in_dirty_worktree(self, command):
        reason = guard.find_violation(command, WT, is_dirty=True)
        assert reason is not None, f"expected block for: {command}"

    def test_blocks_in_cd_chain(self):
        reason = guard.find_violation(f"cd {WT} && git reset --hard", OUTSIDE, is_dirty=True)
        assert reason is not None


class TestFindViolationAllows:
    """Non-destructive or out-of-scope commands are allowed (None)."""

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git reset --soft HEAD~1",
            "git reset HEAD file.py",
            "git checkout -- specific_file.py",
            "git checkout -b new-branch",
            "git stash push -- specific_file.py",
            "git stash list",
            "git stash pop",
            "git commit -m 'reset --hard in message'",
            "ls -la",
        ],
    )
    def test_allows_non_destructive(self, command):
        assert guard.find_violation(command, WT, is_dirty=True) is None

    def test_allows_when_clean_tree(self):
        # A destructive reset on a CLEAN tree loses nothing -> allowed.
        assert guard.find_violation("git reset --hard", WT, is_dirty=False) is None

    def test_allows_outside_worktree(self):
        assert guard.find_violation("git reset --hard", OUTSIDE, is_dirty=True) is None

    def test_allows_with_override_token(self):
        assert (
            guard.find_violation("git reset --hard  # allow-destructive-git", WT, is_dirty=True)
            is None
        )

    def test_empty_inputs_return_none(self):
        assert guard.find_violation("", WT, is_dirty=True) is None
        assert guard.find_violation("git reset --hard", "", is_dirty=True) is None


class TestBlockMessage:
    """The block reason names the command, the worktree path, and the override."""

    def test_reason_mentions_command_path_and_override(self):
        reason = guard.find_violation("git reset --hard", WT, is_dirty=True)
        assert reason is not None
        assert "reset" in reason
        assert WT in reason
        assert "# allow-destructive-git" in reason


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

    def test_nonexistent_cwd_fails_open(self):
        res = self._run(
            json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "git reset --hard"},
                    "cwd": "/nonexistent/.worktrees/ghost",
                }
            )
        )
        # cwd not a real dirty worktree -> git status fails -> fail open (allow).
        assert res.returncode == 0
        assert res.stdout.strip() == ""


class TestRunHookRealWorktree:
    """End-to-end dirty-detection against a throwaway git worktree."""

    def _init_dirty_worktree(self, tmp_path: Path) -> Path:
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
        wt = repo / ".worktrees" / "sdlc-live"
        g("worktree", "add", "-q", "-b", "session/sdlc-live", str(wt), cwd=repo)
        # dirty it
        (wt / "seed.txt").write_text("seed\nchanged\n")
        return wt

    def test_blocks_reset_hard_in_real_dirty_worktree(self, tmp_path):
        wt = self._init_dirty_worktree(tmp_path)
        res = subprocess.run(
            [sys.executable, str(VALIDATOR)],
            input=json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "git reset --hard"},
                    "cwd": str(wt),
                }
            ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert res.returncode == 0
        payload = json.loads(res.stdout)
        assert payload["decision"] == "block"

    def test_allows_reset_hard_in_real_clean_worktree(self, tmp_path):
        wt = self._init_dirty_worktree(tmp_path)
        # make it clean
        subprocess.run(
            ["git", "checkout", "--", "seed.txt"], cwd=wt, check=True, capture_output=True
        )
        res = subprocess.run(
            [sys.executable, str(VALIDATOR)],
            input=json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "git reset --hard"},
                    "cwd": str(wt),
                }
            ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert res.returncode == 0
        assert res.stdout.strip() == ""
