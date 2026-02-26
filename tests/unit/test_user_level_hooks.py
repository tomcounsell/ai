"""Tests for user-level SDLC hook scripts in .claude/hooks/user_level/.

These hooks are standalone scripts with no project imports. They detect SDLC
context (session/ branch or AgentSession model) and enforce rules only in that
context. Outside SDLC, they silently no-op (exit 0).

Tests run each hook as a subprocess with controlled stdin to match the Claude
Code hook protocol.
"""

import json
import subprocess
import sys
from pathlib import Path

# Paths to the user-level hook scripts
HOOKS_DIR = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "user_level"
VALIDATE_COMMIT = HOOKS_DIR / "validate_commit_message.py"
SDLC_REMINDER = HOOKS_DIR / "sdlc_reminder.py"
VALIDATE_ON_STOP = HOOKS_DIR / "validate_sdlc_on_stop.py"


def run_hook(
    hook_path: Path, hook_input: dict, env_overrides: dict | None = None
) -> tuple[int, dict | None, str]:
    """Run a hook script with the given input dict via stdin.

    Returns: (exit_code, stdout_json_or_none, stderr)
    """
    import os

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    stdout_json = None
    if result.stdout.strip():
        try:
            stdout_json = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            pass
    return result.returncode, stdout_json, result.stderr


# ===========================================================================
# validate_commit_message.py tests
# ===========================================================================


class TestUserLevelCommitHookFastPath:
    """Non-commit commands must pass through immediately."""

    def test_non_bash_tool_passes(self):
        inp = {"tool_name": "Read", "tool_input": {"file_path": "/tmp/foo.py"}}
        code, out, _ = run_hook(VALIDATE_COMMIT, inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"

    def test_git_status_passes(self):
        inp = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
        code, out, _ = run_hook(VALIDATE_COMMIT, inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"

    def test_empty_stdin_passes(self):
        result = subprocess.run(
            [sys.executable, str(VALIDATE_COMMIT)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_invalid_json_passes(self):
        result = subprocess.run(
            [sys.executable, str(VALIDATE_COMMIT)],
            input="not valid json",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_non_commit_bash_passes(self):
        inp = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        code, out, _ = run_hook(VALIDATE_COMMIT, inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"


class TestUserLevelCommitHookNotOnMain:
    """When not on main branch, commits should always be allowed."""

    def test_commit_on_feature_branch_passes(self):
        """Since we're running tests on session/sdlc_user_hooks (or main),
        this test verifies the hook at least doesn't crash on a valid commit."""
        inp = {
            "tool_name": "Bash",
            "tool_input": {"command": 'git commit -m "test commit"'},
        }
        code, out, _ = run_hook(VALIDATE_COMMIT, inp)
        # Should exit 0 regardless (either not on main, or not in SDLC context)
        assert code == 0


class TestUserLevelCommitHookStructure:
    """Verify the hook script is properly structured."""

    def test_script_is_executable(self):
        import os

        assert os.access(VALIDATE_COMMIT, os.X_OK)

    def test_script_has_no_project_imports(self):
        """Verify the hook does not import from utils.constants or other project modules."""
        content = VALIDATE_COMMIT.read_text()
        assert "from utils.constants" not in content
        assert "from utils import" not in content
        assert "CLAUDE_PROJECT_DIR" not in content

    def test_has_is_sdlc_context(self):
        """Must contain the is_sdlc_context() function."""
        content = VALIDATE_COMMIT.read_text()
        assert "def is_sdlc_context" in content

    def test_has_try_except_wrapper(self):
        """Main function must be wrapped in try/except for fail-open behavior."""
        content = VALIDATE_COMMIT.read_text()
        assert "except Exception" in content


# ===========================================================================
# sdlc_reminder.py tests
# ===========================================================================


class TestUserLevelReminderHookFastPath:
    """Non-code file writes should not trigger a reminder."""

    def test_non_write_tool_passes_silently(self):
        inp = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}}
        code, out, stderr = run_hook(SDLC_REMINDER, inp)
        assert code == 0

    def test_non_code_file_passes_silently(self):
        inp = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/readme.md"},
            "session_id": "test-session-123",
        }
        code, out, stderr = run_hook(SDLC_REMINDER, inp)
        assert code == 0

    def test_empty_stdin_passes(self):
        result = subprocess.run(
            [sys.executable, str(SDLC_REMINDER)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0


class TestUserLevelReminderHookStructure:
    """Verify the hook script is properly structured."""

    def test_script_is_executable(self):
        import os

        assert os.access(SDLC_REMINDER, os.X_OK)

    def test_script_has_no_project_imports(self):
        content = SDLC_REMINDER.read_text()
        assert "from utils.constants" not in content
        assert "from utils import" not in content
        assert "CLAUDE_PROJECT_DIR" not in content

    def test_has_is_sdlc_context(self):
        content = SDLC_REMINDER.read_text()
        assert "def is_sdlc_context" in content

    def test_has_try_except_wrapper(self):
        content = SDLC_REMINDER.read_text()
        assert "except Exception" in content

    def test_uses_tmp_for_state(self):
        """State tracking must use /tmp, not project directories."""
        content = SDLC_REMINDER.read_text()
        assert "/tmp" in content
        assert "get_data_sessions_dir" not in content


# ===========================================================================
# validate_sdlc_on_stop.py tests
# ===========================================================================


class TestUserLevelStopHookFastPath:
    """Non-SDLC sessions should pass through immediately.

    Note: These tests may run inside a worktree (on a session/ branch) during
    builds, which means is_sdlc_context() returns True. The hook behavior
    depends on whether we're in SDLC context, so we test with SKIP_SDLC=1 to
    ensure the hook doesn't block in test environments, and separately test
    structure/behavior properties.
    """

    def test_empty_stdin_does_not_crash(self):
        """Hook should not crash on empty stdin (exit 0 or 2, never other codes)."""
        result = subprocess.run(
            [sys.executable, str(VALIDATE_ON_STOP)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Exit code 0 (pass) or 2 (quality gate block) — both are valid behaviors
        # depending on whether we're in SDLC context. Any other exit code is a crash.
        assert result.returncode in (0, 2)

    def test_invalid_json_does_not_crash(self):
        """Hook should not crash on invalid JSON (exit 0 or 2, never other codes)."""
        result = subprocess.run(
            [sys.executable, str(VALIDATE_ON_STOP)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode in (0, 2)

    def test_empty_stdin_passes_with_skip_sdlc(self):
        """With SKIP_SDLC=1, hook should always exit 0 regardless of context."""
        import os

        env = os.environ.copy()
        env["SKIP_SDLC"] = "1"
        result = subprocess.run(
            [sys.executable, str(VALIDATE_ON_STOP)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0


class TestUserLevelStopHookStructure:
    """Verify the hook script is properly structured."""

    def test_script_is_executable(self):
        import os

        assert os.access(VALIDATE_ON_STOP, os.X_OK)

    def test_script_has_no_project_imports(self):
        content = VALIDATE_ON_STOP.read_text()
        assert "from utils.constants" not in content
        assert "from utils import" not in content
        assert "CLAUDE_PROJECT_DIR" not in content

    def test_has_is_sdlc_context(self):
        content = VALIDATE_ON_STOP.read_text()
        assert "def is_sdlc_context" in content

    def test_has_try_except_wrapper(self):
        content = VALIDATE_ON_STOP.read_text()
        assert "except Exception" in content

    def test_has_skip_sdlc_escape_hatch(self):
        """Must support SKIP_SDLC=1 environment variable."""
        content = VALIDATE_ON_STOP.read_text()
        assert "SKIP_SDLC" in content

    def test_has_quality_gate_checks(self):
        """Must check for pytest, ruff, and black."""
        content = VALIDATE_ON_STOP.read_text()
        assert "pytest" in content
        assert "ruff" in content
        assert "black" in content


class TestUserLevelStopHookSkipSdlc:
    """SKIP_SDLC=1 should bypass enforcement."""

    def test_skip_sdlc_exits_zero(self):
        inp = {"session_id": "test-session"}
        code, out, stderr = run_hook(
            VALIDATE_ON_STOP, inp, env_overrides={"SKIP_SDLC": "1"}
        )
        assert code == 0
