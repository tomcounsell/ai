"""Fix 3 (issue #1938): the synthetic-slug worktree cleanup must SKIP deletion
when the runner recorded a ``runner_reap_failed`` event.

The runner's ``_run_one_turn`` finally SYNCHRONOUSLY reaps + confirms its process
group before the executor cleanup runs (finally-ordering guarantee), so the
common case deletes normally. The ONE residual — a pathological unkillable group
whose death the ~1s SIGKILL confirm could not verify — leaves a durable
``runner_reap_failed`` session event; the cleanup reads it and refuses to delete
a worktree out from under a possibly-live child.

These tests exercise ``_session_recorded_reap_failure`` (the extracted marker
check) directly, plus a source-inspection guard that the finally block gates on
it. ``agent/worktree_manager.py`` is intentionally NOT modified.
"""

from __future__ import annotations

import time

import pytest

from agent import session_executor
from agent.session_executor import _session_recorded_reap_failure
from models.agent_session import AgentSession, SessionType


@pytest.fixture
def session():
    s = AgentSession.create(
        project_key="test-executor-reap",
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"executor-reap-{time.time_ns()}",
        working_dir="/tmp",
        status="running",
    )
    yield s
    try:
        s.delete()
    except Exception:
        pass


class TestSessionRecordedReapFailure:
    def test_no_events_returns_false(self, session):
        """A session with no events → cleanup proceeds (returns False)."""
        assert _session_recorded_reap_failure(session.agent_session_id) is False

    def test_unrelated_events_return_false(self, session):
        """Only a ``runner_reap_failed`` type counts — other events do not."""
        session.session_events = [
            {"type": "runner_turn_spawned", "pid": 4242},
            {"type": "runner_turn", "turn_end_source": "result"},
        ]
        session.save(update_fields=["session_events"])
        assert _session_recorded_reap_failure(session.agent_session_id) is False

    def test_reap_failed_event_returns_true(self, session):
        """A recorded ``runner_reap_failed`` event → skip cleanup (returns True)."""
        session.session_events = [
            {"type": "runner_turn_spawned", "pid": 4242},
            {"type": "runner_reap_failed", "pid": 4242, "pgid": 4242},
        ]
        session.save(update_fields=["session_events"])
        assert _session_recorded_reap_failure(session.agent_session_id) is True

    def test_reads_fresh_not_stale_in_memory(self, session):
        """The check re-reads from Popoto, so a marker written after the in-scope
        object was loaded is still seen (the stale-object hazard the plan calls out)."""
        # Simulate: the marker is persisted by a different writer (the runner)
        # after this test's ``session`` handle was constructed.
        loaded = AgentSession.get_by_id(session.agent_session_id)
        loaded.session_events = [{"type": "runner_reap_failed", "pid": 999, "pgid": 999}]
        loaded.save(update_fields=["session_events"])
        # The original handle never saw the marker, but the fresh reload does.
        assert _session_recorded_reap_failure(session.agent_session_id) is True

    def test_none_id_returns_false(self):
        """No agent_session_id → nothing to read → proceed (False), no crash."""
        assert _session_recorded_reap_failure(None) is False


class TestCleanupGating:
    def test_finally_block_gates_cleanup_on_reap_marker(self):
        """Source guard: the synthetic-slug cleanup must consult the marker
        helper before calling ``cleanup_after_merge`` (Fix 3, #1938)."""
        import inspect

        src = inspect.getsource(session_executor)
        assert "_session_recorded_reap_failure(session.agent_session_id)" in src, (
            "synthetic-slug cleanup must skip deletion when the runner_reap_failed "
            "marker is present (#1938 Fix 3)"
        )
        # The skip and the delete are mutually exclusive branches.
        assert "SKIPPING worktree cleanup" in src

    def test_finally_block_carries_exit_finalize_guard(self):
        """Source guard (#3176, #3209): the exit finalize guard must sit in the
        `finally` AHEAD of the cleanup_after_merge call, so a raising exit's
        still-`running` row does not permanently block its own removal."""
        import inspect

        src = inspect.getsource(session_executor)
        # Anchor on the call site's unique `reason=`, not on the bare helper
        # name — that also matches the `def`, which trivially precedes
        # everything and would make this assertion vacuous.
        guard_pos = src.index('reason="executor exit finalize guard (#3209)"')
        cleanup_pos = src.index("cleanup_after_merge(_repo_for_cleanup")
        assert guard_pos < cleanup_pos, (
            "the finalize guard must run before the synthetic-slug cleanup, "
            "or the busy check refuses the removal permanently"
        )
        assert 'auth.status != "running"' in src
        # `task` is pre-assigned before the `try`, so a raise before
        # `task = BackgroundTask(...)` reaches the guard as a plain name
        # rather than a NameError.
        assert "\n    task = None\n" in src

    def test_finally_block_logs_blocked_cleanup_loudly(self):
        """Source guard (#3176): a refused cleanup (blocked_by_session) must
        emit a named [synthetic-slug] ... cleanup blocked WARNING mirroring
        the neighbouring runner_reap_failed message."""
        import inspect

        src = inspect.getsource(session_executor)
        assert "blocked_by_session" in src
        assert "cleanup blocked" in src
