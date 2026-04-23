"""Tests for agent/hooks/pre_compact.py.

Issue #1127. Covers the backup snapshot, the 5-minute cooldown, the 3-backup
retention policy, exception swallowing, and the AgentSession lookup via
``claude_session_uuid`` (B1 fix — NOT via ``session_id``).
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.hooks.pre_compact import (
    BACKUP_RETENTION_COUNT,
    COMPACTION_COOLDOWN_SECONDS,
    pre_compact_hook,
)
from models.agent_session import AgentSession


def _make_transcript(tmp_path: Path, lines: int = 5) -> Path:
    """Create a fake JSONL transcript with N lines."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(f'{{"type":"user","content":"msg{i}"}}' for i in range(lines)) + "\n"
    )
    return transcript


def _make_session(claude_uuid: str) -> AgentSession:
    """Create a minimal AgentSession with a known claude_session_uuid."""
    session = AgentSession(
        session_id=f"test-{claude_uuid[:8]}",
        session_type="dev",
        project_key="test-compaction",
        claude_session_uuid=claude_uuid,
    )
    session.save()
    return session


def _reload_by_claude_uuid(claude_uuid: str) -> AgentSession | None:
    """Reload a session from Redis by its claude_session_uuid.

    Prefer this over ``AgentSession.get_by_id(...)`` in tests because under
    pytest-xdist and cross-suite ordering the ``id`` index can accumulate
    stale pointers (the autouse ``redis_test_db`` ``flushdb`` does not always
    interact cleanly with Popoto's in-process index caches). The
    ``claude_session_uuid`` path is the same indexed lookup the hook itself
    uses, so it avoids that hazard.
    """
    rows = list(AgentSession.query.filter(claude_session_uuid=claude_uuid))
    return rows[0] if rows else None


class TestBackupSnapshot:
    async def test_happy_path_creates_backup(self, tmp_path):
        """Hook snapshots the transcript to backups/{uuid}-{ts}.jsonl.bak."""
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        _make_session(claude_uuid)

        result = await pre_compact_hook(
            {
                "session_id": claude_uuid,
                "transcript_path": str(transcript),
                "trigger": "auto",
            },
            None,
            None,
        )
        assert result == {}

        backup_dir = tmp_path / "backups"
        assert backup_dir.is_dir()
        backups = list(backup_dir.glob(f"{claude_uuid}-*.jsonl.bak"))
        assert len(backups) == 1
        assert backups[0].read_text() == transcript.read_text()

        # AgentSession cooldown state written
        refreshed = _reload_by_claude_uuid(claude_uuid)
        assert refreshed is not None
        assert refreshed.last_compaction_ts is not None
        assert float(refreshed.last_compaction_ts) > 0
        assert refreshed.compaction_count == 1

    async def test_backup_filename_keyed_by_claude_uuid_not_session_id(self, tmp_path):
        """B1 regression guard — filename uses claude_session_uuid, not session_id."""
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        session = _make_session(claude_uuid)

        await pre_compact_hook(
            {"session_id": claude_uuid, "transcript_path": str(transcript)},
            None,
            None,
        )

        backup_dir = tmp_path / "backups"
        backups = list(backup_dir.glob("*.jsonl.bak"))
        assert len(backups) == 1
        # Backup must be keyed by the Claude UUID (hook's input session_id),
        # NOT by our bridge-side AgentSession.session_id.
        assert backups[0].name.startswith(claude_uuid)
        assert session.session_id not in backups[0].name


class TestMissingTranscript:
    async def test_file_not_found_is_silent_debug(self, tmp_path, caplog):
        """FileNotFoundError logs at debug level, not warning. C3 regression guard."""
        import logging as _logging

        claude_uuid = str(uuid.uuid4())
        # Non-existent path
        fake_path = tmp_path / "does-not-exist.jsonl"

        with caplog.at_level(_logging.DEBUG, logger="agent.hooks.pre_compact"):
            result = await pre_compact_hook(
                {"session_id": claude_uuid, "transcript_path": str(fake_path)},
                None,
                None,
            )
        assert result == {}

        # No warnings
        warnings = [r for r in caplog.records if r.levelno >= _logging.WARNING]
        assert not warnings, f"Expected no warnings, got: {[r.message for r in warnings]}"

        # At least one debug log for the missing transcript
        debug_msgs = [r.message for r in caplog.records if r.levelno == _logging.DEBUG]
        assert any("transcript missing" in m.lower() for m in debug_msgs), (
            f"Expected a debug log for missing transcript, got: {debug_msgs}"
        )

    async def test_empty_transcript_path_warning_no_op(self, tmp_path):
        """Empty transcript_path → warning log, no snapshot, no exception."""
        result = await pre_compact_hook(
            {"session_id": str(uuid.uuid4()), "transcript_path": ""},
            None,
            None,
        )
        assert result == {}
        # No backup created anywhere
        assert not any(tmp_path.glob("**/*.jsonl.bak"))

    async def test_missing_session_id_no_op(self):
        result = await pre_compact_hook(
            {"transcript_path": "/nonexistent"},
            None,
            None,
        )
        assert result == {}


class TestCooldown:
    async def test_second_fire_within_cooldown_skips(self, tmp_path):
        """Second PreCompact within 300s for same UUID is a no-op."""
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        _make_session(claude_uuid)

        # First fire
        await pre_compact_hook(
            {"session_id": claude_uuid, "transcript_path": str(transcript)},
            None,
            None,
        )
        first_backups = list((tmp_path / "backups").glob("*.jsonl.bak"))
        assert len(first_backups) == 1

        # Second fire moments later
        await pre_compact_hook(
            {"session_id": claude_uuid, "transcript_path": str(transcript)},
            None,
            None,
        )
        second_backups = list((tmp_path / "backups").glob("*.jsonl.bak"))
        # Still exactly 1 backup (no new snapshot)
        assert len(second_backups) == 1
        assert second_backups[0].name == first_backups[0].name

        # Session state: compaction_count stays at 1, compaction_skipped_count bumped
        refreshed = _reload_by_claude_uuid(claude_uuid)
        assert refreshed is not None
        assert refreshed.compaction_count == 1
        assert refreshed.compaction_skipped_count == 1

    async def test_after_cooldown_expires_snapshots_again(self, tmp_path):
        """Once cooldown expires, the hook snapshots again."""
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        _make_session(claude_uuid)

        # First fire
        await pre_compact_hook(
            {"session_id": claude_uuid, "transcript_path": str(transcript)},
            None,
            None,
        )
        # Simulate cooldown expiry by rewinding last_compaction_ts
        refreshed_list = list(AgentSession.query.filter(claude_session_uuid=claude_uuid))
        assert refreshed_list, f"Expected session for {claude_uuid}, got empty"
        refreshed = refreshed_list[0]
        refreshed.last_compaction_ts = time.time() - (COMPACTION_COOLDOWN_SECONDS + 10)
        refreshed.save(update_fields=["last_compaction_ts"])

        # Second fire — needs a new integer timestamp so the filename differs.
        # Sleep 1.1s to ensure int(time.time()) advances.
        time.sleep(1.1)
        await pre_compact_hook(
            {"session_id": claude_uuid, "transcript_path": str(transcript)},
            None,
            None,
        )
        backups = list((tmp_path / "backups").glob("*.jsonl.bak"))
        assert len(backups) == 2

        final_list = list(AgentSession.query.filter(claude_session_uuid=claude_uuid))
        assert final_list, f"Expected session for {claude_uuid}, got empty"
        final = final_list[0]
        assert final.compaction_count == 2


class TestAgentSessionLookup:
    async def test_lookup_uses_claude_session_uuid_not_session_id(self, tmp_path):
        """B1 regression guard — lookup filters on claude_session_uuid only.

        Create a session whose `session_id` equals a different UUID than
        `claude_session_uuid`. The hook's lookup MUST find the row via
        `claude_session_uuid`, not via `session_id`.
        """
        claude_uuid = str(uuid.uuid4())
        bridge_session_id = f"bridge-{uuid.uuid4().hex[:8]}"
        transcript = _make_transcript(tmp_path)

        session = AgentSession(
            session_id=bridge_session_id,
            session_type="dev",
            project_key="test-compaction-b1",
            claude_session_uuid=claude_uuid,
        )
        session.save()

        await pre_compact_hook(
            {"session_id": claude_uuid, "transcript_path": str(transcript)},
            None,
            None,
        )

        refreshed = _reload_by_claude_uuid(claude_uuid)
        assert refreshed is not None
        assert refreshed.last_compaction_ts is not None
        assert refreshed.compaction_count == 1

    async def test_no_matching_session_still_snapshots(self, tmp_path, caplog):
        """If no AgentSession row matches, snapshot still succeeds — info log only."""
        import logging as _logging

        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)

        with caplog.at_level(_logging.INFO, logger="agent.hooks.pre_compact"):
            await pre_compact_hook(
                {"session_id": claude_uuid, "transcript_path": str(transcript)},
                None,
                None,
            )

        backups = list((tmp_path / "backups").glob("*.jsonl.bak"))
        assert len(backups) == 1
        info_msgs = [r.message for r in caplog.records if r.levelno == _logging.INFO]
        assert any("no AgentSession row" in m for m in info_msgs)


class TestRetention:
    async def test_prunes_to_last_three(self, tmp_path):
        """After 4 compactions (with cooldown bypassed), exactly 3 backups remain."""
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        _make_session(claude_uuid)

        for i in range(4):
            # Bypass cooldown by rewinding last_compaction_ts each round
            if i > 0:
                refreshed = _reload_by_claude_uuid(claude_uuid)
                assert refreshed is not None
                refreshed.last_compaction_ts = time.time() - (COMPACTION_COOLDOWN_SECONDS + 10)
                refreshed.save(update_fields=["last_compaction_ts"])
            # Sleep to ensure integer timestamps differ
            if i > 0:
                time.sleep(1.1)
            await pre_compact_hook(
                {"session_id": claude_uuid, "transcript_path": str(transcript)},
                None,
                None,
            )

        backups = list((tmp_path / "backups").glob(f"{claude_uuid}-*.jsonl.bak"))
        assert len(backups) == BACKUP_RETENTION_COUNT == 3


class TestExceptionSwallowing:
    async def test_copy_oserror_does_not_propagate(self, tmp_path, caplog):
        """OSError from shutil.copy2 logs warning, returns {}, does not raise."""
        import logging as _logging

        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        _make_session(claude_uuid)

        original_copy = shutil.copy2

        def exploding_copy(src, dst):
            raise OSError("disk full")

        with (
            patch("agent.hooks.pre_compact.shutil.copy2", side_effect=exploding_copy),
            caplog.at_level(_logging.WARNING, logger="agent.hooks.pre_compact"),
        ):
            result = await pre_compact_hook(
                {"session_id": claude_uuid, "transcript_path": str(transcript)},
                None,
                None,
            )
        assert result == {}
        warnings = [r.message for r in caplog.records if r.levelno >= _logging.WARNING]
        assert any("snapshot failed" in m for m in warnings)
        # Sanity: real copy is still importable after the patch exits
        assert shutil.copy2 is original_copy

    async def test_lookup_error_does_not_propagate(self, tmp_path):
        """Exception from AgentSession.query.filter does not prevent snapshot."""
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)

        # Patch the AgentSession import inside _update_session_cooldown and
        # _check_cooldown so any model lookup raises.
        with patch(
            "models.agent_session.AgentSession.query",
            new_callable=lambda: _RaisingQuery(),
        ):
            result = await pre_compact_hook(
                {"session_id": claude_uuid, "transcript_path": str(transcript)},
                None,
                None,
            )
        assert result == {}

        # Snapshot still landed
        backups = list((tmp_path / "backups").glob("*.jsonl.bak"))
        assert len(backups) == 1


class _RaisingQuery:
    """Stand-in for AgentSession.query that raises on every operation."""

    def filter(self, *_args, **_kwargs):
        raise ConnectionError("redis down")

    def get(self, *_args, **_kwargs):
        raise ConnectionError("redis down")


class TestHookNeverRaises:
    """The hook's top-level contract: never raise, always return {}."""

    async def test_malformed_input_data_returns_empty_dict(self):
        """Malformed input_data (None, missing fields) returns {} without raising."""
        # None input
        result = await pre_compact_hook({}, None, None)  # type: ignore[arg-type]
        assert result == {}

    async def test_session_id_none_returns_empty(self):
        result = await pre_compact_hook(
            {"session_id": None, "transcript_path": "/tmp/nope"},
            None,
            None,
        )
        assert result == {}


@pytest.fixture(autouse=True)
def _isolate_agent_sessions():
    """Drop all AgentSession rows after each test for cleanliness."""
    yield
    try:
        for s in AgentSession.query.all():
            try:
                s.delete()
            except Exception:
                pass
    except Exception:
        pass
