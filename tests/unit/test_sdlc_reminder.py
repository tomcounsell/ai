"""Unit tests for sdlc_reminder.py PostToolUse hook."""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Hook scripts live in .claude/hooks/ — add that to sys.path so we can import
HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from sdlc_reminder import (  # noqa: E402, I001
    SDLC_REMINDER_MESSAGE,
    emit_reminder_if_needed,
    get_reminder_state_path,
    has_reminder_been_sent,
    mark_reminder_sent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_session(tmp_path):
    """Provide a temporary session ID."""
    session_id = f"test-reminder-{int(time.time() * 1000)}"
    yield session_id, tmp_path


@pytest.fixture(autouse=True)
def patch_sessions_dir(tmp_path):
    """Redirect data/sessions writes to a temp directory so tests are hermetic."""
    sessions_dir = tmp_path / "sessions"
    with patch("sdlc_reminder.get_data_sessions_dir", return_value=sessions_dir):
        yield sessions_dir


# ---------------------------------------------------------------------------
# get_reminder_state_path
# ---------------------------------------------------------------------------


class TestGetReminderStatePath:
    def test_returns_path_under_sessions_dir(self, patch_sessions_dir):
        sessions_dir = patch_sessions_dir
        path = get_reminder_state_path("my-session-abc")
        assert path == sessions_dir / "my-session-abc" / "sdlc_state.json"


# ---------------------------------------------------------------------------
# has_reminder_been_sent / mark_reminder_sent
# ---------------------------------------------------------------------------


class TestReminderState:
    def test_no_state_file_returns_false(self, patch_sessions_dir, tmp_session):
        session_id, _ = tmp_session
        assert has_reminder_been_sent(session_id) is False

    def test_state_without_reminder_sent_key_returns_false(self, patch_sessions_dir, tmp_session):
        import json

        sessions_dir = patch_sessions_dir
        session_id, _ = tmp_session
        state_path = sessions_dir / session_id / "sdlc_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w") as f:
            json.dump({"code_modified": True, "files": ["foo.py"]}, f)
        assert has_reminder_been_sent(session_id) is False

    def test_mark_reminder_sent_creates_state_with_flag(self, patch_sessions_dir, tmp_session):
        import json

        sessions_dir = patch_sessions_dir
        session_id, _ = tmp_session
        mark_reminder_sent(session_id)
        state_path = sessions_dir / session_id / "sdlc_state.json"
        assert state_path.exists()
        with open(state_path) as f:
            data = json.load(f)
        assert data["reminder_sent"] is True

    def test_mark_reminder_sent_preserves_existing_state(self, patch_sessions_dir, tmp_session):
        import json

        sessions_dir = patch_sessions_dir
        session_id, _ = tmp_session
        # Write pre-existing state
        state_path = sessions_dir / session_id / "sdlc_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            "code_modified": True,
            "files": ["a.py"],
            "quality_commands": {"pytest": True, "ruff": False, "black": False},
        }
        with open(state_path, "w") as f:
            json.dump(existing, f)
        mark_reminder_sent(session_id)
        with open(state_path) as f:
            data = json.load(f)
        assert data["reminder_sent"] is True
        assert data["code_modified"] is True
        assert data["files"] == ["a.py"]
        assert data["quality_commands"]["pytest"] is True

    def test_has_reminder_been_sent_returns_true_after_mark(self, patch_sessions_dir, tmp_session):
        session_id, _ = tmp_session
        assert has_reminder_been_sent(session_id) is False
        mark_reminder_sent(session_id)
        assert has_reminder_been_sent(session_id) is True


# ---------------------------------------------------------------------------
# emit_reminder_if_needed
# ---------------------------------------------------------------------------


class TestEmitReminderIfNeeded:
    def test_code_file_write_no_prior_state_emits_reminder(
        self, patch_sessions_dir, tmp_session, capsys
    ):
        """First code file write in session: reminder should be printed."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "agent/foo.py"},
        }
        emit_reminder_if_needed(hook_input)
        out = capsys.readouterr().out
        assert SDLC_REMINDER_MESSAGE in out

    def test_code_file_write_marks_reminder_sent(self, patch_sessions_dir, tmp_session, capsys):
        """After reminder is emitted, reminder_sent should be True in state."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "tools/helper.py"},
        }
        emit_reminder_if_needed(hook_input)
        assert has_reminder_been_sent(session_id) is True

    def test_code_file_write_reminder_already_sent_no_output(
        self, patch_sessions_dir, tmp_session, capsys
    ):
        """When reminder_sent is already True, no output should be produced."""
        session_id, _ = tmp_session
        mark_reminder_sent(session_id)
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "agent/bar.py"},
        }
        emit_reminder_if_needed(hook_input)
        out = capsys.readouterr().out
        assert out == ""

    def test_non_code_file_write_no_output(self, patch_sessions_dir, tmp_session, capsys):
        """Writing a .md file should produce no output at all."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "docs/README.md"},
        }
        emit_reminder_if_needed(hook_input)
        out = capsys.readouterr().out
        assert out == ""

    def test_non_code_file_does_not_set_reminder_sent(
        self, patch_sessions_dir, tmp_session, capsys
    ):
        """Non-code file writes must not pollute the reminder state."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "config.json"},
        }
        emit_reminder_if_needed(hook_input)
        assert has_reminder_been_sent(session_id) is False

    def test_edit_tool_code_file_emits_reminder(self, patch_sessions_dir, tmp_session, capsys):
        """Edit tool on a code file should also trigger the reminder."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/app.ts"},
        }
        emit_reminder_if_needed(hook_input)
        out = capsys.readouterr().out
        assert SDLC_REMINDER_MESSAGE in out

    @pytest.mark.parametrize("ext", [".py", ".js", ".ts"])
    def test_all_code_extensions_trigger_reminder(
        self, patch_sessions_dir, tmp_session, capsys, ext
    ):
        """All three code extensions should trigger the reminder."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": f"{session_id}-{ext}",
            "tool_name": "Write",
            "tool_input": {"file_path": f"module/code{ext}"},
        }
        emit_reminder_if_needed(hook_input)
        out = capsys.readouterr().out
        assert SDLC_REMINDER_MESSAGE in out

    @pytest.mark.parametrize("ext", [".md", ".json", ".yaml", ".toml", ".sh", ".txt"])
    def test_non_code_extensions_no_output(self, patch_sessions_dir, tmp_session, capsys, ext):
        """Non-code file extensions must not trigger the reminder."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": f"{session_id}-{ext}",
            "tool_name": "Write",
            "tool_input": {"file_path": f"stuff/file{ext}"},
        }
        emit_reminder_if_needed(hook_input)
        out = capsys.readouterr().out
        assert out == ""

    def test_non_write_edit_tool_is_ignored(self, patch_sessions_dir, tmp_session, capsys):
        """A Bash tool call on a .py path should not trigger the reminder."""
        session_id, _ = tmp_session
        hook_input = {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "python foo.py"},
        }
        emit_reminder_if_needed(hook_input)
        out = capsys.readouterr().out
        assert out == ""

    def test_reminder_only_emitted_once_across_multiple_writes(
        self, patch_sessions_dir, tmp_session, capsys
    ):
        """Calling emit multiple times should only produce output the first time."""
        session_id, _ = tmp_session
        for fp in ["a.py", "b.js", "c.ts"]:
            hook_input = {
                "session_id": session_id,
                "tool_name": "Write",
                "tool_input": {"file_path": fp},
            }
            emit_reminder_if_needed(hook_input)
        captured = capsys.readouterr()
        # Reminder message should appear exactly once
        assert captured.out.count(SDLC_REMINDER_MESSAGE) == 1
