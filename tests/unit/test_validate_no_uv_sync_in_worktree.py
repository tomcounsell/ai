"""Unit tests for validate_no_uv_sync_in_worktree.py hook validator (issue #2050)."""

import json
import subprocess
import sys
from pathlib import Path

# Hook scripts live in .claude/hooks/validators/
VALIDATORS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "validators"
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))

HOOK_PATH = VALIDATORS_DIR / "validate_no_uv_sync_in_worktree.py"


def import_validator():
    import validate_no_uv_sync_in_worktree

    return validate_no_uv_sync_in_worktree


class TestFindViolationBlocks:
    """The guard must FIRE (block) on `uv sync` from a worktree cwd."""

    def test_blocks_plain_uv_sync_from_dot_worktrees(self):
        mod = import_validator()
        reason = mod.find_violation("uv sync", "/repo/.worktrees/my-slug")
        assert reason is not None
        assert "uv pip install" in reason
        assert "/repo/.worktrees/my-slug" in reason

    def test_blocks_uv_sync_frozen(self):
        mod = import_validator()
        reason = mod.find_violation("uv sync --frozen", "/repo/.worktrees/my-slug")
        assert reason is not None

    def test_blocks_from_claude_worktrees(self):
        mod = import_validator()
        reason = mod.find_violation("uv sync", "/repo/.claude/worktrees/agent-x")
        assert reason is not None

    def test_blocks_cd_prefix_chain(self):
        mod = import_validator()
        reason = mod.find_violation("cd .worktrees/my-slug && uv sync --frozen", "/repo")
        assert reason is not None

    def test_blocks_cd_prefix_chain_semicolon(self):
        mod = import_validator()
        reason = mod.find_violation("cd .worktrees/my-slug; uv sync", "/repo")
        assert reason is not None


class TestFindViolationAllows:
    """The guard must NOT fire on legitimate commands."""

    def test_allows_uv_sync_from_repo_root(self):
        mod = import_validator()
        reason = mod.find_violation("uv sync", "/repo")
        assert reason is None

    def test_allows_uv_pip_install_from_worktree(self):
        mod = import_validator()
        reason = mod.find_violation("uv pip install foo", "/repo/.worktrees/my-slug")
        assert reason is None

    def test_allows_uv_run_from_worktree(self):
        mod = import_validator()
        reason = mod.find_violation("uv run pytest", "/repo/.worktrees/my-slug")
        assert reason is None

    def test_allows_uv_lock_from_worktree(self):
        mod = import_validator()
        reason = mod.find_violation("uv lock", "/repo/.worktrees/my-slug")
        assert reason is None

    def test_allows_git_commit_message_containing_uv_sync_substring(self):
        """Command-position anchoring: `uv sync` appearing inside an unrelated
        argument (e.g. a commit message) must NOT trip the guard."""
        mod = import_validator()
        reason = mod.find_violation('git commit -m "fix uv sync bug"', "/repo/.worktrees/my-slug")
        assert reason is None

    def test_allows_file_named_uv_sync(self):
        mod = import_validator()
        reason = mod.find_violation("cat uv-sync-notes.txt", "/repo/.worktrees/my-slug")
        assert reason is None

    def test_allows_worktrees_backup_sibling_dir(self):
        """Path-component match, not substring: `.worktrees-backup` must not
        match `.worktrees`."""
        mod = import_validator()
        reason = mod.find_violation("uv sync", "/repo/.worktrees-backup/x")
        assert reason is None


class TestFailOpen:
    """Any parse error must result in exit 0 / no violation -- never a crash."""

    def test_empty_command(self):
        mod = import_validator()
        assert mod.find_violation("", "/repo/.worktrees/x") is None

    def test_empty_cwd(self):
        mod = import_validator()
        assert mod.find_violation("uv sync", "") is None

    def test_unparseable_shell_tokens(self):
        mod = import_validator()
        # Unbalanced quote -- shlex.split raises ValueError internally.
        reason = mod.find_violation('uv sync "unterminated', "/repo/.worktrees/x")
        assert reason is None


class TestHookProtocol:
    """Integration test: drive the guard through the actual stdin/stdout hook protocol."""

    def _run_hook(self, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_hook_blocks_uv_sync_in_worktree(self):
        result = self._run_hook(
            {
                "tool_name": "Bash",
                "cwd": "/repo/.worktrees/my-slug",
                "tool_input": {"command": "uv sync --frozen"},
            }
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["decision"] == "block"
        assert "uv pip install" in out["reason"]

    def test_hook_allows_uv_sync_at_repo_root(self):
        result = self._run_hook(
            {
                "tool_name": "Bash",
                "cwd": "/repo",
                "tool_input": {"command": "uv sync"},
            }
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_hook_allows_uv_pip_install_in_worktree(self):
        result = self._run_hook(
            {
                "tool_name": "Bash",
                "cwd": "/repo/.worktrees/my-slug",
                "tool_input": {"command": "uv pip install foo"},
            }
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_hook_ignores_non_bash_tool(self):
        result = self._run_hook(
            {
                "tool_name": "Read",
                "cwd": "/repo/.worktrees/my-slug",
                "tool_input": {"file_path": "uv sync"},
            }
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_hook_fails_open_on_malformed_json(self):
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="{not valid json",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_hook_fails_open_on_empty_stdin(self):
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
