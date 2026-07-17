"""Unit tests for validate_no_uv_sync_in_worktree.py hook validator (issue #2050)."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.worktree_manager import resolve_main_repo_root

# Hook scripts live in .claude/hooks/validators/. This deliberately uses the
# LOCAL checkout (not resolve_main_repo_root) -- during SDLC review this test
# file runs from inside this feature's own worktree, and the guard module
# under test only exists there until the PR merges. TestRealWorktreeHookDispatch
# below separately resolves the true MAIN_REPO_ROOT where it specifically
# needs the main checkout (to create a throwaway worktree and to exercise the
# "session cwd is the main checkout" allow-case).
LOCAL_ROOT = Path(__file__).resolve().parent.parent.parent
VALIDATORS_DIR = LOCAL_ROOT / ".claude" / "hooks" / "validators"
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))

HOOK_PATH = VALIDATORS_DIR / "validate_no_uv_sync_in_worktree.py"
MAIN_REPO_ROOT = resolve_main_repo_root(LOCAL_ROOT)


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


class TestRealWorktreeHookDispatch:
    """Real-dispatch integration test (issue #2050 Risk 3 / plan Success
    Criterion: "A real-dispatch integration test confirms the hook fires from
    a worktree CWD created via create_worktree()").

    ``TestHookProtocol`` above drives the hook through the JSON stdin
    protocol but with a hard-coded worktree cwd *string* -- it never proves
    the guard is actually reachable from a real worktree's checked-out
    ``.claude/settings.json``. Risk 3 in the plan names exactly this gap: a
    worktree could resolve ``$CLAUDE_PROJECT_DIR`` to a stale settings.json
    copy (predating this guard) and the hook would simply never fire.

    This test creates a real worktree via ``agent.worktree_manager.
    create_worktree()``, reads the PreToolUse/Bash hook wiring out of THAT
    worktree's own checked-out ``.claude/settings.json`` (not the outer
    repo's), and executes the resolved command exactly as Claude Code's
    harness does: a JSON payload on stdin, ``CLAUDE_PROJECT_DIR`` in the
    environment resolved to the worktree path, and the hook subprocess's own
    OS cwd set to the worktree. This exercises real hook loading + worktree
    cwd resolution end to end, not just the validator function in isolation.
    """

    @pytest.fixture
    def real_worktree(self):
        from agent.worktree_manager import create_worktree, remove_worktree

        # Fork the throwaway test worktree from the CURRENT branch tip (not
        # the default "main") so the checked-out .claude/settings.json
        # reflects whatever guard wiring is actually on HEAD right now --
        # this branch pre-merge, or main post-merge. Using the hardcoded
        # default "main" would fork from a ref that doesn't have the guard
        # yet while this PR is still open. Read HEAD from LOCAL_ROOT (this
        # test file's own checkout) -- MAIN_REPO_ROOT is a separate checkout
        # (typically "main") that may be behind this branch pre-merge.
        current_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=LOCAL_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()

        slug = f"test-uv-sync-guard-{uuid.uuid4().hex[:8]}"
        branch_name = f"session/{slug}"
        # Patch out venv provisioning (issue #2052): a real `uv sync` here
        # would be slow AND would make the worktree ISOLATED, flipping the
        # guard to allow -- this fixture specifically needs an UNPROVISIONED
        # worktree so the block path stays exercised end to end.
        with patch("agent.worktree_manager.provision_worktree_venv", return_value=False):
            wt_path = create_worktree(MAIN_REPO_ROOT, slug, base_branch=current_branch)
        try:
            yield wt_path
        finally:
            # remove_worktree(..., delete_branch=True) routes through
            # safe_delete_branch's unmerged-branch-guard, which preserves
            # any branch not merged into "main" -- correct for real work,
            # but this throwaway branch is forked from a non-main HEAD
            # (this feature branch, pre-merge) purely to create a worktree
            # for the test and never receives a commit, so it is always
            # "unmerged" and would otherwise leak a branch on every test
            # run (see cleanup of 12 leaked session/test-uv-sync-guard-*
            # branches during development of this test). It is provably
            # safe to force-delete directly: assert zero unique commits
            # vs. the base branch before doing so.
            remove_worktree(MAIN_REPO_ROOT, slug, delete_branch=False, force=True)
            unique_commits = subprocess.run(
                ["git", "rev-list", f"{current_branch}..{branch_name}"],
                cwd=MAIN_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            assert not unique_commits, (
                f"throwaway test branch {branch_name} has unique commits vs "
                f"{current_branch} -- refusing to force-delete unexpected work"
            )
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=MAIN_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )

    @staticmethod
    def _hook_command_for_bash(worktree_path: Path) -> str:
        """Read the PreToolUse/Bash hook command wiring from the worktree's
        own checked-out settings.json -- the same file Claude Code's harness
        would resolve via $CLAUDE_PROJECT_DIR for a session rooted there."""
        settings_path = worktree_path / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        for entry in settings["hooks"]["PreToolUse"]:
            if entry.get("matcher") == "Bash":
                for h in entry["hooks"]:
                    if "validate_no_uv_sync_in_worktree.py" in h["command"]:
                        return h["command"]
        raise AssertionError(
            f"validate_no_uv_sync_in_worktree.py not wired into PreToolUse/Bash "
            f"in {settings_path} -- guard wiring missing from this worktree's checkout"
        )

    @staticmethod
    def _run_hook_command(
        command: str, worktree_path: Path, payload: dict
    ) -> subprocess.CompletedProcess:
        """Invoke the resolved hook command as a shell command, the same way
        Claude Code's hook runner dispatches a "type": "command" entry:
        $CLAUDE_PROJECT_DIR expanded by the shell, JSON payload fed on
        stdin, subprocess OS cwd set to the session cwd."""
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(worktree_path)}
        return subprocess.run(
            command,
            shell=True,
            cwd=str(worktree_path),
            env=env,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_guard_fires_from_real_worktree_cwd(self, real_worktree):
        """The guard, wired via the worktree's own settings.json and
        dispatched via the real hook protocol, must BLOCK `uv sync` when the
        session cwd is inside the real worktree."""
        command = self._hook_command_for_bash(real_worktree)
        result = self._run_hook_command(
            command,
            real_worktree,
            {
                "tool_name": "Bash",
                "cwd": str(real_worktree),
                "tool_input": {"command": "uv sync"},
            },
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "block"
        assert "uv pip install" in out["reason"]

    def test_guard_allows_from_main_checkout_cwd(self, real_worktree):
        """Same real hook command (resolved from the worktree's own
        settings.json), but a session cwd in the MAIN checkout must NOT
        block -- proves the guard is worktree-scoped, not a blanket ban."""
        command = self._hook_command_for_bash(real_worktree)
        result = self._run_hook_command(
            command,
            real_worktree,
            {
                "tool_name": "Bash",
                "cwd": str(MAIN_REPO_ROOT),
                "tool_input": {"command": "uv sync"},
            },
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_guard_allows_benign_command_from_real_worktree_cwd(self, real_worktree):
        """Same real worktree, same real hook command -- a benign command
        must NOT block."""
        command = self._hook_command_for_bash(real_worktree)
        result = self._run_hook_command(
            command,
            real_worktree,
            {
                "tool_name": "Bash",
                "cwd": str(real_worktree),
                "tool_input": {"command": "uv pip install foo"},
            },
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""


class TestIsolatedWorktreeRelaxation:
    """Issue #2052: `uv sync` from a worktree with its own .venv is ALLOWED
    (warn-not-block); unprovisioned worktrees keep the block."""

    @staticmethod
    def _make_worktree(tmp_path, container: str, isolated: bool) -> Path:
        if container == ".worktrees":
            root = tmp_path / "repo" / ".worktrees" / "my-slug"
        else:
            root = tmp_path / "repo" / ".claude" / "worktrees" / "agent-x"
        root.mkdir(parents=True)
        if isolated:
            venv = root / ".venv"
            venv.mkdir()
            (venv / "pyvenv.cfg").write_text("home = /opt/python\n")
        return root

    def test_isolated_dot_worktrees_allows_uv_sync(self, tmp_path):
        mod = import_validator()
        root = self._make_worktree(tmp_path, ".worktrees", isolated=True)
        assert mod.find_violation("uv sync", str(root)) is None
        notice = mod.find_isolation_notice("uv sync", str(root))
        assert notice is not None
        assert ".venv" in notice

    def test_isolated_claude_worktrees_allows_uv_sync(self, tmp_path):
        mod = import_validator()
        root = self._make_worktree(tmp_path, ".claude/worktrees", isolated=True)
        assert mod.find_violation("uv sync --all-extras", str(root)) is None
        assert mod.find_isolation_notice("uv sync --all-extras", str(root)) is not None

    def test_unprovisioned_real_worktree_still_blocked(self, tmp_path):
        mod = import_validator()
        root = self._make_worktree(tmp_path, ".worktrees", isolated=False)
        reason = mod.find_violation("uv sync", str(root))
        assert reason is not None
        assert "uv venv .venv" in reason  # bootstrap instructions present
        assert mod.find_isolation_notice("uv sync", str(root)) is None

    def test_bare_venv_dir_without_pyvenv_cfg_still_blocked(self, tmp_path):
        """An empty .venv directory (no pyvenv.cfg) is not an environment."""
        mod = import_validator()
        root = self._make_worktree(tmp_path, ".worktrees", isolated=False)
        (root / ".venv").mkdir()
        assert mod.find_violation("uv sync", str(root)) is not None

    def test_subdir_of_isolated_worktree_allows_uv_sync(self, tmp_path):
        """cwd deeper inside the worktree resolves to the worktree ROOT."""
        mod = import_validator()
        root = self._make_worktree(tmp_path, ".worktrees", isolated=True)
        sub = root / "tests" / "unit"
        sub.mkdir(parents=True)
        assert mod.find_violation("uv sync", str(sub)) is None
        assert mod.find_isolation_notice("uv sync", str(sub)) is not None

    def test_repo_root_never_isolated(self, tmp_path):
        """Anti-criterion: a repo root with its own .venv is NOT a worktree
        path -- no notice, no violation (allowed for the pre-existing
        non-worktree reason), and the shared env keeps block protection
        for worktrees beneath it."""
        mod = import_validator()
        repo = tmp_path / "repo"
        (repo / ".venv").mkdir(parents=True)
        (repo / ".venv" / "pyvenv.cfg").write_text("home = /opt/python\n")
        assert mod.find_isolation_notice("uv sync", str(repo)) is None
        assert mod.find_violation("uv sync", str(repo)) is None

    def test_no_notice_for_non_uv_sync_commands(self, tmp_path):
        mod = import_validator()
        root = self._make_worktree(tmp_path, ".worktrees", isolated=True)
        assert mod.find_isolation_notice("uv pip install foo", str(root)) is None
        assert mod.find_isolation_notice("pytest -q", str(root)) is None

    def test_synthetic_nonexistent_worktree_path_blocked(self):
        """The historical synthetic-path cases: no .venv exists on disk, so
        the worktree is unprovisioned and the block stands."""
        mod = import_validator()
        assert mod.find_violation("uv sync", "/repo/.worktrees/my-slug") is not None

    def test_worktree_root_resolution(self):
        mod = import_validator()
        assert mod._worktree_root("/repo/.worktrees/slug/sub/dir") == Path("/repo/.worktrees/slug")
        assert mod._worktree_root("/repo/.claude/worktrees/agent-x/deep") == Path(
            "/repo/.claude/worktrees/agent-x"
        )
        assert mod._worktree_root("/repo/src/module") is None
        assert mod._worktree_root("/repo/.worktrees") is None
        assert mod._worktree_root("/repo/.claude/worktrees") is None

    def test_hook_protocol_emits_system_message_for_isolated(self, tmp_path):
        """End-to-end hook protocol: isolated worktree -> exit 0, stdout is a
        systemMessage JSON (no decision key), tool call proceeds."""
        root = self._make_worktree(tmp_path, ".worktrees", isolated=True)
        payload = {
            "tool_name": "Bash",
            "cwd": str(root),
            "tool_input": {"command": "uv sync"},
        }
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert "decision" not in out
        assert "isolated .venv" in out["systemMessage"]

    def test_cli_mode_isolated_exits_zero_with_stderr_notice(self, tmp_path):
        root = self._make_worktree(tmp_path, ".worktrees", isolated=True)
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH), "uv sync", str(root)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "isolated .venv" in result.stderr
