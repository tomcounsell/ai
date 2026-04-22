"""End-to-end integration tests for compaction hardening (issue #1127).

Exercises the full chain: a PreCompact hook fires on a real (temp) JSONL file,
the AgentSession in Redis records the cooldown timestamp, and the output router
sees the timestamp and defers a subsequent nudge tick.

Verifies the three pieces work together — the hook, the model fields, and the
router's defer guard — not just each piece in isolation.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from agent.hooks.pre_compact import (
    BACKUP_RETENTION_COUNT,
    COMPACTION_COOLDOWN_SECONDS,
    pre_compact_hook,
)
from agent.output_router import (
    POST_COMPACT_NUDGE_GUARD_SECONDS,
    route_session_output,
)
from models.agent_session import AgentSession


def _make_transcript(tmp_path: Path, lines: int = 5) -> Path:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(f'{{"type":"user","content":"msg{i}"}}' for i in range(lines)) + "\n"
    )
    return transcript


def _make_session(claude_uuid: str, project_key: str = "test-compaction-int") -> AgentSession:
    session = AgentSession(
        session_id=f"int-{claude_uuid[:8]}",
        session_type="dev",
        project_key=project_key,
        claude_session_uuid=claude_uuid,
    )
    session.save()
    return session


def _reload_by_claude_uuid(claude_uuid: str) -> AgentSession | None:
    """Reload a session by claude_session_uuid (robust against index stale-state)."""
    rows = list(AgentSession.query.filter(claude_session_uuid=claude_uuid))
    return rows[0] if rows else None


@pytest.fixture(autouse=True)
def _clean_sessions(redis_test_db):
    """Drop AgentSession rows after each test for isolation."""
    yield
    try:
        for s in AgentSession.query.all():
            try:
                s.delete()
            except Exception:
                pass
    except Exception:
        pass


class TestHookToRouterIntegration:
    """Hook fires → AgentSession field updated → router observes guard."""

    @pytest.mark.asyncio
    async def test_hook_arms_router_defer(self, tmp_path):
        """After the hook fires, route_session_output for that session defers."""
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        _make_session(claude_uuid)

        # Fire the PreCompact hook on the transcript.
        await pre_compact_hook(
            {
                "session_id": claude_uuid,
                "transcript_path": str(transcript),
                "trigger": "auto",
            },
            None,
            None,
        )

        # Backup exists.
        backups = list((tmp_path / "backups").glob(f"{claude_uuid}-*.jsonl.bak"))
        assert len(backups) == 1

        # Re-read the session — last_compaction_ts is set.
        refreshed = _reload_by_claude_uuid(claude_uuid)
        assert refreshed is not None
        assert refreshed.last_compaction_ts is not None
        assert refreshed.compaction_count == 1
        ts_value = float(refreshed.last_compaction_ts)
        assert abs(time.time() - ts_value) < 5.0

        # The output router, given that timestamp, defers.
        action, _cap = route_session_output(
            msg="some output",
            stop_reason="end_turn",
            auto_continue_count=0,
            last_compaction_ts=ts_value,
        )
        assert action == "defer_post_compact"

    @pytest.mark.asyncio
    async def test_router_releases_after_window_elapses(self, tmp_path):
        """After 30+s, the router stops deferring and nudges/delivers normally."""
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        _make_session(claude_uuid)

        await pre_compact_hook(
            {"session_id": claude_uuid, "transcript_path": str(transcript)},
            None,
            None,
        )

        # Rewind last_compaction_ts so the guard window has elapsed.
        refreshed = _reload_by_claude_uuid(claude_uuid)
        assert refreshed is not None
        old_ts = time.time() - (POST_COMPACT_NUDGE_GUARD_SECONDS + 60)
        refreshed.last_compaction_ts = old_ts
        refreshed.save(update_fields=["last_compaction_ts"])

        action, _cap = route_session_output(
            msg="follow-up output",
            stop_reason="end_turn",
            auto_continue_count=0,
            last_compaction_ts=old_ts,
        )
        assert action != "defer_post_compact"
        assert action == "deliver"


class TestCooldownIntegration:
    """A second PreCompact within 5 minutes is a no-op end-to-end."""

    @pytest.mark.asyncio
    async def test_second_hook_fire_creates_no_new_backup(self, tmp_path):
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        _make_session(claude_uuid)

        # First fire — backup written, count=1.
        await pre_compact_hook(
            {"session_id": claude_uuid, "transcript_path": str(transcript)},
            None,
            None,
        )
        backups_after_first = list((tmp_path / "backups").glob("*.jsonl.bak"))
        assert len(backups_after_first) == 1

        # Second fire moments later — no new backup.
        await pre_compact_hook(
            {"session_id": claude_uuid, "transcript_path": str(transcript)},
            None,
            None,
        )
        backups_after_second = list((tmp_path / "backups").glob("*.jsonl.bak"))
        assert len(backups_after_second) == 1
        assert backups_after_second[0].name == backups_after_first[0].name

        # Session: count stayed at 1, skipped count went to 1.
        refreshed = _reload_by_claude_uuid(claude_uuid)
        assert refreshed is not None
        assert refreshed.compaction_count == 1
        assert refreshed.compaction_skipped_count == 1


class TestRetentionIntegration:
    """End-to-end retention: 4 fires (with cooldown manually bypassed) → 3 backups remain."""

    @pytest.mark.asyncio
    async def test_retention_keeps_last_three_backups(self, tmp_path):
        claude_uuid = str(uuid.uuid4())
        transcript = _make_transcript(tmp_path)
        session = _make_session(claude_uuid)
        # Reload by primary key (session.id) instead of the claude_session_uuid
        # index. The index can lag across the autouse _clean_sessions teardown,
        # producing a flaky `None` return on iteration 1. Primary-key lookup is
        # unaffected (#1127 PR review tech-debt fix).
        session_id = session.id

        for i in range(4):
            if i > 0:
                # Bypass the 5-minute cooldown by rewinding the timestamp.
                refreshed = AgentSession.get_by_id(session_id)
                assert refreshed is not None
                refreshed.last_compaction_ts = time.time() - (COMPACTION_COOLDOWN_SECONDS + 10)
                refreshed.save(update_fields=["last_compaction_ts"])
                # Sleep to ensure the integer-second timestamp in the filename advances.
                time.sleep(1.1)
            await pre_compact_hook(
                {"session_id": claude_uuid, "transcript_path": str(transcript)},
                None,
                None,
            )

        backups = sorted((tmp_path / "backups").glob(f"{claude_uuid}-*.jsonl.bak"))
        assert len(backups) == BACKUP_RETENTION_COUNT == 3

        final = AgentSession.get_by_id(session_id)
        assert final is not None
        assert final.compaction_count == 4
