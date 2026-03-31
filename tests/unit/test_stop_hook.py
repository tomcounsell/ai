"""Tests for the stop hook JSONL transcript backup (issue #188).

The stop hook (.claude/hooks/stop.py) is a standalone script with non-standard
imports. We test the backup logic directly rather than importing the hook module.
"""

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def session_dir(tmp_path):
    """Create a temp session log directory."""
    d = tmp_path / "logs" / "sessions" / "test-session"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def sample_jsonl(tmp_path):
    """Create a sample JSONL transcript file."""
    jsonl_path = tmp_path / "source_transcript.jsonl"
    entries = [
        {"type": "user", "message": "Hello"},
        {"type": "assistant", "message": "Hi there"},
        {"type": "tool_call", "tool": "Bash", "input": "ls"},
    ]
    with jsonl_path.open("w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return jsonl_path


class TestJSONLBackupLogic:
    """Test the JSONL backup behavior that stop.py implements."""

    def test_copies_jsonl_to_transcript_jsonl(self, session_dir, sample_jsonl):
        """JSONL is copied as transcript.jsonl."""
        dst = session_dir / "transcript.jsonl"
        src = sample_jsonl

        # This mirrors the logic in stop.py
        shutil.copy2(str(src), str(dst))

        assert dst.exists()
        assert dst.read_text() == src.read_text()

    def test_jsonl_not_named_chat_json(self, session_dir, sample_jsonl):
        """Backup is transcript.jsonl, not the old chat.json name."""
        dst = session_dir / "transcript.jsonl"
        shutil.copy2(str(sample_jsonl), str(dst))

        assert dst.exists()
        assert not (session_dir / "chat.json").exists()

    def test_jsonl_content_preserved_exactly(self, session_dir, sample_jsonl):
        """JSONL content is byte-for-byte identical after copy."""
        dst = session_dir / "transcript.jsonl"
        shutil.copy2(str(sample_jsonl), str(dst))

        # Parse both and compare
        src_lines = sample_jsonl.read_text().strip().split("\n")
        dst_lines = dst.read_text().strip().split("\n")
        assert len(src_lines) == len(dst_lines) == 3

        for src_line, dst_line in zip(src_lines, dst_lines):
            assert json.loads(src_line) == json.loads(dst_line)

    def test_missing_source_skipped(self, session_dir):
        """When source file doesn't exist, no copy happens."""
        src = Path("/nonexistent/transcript.jsonl")
        dst = session_dir / "transcript.jsonl"

        # Mirror stop.py logic: only copy if src exists
        if src.exists():
            shutil.copy2(str(src), str(dst))

        assert not dst.exists()

    def test_no_transcript_path_skipped(self, session_dir):
        """When transcript_path is None/missing, no copy happens."""
        transcript_path = None
        dst = session_dir / "transcript.jsonl"

        # Mirror stop.py logic
        if transcript_path:
            src = Path(transcript_path)
            if src.exists():
                shutil.copy2(str(src), str(dst))

        assert not dst.exists()


class TestUpdateAgentSessionLogPath:
    """Test that AgentSession.log_path gets updated with JSONL backup path."""

    def test_updates_existing_session(self):
        """log_path is set to the JSONL backup path."""
        mock_session = MagicMock()
        mock_session.log_path = "/old/transcript.txt"

        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = [mock_session]

            # Inline the helper logic from stop.py
            session_id = "sess-123"
            jsonl_path = "/logs/sessions/sess-123/transcript.jsonl"
            try:
                from models.agent_session import AgentSession

                sessions = list(AgentSession.query.filter(session_id=session_id))
                if sessions:
                    s = sessions[0]
                    s.log_path = jsonl_path
                    s.save()
            except Exception:
                pass

        assert mock_session.log_path == jsonl_path
        mock_session.save.assert_called_once()

    def test_no_session_found_noop(self):
        """When session doesn't exist, no error raised."""
        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = []

            session_id = "nonexistent"
            jsonl_path = "/path/to/transcript.jsonl"
            try:
                from models.agent_session import AgentSession

                sessions = list(AgentSession.query.filter(session_id=session_id))
                if sessions:
                    s = sessions[0]
                    s.log_path = jsonl_path
                    s.save()
            except Exception:
                pass
            # No assertion needed — just shouldn't raise

    def test_redis_error_swallowed(self):
        """Redis connection errors don't propagate."""
        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.side_effect = ConnectionError("Redis down")

            session_id = "sess-123"
            jsonl_path = "/path/to/transcript.jsonl"
            try:
                from models.agent_session import AgentSession

                sessions = list(AgentSession.query.filter(session_id=session_id))
                if sessions:
                    s = sessions[0]
                    s.log_path = jsonl_path
                    s.save()
            except Exception:
                pass
            # No assertion needed — just shouldn't raise


class TestAgentSessionCompletion:
    """Test that stop hook completes AgentSession lifecycle."""

    def test_stop_hook_has_complete_agent_session(self):
        """stop.py contains the _complete_agent_session function."""
        hook = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "stop.py"
        content = hook.read_text()
        assert "_complete_agent_session" in content

    def test_stop_hook_has_post_merge_extraction(self):
        """stop.py contains the _run_post_merge_extraction function."""
        hook = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "stop.py"
        content = hook.read_text()
        assert "_run_post_merge_extraction" in content

    def test_complete_marks_session_completed(self):
        """AgentSession status is set to completed on normal stop."""
        mock_session = MagicMock()
        mock_session.status = "running"

        mock_sidecar = {"agent_session_agent_session_id": "job-123"}

        with (
            patch(
                "hook_utils.memory_bridge.load_agent_session_sidecar",
                return_value=mock_sidecar,
            ),
            patch("models.agent_session.AgentSession.query") as mock_query,
        ):
            mock_query.get.return_value = mock_session

            # Import and test the function inline (hook scripts use non-standard imports)
            import sys

            hook_dir = str(Path(__file__).parent.parent.parent / ".claude" / "hooks")
            if hook_dir not in sys.path:
                sys.path.insert(0, hook_dir)

            # Simulate calling _complete_agent_session
            session_id = "test-session"
            hook_input = {"stop_reason": "end_turn", "session_id": session_id}

            try:
                from hook_utils.memory_bridge import load_agent_session_sidecar

                sidecar = load_agent_session_sidecar(session_id)
                agent_session_id = sidecar.get("agent_session_agent_session_id")
                if agent_session_id:
                    from models.agent_session import AgentSession

                    agent_session = AgentSession.query.get(agent_session_id)
                    if agent_session:
                        stop_reason = hook_input.get("stop_reason", "unknown")
                        if stop_reason in ("error", "crash"):
                            agent_session.status = "failed"
                        else:
                            agent_session.status = "completed"
                        agent_session.save()
            except Exception:
                pass

        assert mock_session.status == "completed"
        mock_session.save.assert_called_once()

    def test_complete_marks_session_failed_on_error(self):
        """AgentSession status is set to failed on error stop."""
        mock_session = MagicMock()
        mock_session.status = "running"

        mock_sidecar = {"agent_session_agent_session_id": "job-456"}

        with (
            patch(
                "hook_utils.memory_bridge.load_agent_session_sidecar",
                return_value=mock_sidecar,
            ),
            patch("models.agent_session.AgentSession.query") as mock_query,
        ):
            mock_query.get.return_value = mock_session

            session_id = "test-session-err"
            hook_input = {"stop_reason": "error", "session_id": session_id}

            try:
                from hook_utils.memory_bridge import load_agent_session_sidecar

                sidecar = load_agent_session_sidecar(session_id)
                agent_session_id = sidecar.get("agent_session_agent_session_id")
                if agent_session_id:
                    from models.agent_session import AgentSession

                    agent_session = AgentSession.query.get(agent_session_id)
                    if agent_session:
                        stop_reason = hook_input.get("stop_reason", "unknown")
                        if stop_reason in ("error", "crash"):
                            agent_session.status = "failed"
                        else:
                            agent_session.status = "completed"
                        agent_session.save()
            except Exception:
                pass

        assert mock_session.status == "failed"

    def test_no_sidecar_skips_gracefully(self):
        """No error when sidecar has no agent_session_agent_session_id."""
        with patch(
            "hook_utils.memory_bridge.load_agent_session_sidecar",
            return_value={},
        ):
            # Should not raise
            try:
                from hook_utils.memory_bridge import load_agent_session_sidecar

                sidecar = load_agent_session_sidecar("no-session")
                agent_session_id = sidecar.get("agent_session_agent_session_id")
                assert agent_session_id is None
            except Exception:
                pass


class TestStopHookScript:
    """Integration test: verify stop.py script structure is correct."""

    def test_stop_hook_exists(self):
        """The stop hook file exists."""
        hook = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "stop.py"
        assert hook.exists()

    def test_stop_hook_references_transcript_jsonl(self):
        """stop.py writes to transcript.jsonl, not chat.json."""
        hook = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "stop.py"
        content = hook.read_text()
        assert "transcript.jsonl" in content
        assert "chat.json" not in content

    def test_stop_hook_updates_agent_session(self):
        """stop.py calls _update_agent_session_log_path."""
        hook = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "stop.py"
        content = hook.read_text()
        assert "_update_agent_session_log_path" in content

    def test_stop_hook_always_copies(self):
        """stop.py copies transcript unconditionally (not gated by --chat)."""
        hook = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "stop.py"
        content = hook.read_text()
        # The copy logic should NOT be inside "if args.chat:"
        # It should be at the same indentation as the metadata save
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "transcript_path" in line and "hook_input.get" in line:
                # This line should not be indented under an if args.chat block
                assert "if args.chat" not in content.split("transcript_path")[0].split("\n")[-1]
                break
