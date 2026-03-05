"""Unit tests for post_tool_use.py SDLC session state tracking."""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Hook scripts live in .claude/hooks/ — add that to sys.path so we can import utils
HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from post_tool_use import (  # noqa: E402, I001
    get_sdlc_state_path,
    is_code_file,
    load_sdlc_state,
    save_sdlc_state,
    update_sdlc_state_for_bash,
    update_sdlc_state_for_file_write,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_session(tmp_path):
    """Provide a temporary session ID (tmp_path cleanup is automatic)."""
    session_id = f"test-sdlc-{int(time.time() * 1000)}"
    yield session_id, tmp_path


@pytest.fixture(autouse=True)
def patch_project_dir(tmp_path):
    """Redirect data/sessions writes to a temp directory so tests are hermetic."""
    sessions_dir = tmp_path / "sessions"
    with patch("post_tool_use.get_data_sessions_dir", return_value=sessions_dir):
        yield sessions_dir


def _make_quality_state() -> dict:
    """Return a pre-existing code-session state dict."""
    return {
        "code_modified": True,
        "files": ["foo.py"],
        "quality_commands": {"pytest": False, "ruff": False, "ruff-format": False},
    }


# ---------------------------------------------------------------------------
# is_code_file
# ---------------------------------------------------------------------------


class TestIsCodeFile:
    @pytest.mark.parametrize(
        "path",
        [
            "foo/bar.py",
            "/abs/path/script.js",
            "src/app/component.ts",
            "tools/helper.py",
        ],
    )
    def test_returns_true_for_code_files(self, path):
        assert is_code_file(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            "config.json",
            "settings.yaml",
            "pyproject.toml",
            "script.sh",
            "notes.txt",
            "data.csv",
            "",
        ],
    )
    def test_returns_false_for_non_code_files(self, path):
        assert is_code_file(path) is False


# ---------------------------------------------------------------------------
# get_sdlc_state_path
# ---------------------------------------------------------------------------


class TestGetSdlcStatePath:
    def test_returns_path_under_data_sessions(self, patch_project_dir):
        sessions_dir = patch_project_dir
        path = get_sdlc_state_path("my-session-123")
        assert path == sessions_dir / "my-session-123" / "sdlc_state.json"

    def test_different_session_ids_give_different_paths(self, patch_project_dir):
        p1 = get_sdlc_state_path("session-a")
        p2 = get_sdlc_state_path("session-b")
        assert p1 != p2


# ---------------------------------------------------------------------------
# load_sdlc_state / save_sdlc_state
# ---------------------------------------------------------------------------


class TestLoadSaveSdlcState:
    def test_load_missing_file_returns_default(self, patch_project_dir):
        state = load_sdlc_state("nonexistent-session-xyz")
        assert state["code_modified"] is False
        assert state["files"] == []
        assert state["quality_commands"] == {
            "pytest": False,
            "ruff": False,
            "ruff-format": False,
        }

    def test_save_creates_parent_dirs(self, patch_project_dir):
        sessions_dir = patch_project_dir
        session_id = "new-session-abc"
        state = {
            "code_modified": True,
            "files": ["foo.py"],
            "quality_commands": {"pytest": True, "ruff": False, "ruff-format": False},
        }
        save_sdlc_state(session_id, state)
        expected = sessions_dir / session_id / "sdlc_state.json"
        assert expected.exists()

    def test_save_then_load_roundtrip(self, patch_project_dir):
        session_id = "roundtrip-session"
        original = {
            "code_modified": True,
            "files": ["agent/foo.py", "tools/bar.py"],
            "quality_commands": {"pytest": True, "ruff": True, "ruff-format": False},
        }
        save_sdlc_state(session_id, original)
        loaded = load_sdlc_state(session_id)
        assert loaded["code_modified"] is True
        assert loaded["files"] == ["agent/foo.py", "tools/bar.py"]
        assert loaded["quality_commands"]["pytest"] is True
        assert loaded["quality_commands"]["ruff"] is True
        assert loaded["quality_commands"]["ruff-format"] is False

    def test_save_overwrites_existing(self, patch_project_dir):
        session_id = "overwrite-session"
        save_sdlc_state(
            session_id,
            {
                "code_modified": False,
                "files": [],
                "quality_commands": {"pytest": False, "ruff": False, "ruff-format": False},
            },
        )
        save_sdlc_state(
            session_id,
            {
                "code_modified": True,
                "files": ["x.py"],
                "quality_commands": {"pytest": True, "ruff": False, "ruff-format": False},
            },
        )
        loaded = load_sdlc_state(session_id)
        assert loaded["code_modified"] is True
        assert loaded["files"] == ["x.py"]


# ---------------------------------------------------------------------------
# update_sdlc_state_for_file_write
# ---------------------------------------------------------------------------


class TestUpdateSdlcStateForFileWrite:
    def test_code_file_creates_state(self, patch_project_dir, tmp_session):
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "agent/foo.py"},
        }
        update_sdlc_state_for_file_write(hook_input)
        state = load_sdlc_state(session_id)
        assert state["code_modified"] is True
        assert "agent/foo.py" in state["files"]

    def test_non_code_file_does_not_create_state(self, patch_project_dir, tmp_session):
        sessions_dir = patch_project_dir
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "README.md"},
        }
        update_sdlc_state_for_file_write(hook_input)
        state_path = sessions_dir / session_id / "sdlc_state.json"
        assert not state_path.exists()

    def test_edit_tool_also_tracked(self, patch_project_dir, tmp_session):
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Edit",
            "tool_input": {"file_path": "tools/helper.ts"},
        }
        update_sdlc_state_for_file_write(hook_input)
        state = load_sdlc_state(session_id)
        assert state["code_modified"] is True
        assert "tools/helper.ts" in state["files"]

    def test_multiple_files_accumulated(self, patch_project_dir, tmp_session):
        session_id, _ = tmp_session
        for fp in ["a.py", "b.py", "c.js"]:
            hook_input = {
                "session_id": session_id,
                "tool_name": "Write",
                "tool_input": {"file_path": fp},
            }
            update_sdlc_state_for_file_write(hook_input)
        state = load_sdlc_state(session_id)
        assert set(state["files"]) == {"a.py", "b.py", "c.js"}

    def test_duplicate_file_not_double_added(self, patch_project_dir, tmp_session):
        session_id, _ = tmp_session
        for _ in range(3):
            hook_input = {
                "session_id": session_id,
                "tool_name": "Write",
                "tool_input": {"file_path": "foo.py"},
            }
            update_sdlc_state_for_file_write(hook_input)
        state = load_sdlc_state(session_id)
        assert state["files"].count("foo.py") == 1

    def test_non_write_edit_tool_ignored(self, patch_project_dir, tmp_session):
        sessions_dir = patch_project_dir
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Read",
            "tool_input": {"file_path": "foo.py"},
        }
        update_sdlc_state_for_file_write(hook_input)
        state_path = sessions_dir / session_id / "sdlc_state.json"
        assert not state_path.exists()


# ---------------------------------------------------------------------------
# update_sdlc_state_for_bash
# ---------------------------------------------------------------------------


class TestUpdateSdlcStateForBash:
    @pytest.mark.parametrize(
        "command,expected_key",
        [
            ("pytest tests/", "pytest"),
            ("pytest tests/ -v -x", "pytest"),
            ("ruff check .", "ruff"),
            ("ruff format --check .", "ruff-format"),
            ("ruff format .", "ruff-format"),
        ],
    )
    def test_quality_command_updates_state(
        self, patch_project_dir, tmp_session, command, expected_key
    ):
        session_id, _ = tmp_session
        # Pre-create state so bash update has something to write into
        save_sdlc_state(session_id, _make_quality_state())
        hook_input = {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        update_sdlc_state_for_bash(hook_input)
        state = load_sdlc_state(session_id)
        assert state["quality_commands"][expected_key] is True

    def test_non_quality_bash_ignored_when_no_state(self, patch_project_dir, tmp_session):
        sessions_dir = patch_project_dir
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        }
        update_sdlc_state_for_bash(hook_input)
        state_path = sessions_dir / session_id / "sdlc_state.json"
        assert not state_path.exists()

    def test_quality_command_without_pre_existing_state_is_no_op(
        self, patch_project_dir, tmp_session
    ):
        """If no sdlc_state.json exists (non-code session), bash quality cmd does nothing."""
        sessions_dir = patch_project_dir
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
        }
        update_sdlc_state_for_bash(hook_input)
        state_path = sessions_dir / session_id / "sdlc_state.json"
        assert not state_path.exists()

    def test_bash_non_tool_name_ignored(self, patch_project_dir, tmp_session):
        sessions_dir = patch_project_dir
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Grep",
            "tool_input": {"command": "pytest tests/"},
        }
        update_sdlc_state_for_bash(hook_input)
        state_path = sessions_dir / session_id / "sdlc_state.json"
        assert not state_path.exists()


# ---------------------------------------------------------------------------
# Merge detection: gh pr merge resets code_modified
# ---------------------------------------------------------------------------


class TestMergeDetection:
    @pytest.mark.parametrize(
        "command",
        [
            "gh pr merge --squash --delete-branch",
            "gh pr merge 42 --squash",
            "gh pr merge",
            "gh pr merge --merge",
            "gh pr merge --rebase",
        ],
    )
    def test_gh_pr_merge_resets_code_modified(self, patch_project_dir, tmp_session, command):
        """gh pr merge commands should reset code_modified to false."""
        session_id, _ = tmp_session
        save_sdlc_state(session_id, _make_quality_state())
        hook_input = {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        update_sdlc_state_for_bash(hook_input)
        state = load_sdlc_state(session_id)
        assert state["code_modified"] is False

    def test_non_merge_command_preserves_code_modified(self, patch_project_dir, tmp_session):
        """Non-merge bash commands should not reset code_modified."""
        session_id, _ = tmp_session
        save_sdlc_state(session_id, _make_quality_state())
        hook_input = {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr view 42"},
        }
        update_sdlc_state_for_bash(hook_input)
        state = load_sdlc_state(session_id)
        assert state["code_modified"] is True

    def test_merge_without_existing_state_is_noop(self, patch_project_dir, tmp_session):
        """gh pr merge without pre-existing state file does nothing."""
        sessions_dir = patch_project_dir
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge --squash"},
        }
        update_sdlc_state_for_bash(hook_input)
        state_path = sessions_dir / session_id / "sdlc_state.json"
        assert not state_path.exists()


# ---------------------------------------------------------------------------
# Branch recording: modified_on_branch set on first code write
# ---------------------------------------------------------------------------


class TestBranchRecording:
    def test_branch_recorded_on_first_code_write(self, patch_project_dir, tmp_session):
        """When code is first modified, modified_on_branch should be recorded."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "agent/foo.py"},
        }
        with patch("subprocess.run") as mock_run:
            mock_result = type("Result", (), {"stdout": "session/my-feature\n"})()
            mock_run.return_value = mock_result
            update_sdlc_state_for_file_write(hook_input)
        state = load_sdlc_state(session_id)
        assert state["modified_on_branch"] == "session/my-feature"

    def test_branch_not_overwritten_on_subsequent_writes(self, patch_project_dir, tmp_session):
        """modified_on_branch should only be set once (first write wins)."""
        session_id, _ = tmp_session
        # First write on session/first-branch
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "agent/foo.py"},
        }
        with patch("subprocess.run") as mock_run:
            mock_result = type("Result", (), {"stdout": "session/first-branch\n"})()
            mock_run.return_value = mock_result
            update_sdlc_state_for_file_write(hook_input)

        # Second write (branch changed to main, but should not overwrite)
        hook_input2 = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "agent/bar.py"},
        }
        with patch("subprocess.run") as mock_run:
            mock_result = type("Result", (), {"stdout": "main\n"})()
            mock_run.return_value = mock_result
            update_sdlc_state_for_file_write(hook_input2)

        state = load_sdlc_state(session_id)
        assert state["modified_on_branch"] == "session/first-branch"

    def test_branch_recording_failure_does_not_block_state_save(
        self, patch_project_dir, tmp_session
    ):
        """If git rev-parse fails, state should still be saved with code_modified=True."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "agent/foo.py"},
        }
        with patch("subprocess.run", side_effect=Exception("git not found")):
            update_sdlc_state_for_file_write(hook_input)
        state = load_sdlc_state(session_id)
        assert state["code_modified"] is True
        # modified_on_branch should not be present since git failed
        assert "modified_on_branch" not in state
