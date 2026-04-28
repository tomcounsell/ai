"""Unit tests for the executor-entry guard against ``None`` ``working_dir`` /
``session_id`` (issue #1195).

The guard at ``agent/session_executor.py`` short-circuits ``_execute_agent_session``
before reaching ``Path(session.working_dir)`` when either field is ``None``.
The session is marked ``failed`` and a ``[executor-guard]`` error log carrying
``reason=missing_working_dir_or_session_id`` is emitted. There is no
``failure_reason`` column on AgentSession — the structured log is the durable
failure record (consumed by reflections and dashboards via log scrape). The
guard is the defense-in-depth counterpart to the spawn-site fix in
``_create_continuation_pm``: any future spawn site that forgets a required
field fails loudly here instead of crashing mid-startup with no Telegram
surface.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from agent.session_executor import _execute_agent_session
from models.agent_session import AgentSession


@pytest.fixture
def _block_path_constructor(monkeypatch):
    """Replace ``pathlib.Path`` inside the executor with a sentinel that raises.

    If the guard does *not* short-circuit, the next line builds
    ``Path(session.working_dir)`` — this fixture turns that into a loud
    ``RuntimeError`` so the test fails informatively rather than masking the
    regression with a real ``TypeError`` deep in the stack.
    """

    def _explode(*args, **kwargs):
        raise RuntimeError("Path() constructor reached — executor guard did not short-circuit")

    monkeypatch.setattr("agent.session_executor.Path", _explode)


class TestExecutorGuardWorkingDirNone:
    @pytest.mark.asyncio
    async def test_none_working_dir_marks_failed_does_not_raise(
        self, redis_test_db, _block_path_constructor, caplog
    ):
        """``working_dir=None`` → session marked ``failed``, no exception, no Path()."""
        session = AgentSession.create(
            session_id="exec-guard-wd-001",
            session_type="pm",
            project_key="test",
            # working_dir intentionally omitted (defaults to None on save).
            status="pending",
            chat_id="999",
            message_text="Should never run",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        assert session.working_dir is None

        with caplog.at_level(logging.ERROR):
            # Must not raise; guard short-circuits the body.
            await _execute_agent_session(session)

        # Session is marked failed (in-memory state from finalize_session).
        assert session.status == "failed"

        # Structured log emitted with the expected prefix, field name, and the
        # canonical ``missing_working_dir_or_session_id`` reason. The reason is
        # not stored on the model (no ``failure_reason`` field) — the log line
        # is the durable failure record consumed by reflections / dashboards.
        guard_records = [r for r in caplog.records if "[executor-guard]" in r.message]
        assert guard_records
        assert any("working_dir" in r.message for r in guard_records)
        assert any("missing_working_dir_or_session_id" in r.message for r in guard_records)


class TestExecutorGuardSessionIdNone:
    @pytest.mark.asyncio
    async def test_none_session_id_marks_failed_does_not_raise(
        self, redis_test_db, _block_path_constructor, caplog
    ):
        """``session_id=None`` → session marked ``failed``, no exception, no Path()."""
        # We cannot create an AgentSession with session_id=None via the public
        # factories (they require it as a positional / keyword argument). Use
        # the model directly to construct a synthetic instance, then null the
        # field, mirroring the historical broken-spawn shape from #1195.
        session = AgentSession.create(
            session_id="exec-guard-sid-001",
            session_type="pm",
            project_key="test",
            working_dir="/tmp",
            status="pending",
            chat_id="999",
            message_text="Should never run",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        # Force the corrupt state we are guarding against.
        session.session_id = None

        with caplog.at_level(logging.ERROR):
            await _execute_agent_session(session)

        # Status moved to failed (in-memory; finalize_session mutates the
        # same instance the guard branched on).
        assert session.status == "failed"

        # The structured ``[executor-guard]`` log line carries the offending
        # field and the canonical reason — there is no ``failure_reason`` field
        # on AgentSession, so the log is the durable record.
        guard_records = [r for r in caplog.records if "[executor-guard]" in r.message]
        assert guard_records
        assert any("session_id" in r.message for r in guard_records)
        assert any("missing_working_dir_or_session_id" in r.message for r in guard_records)


class TestExecutorGuardLogStructure:
    @pytest.mark.asyncio
    async def test_both_none_logs_structured_error_with_parent_id(
        self, redis_test_db, _block_path_constructor, caplog
    ):
        """When both fields are ``None``, the log includes parent context."""
        parent_uuid = "parent-uuid-for-guard-test"
        session = AgentSession.create(
            session_id="exec-guard-both-001",
            session_type="pm",
            project_key="test",
            working_dir="/tmp",  # set initially so create() succeeds
            status="pending",
            chat_id="999",
            message_text="Should never run",
            parent_agent_session_id=parent_uuid,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        # Force both fields None — synthetic worst case.
        session.working_dir = None
        session.session_id = None

        with caplog.at_level(logging.ERROR):
            await _execute_agent_session(session)

        # The structured log must include the parent_agent_session_id and
        # session_type so dashboards / reflections can correlate it.
        guard_records = [r for r in caplog.records if "[executor-guard]" in r.message]
        assert guard_records, "Expected at least one [executor-guard] error log"
        joined = " ".join(r.message for r in guard_records)
        assert parent_uuid in joined
        assert "session_type=pm" in joined
