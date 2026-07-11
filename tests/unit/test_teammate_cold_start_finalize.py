"""Regression tests for the teammate cold-start finalize gap (issue #2007).

Two defects, discovered together during a 2026-07-10 production incident,
share a single root cause: duplicate ``AgentSession`` records for one
``session_id`` in divergent statuses, mishandled at two consumer sites.

Defect B — a completed cold-start run never reached a terminal status. The
unconditional completion-exit finalize guard in ``agent/session_executor.py``
(added after the whole ``if agent_session: / else:`` completion block) is the
load-bearing fix: it re-reads the authoritative session and finalizes it if
still ``running``, regardless of what ``complete_transcript`` did. These
tests prove the GUARD closes the gap — not just the ``complete_transcript``
migration to ``get_authoritative_session`` (covered separately in
``tests/unit/test_session_transcript.py``).

Defect A — the pop loop spun silently forever on a duplicate-record
``StatusConflictError``. ``agent/agent_session_queue.py`` now bounds this
with a loop-local per-``session_id`` conflict counter: at N=3 it deletes the
stale terminal duplicate (child-guarded, via the shared
``_delete_stale_terminal_duplicates`` helper) so the queued ``pending``
record pops cleanly; at N=6 (residual case — the terminal duplicate has
children so the delete was skipped) it cancels the ``pending`` record with
``cancel_reason="conflict_escalation"`` as a bounded last resort.

Real integration tests against the local Redis test DB (autouse
``redis_test_db`` fixture) — no mocks for the ORM/Redis layer. Only the
runner (``agent.session_runner.SessionRunner``) and worktree provisioning are
faked, matching the established pattern in
``tests/unit/test_session_executor_runner_dispatch.py``. Session ids and
worker keys are uuid-suffixed so parallel ``-n auto --dist=loadfile`` runs
never collide.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import agent.agent_session_queue as asq
from agent.agent_session_queue import (
    _delete_stale_terminal_duplicates,
    _push_agent_session,
    _worker_loop,
)
from agent.cancel_reason import get_cancel_reason
from agent.session_executor import _execute_agent_session
from models.agent_session import AgentSession
from models.session_lifecycle import StatusConflictError, get_authoritative_session

# ---------------------------------------------------------------------------
# Shared helpers (mirrors tests/unit/test_session_executor_runner_dispatch.py)
# ---------------------------------------------------------------------------


class FakeSessionRunner:
    """Records constructor kwargs; never spawns a real Claude subprocess."""

    instances: list[FakeSessionRunner] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        type(self).instances.append(self)

    async def run(self, user_message: str):
        from agent.session_runner import RunSummary

        return RunSummary(exit_reason="pm_complete", turn_count=1)


@pytest.fixture(autouse=True)
def _reset_fake_runner():
    FakeSessionRunner.instances = []
    yield
    FakeSessionRunner.instances = []


def _patch_runner():
    return patch("agent.session_runner.SessionRunner", FakeSessionRunner)


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _make_teammate_session(session_id: str, **overrides) -> AgentSession:
    """A teammate session with no persisted resume scalars (matches the
    incident: missing runner_cwd / claude_session_uuid / dev_agent_id /
    claude_version fails ``_resume_invalid_reason()`` and takes the
    cold-start-with-prime fallback — not exercised directly here, but the
    default-None scalars below reproduce the same starting condition)."""
    defaults = dict(
        session_id=session_id,
        session_type="teammate",
        project_key="cold-start-finalize-test",
        working_dir="/tmp",
        status="running",
        chat_id=f"chat-{session_id}",
        message_text="hello from teammate",
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )
    defaults.update(overrides)
    return AgentSession.create(**defaults)


# ---------------------------------------------------------------------------
# Defect B — unconditional completion-exit finalize guard
# ---------------------------------------------------------------------------


class TestDefectBCompletionExitGuard:
    """The load-bearing fix: a re-read + finalize after the whole if/else
    completion block, unconditional and independent of complete_transcript's
    own record selection or success."""

    @pytest.mark.asyncio
    async def test_if_branch_divergent_pair_finalizes_despite_noop_complete_transcript(
        self, redis_test_db, caplog
    ):
        """(running, failed) divergent pair, teammate session, invalid/missing
        resume scalars. complete_transcript is stubbed to a no-op (simulating
        the historical bug: it silently no-ops or picks the wrong record —
        spike-1's two candidate triggers). The completion-exit guard must
        still finalize the authoritative running record to a terminal status.
        This proves the GUARD — not complete_transcript's record selection —
        is what closes the phantom-running bug."""
        sid = _sid("defect-b-if")
        running = _make_teammate_session(sid, project_key="dbif")
        # Stale terminal duplicate created AFTER the running record (matches
        # the incident: a re-enqueue produced a divergent pair).
        AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="dbif",
            working_dir="/tmp",
            status="failed",
            chat_id=running.chat_id,
            message_text="stale duplicate",
            sender_name="tester",
            created_at=datetime.now(tz=UTC) + timedelta(seconds=1),
            turn_count=0,
            tool_call_count=0,
        )

        def _noop_complete_transcript(*_a, **_k):
            return None

        with (
            _patch_runner(),
            patch("bridge.session_transcript.complete_transcript", _noop_complete_transcript),
            caplog.at_level(logging.INFO),
        ):
            await _execute_agent_session(running)

        # Re-read the ORIGINAL running record by its stable `id` (not via
        # get_authoritative_session's tie-break, which — once BOTH records
        # are terminal — falls back to most-recent-created_at and would
        # otherwise pick the stale duplicate here, an artifact of this test's
        # fixture timing rather than anything under test).
        reloaded_running = AgentSession.get_by_id(running.id)
        assert reloaded_running is not None
        assert reloaded_running.status != "running", (
            "Authoritative record must not stay running — the completion-exit "
            "guard must finalize it even when complete_transcript no-ops."
        )
        assert reloaded_running.status == "completed"

        # Stale terminal duplicate is untouched by Defect B's fix — Defect A's
        # reconciliation (a separate mechanism) is what removes it.
        remaining = list(AgentSession.query.filter(session_id=sid))
        statuses = sorted(s.status for s in remaining)
        assert statuses == ["completed", "failed"]

        guard_logs = [
            r.getMessage()
            for r in caplog.records
            if "Completion-exit guard finalized session" in r.getMessage()
        ]
        assert guard_logs, "expected the unconditional completion-exit guard to log its finalize"

    @pytest.mark.asyncio
    async def test_else_branch_agent_session_none_still_finalizes_authoritative_record(
        self, redis_test_db, caplog
    ):
        """Force the `else:` exit (agent_session lookup returns None — #917
        race) by making the in-memory session's project_key diverge from the
        persisted record's project_key at the status="running" lookup, while
        the persisted record genuinely stays running. complete_transcript is
        again stubbed to a no-op. The guard must still finalize the
        authoritative record — round-2 CONCERN 3: a prior version of this
        fallback lived only inside the `if agent_session:` branch and never
        covered this exit."""
        sid = _sid("defect-b-else")
        session = _make_teammate_session(sid, project_key="dbelse-real")
        # Simulate the lookup race: the in-memory project_key used for the
        # status="running" filter no longer matches the persisted record.
        session.project_key = "dbelse-mismatched"

        def _noop_complete_transcript(*_a, **_k):
            return None

        with (
            _patch_runner(),
            patch("bridge.session_transcript.complete_transcript", _noop_complete_transcript),
            caplog.at_level(logging.INFO),
        ):
            await _execute_agent_session(session)

        authoritative = get_authoritative_session(sid)
        assert authoritative is not None
        assert authoritative.status != "running"
        assert authoritative.status == "completed"

        guard_logs = [
            r.getMessage()
            for r in caplog.records
            if "Completion-exit guard finalized session" in r.getMessage()
        ]
        assert guard_logs, (
            "expected the post if/else completion-exit guard to cover the "
            "agent_session-is-None exit, not just the if agent_session: branch"
        )

    @pytest.mark.asyncio
    async def test_guard_own_status_conflict_error_treated_as_success(self, redis_test_db, caplog):
        """When the guard's re-read finds status="running" but finalize_session
        itself raises StatusConflictError (another actor won the race), the
        guard must swallow it as success — no exception escapes, no ERROR/
        WARNING log for a benign race."""
        sid = _sid("defect-b-swallow")
        session = _make_teammate_session(sid, project_key="dbswallow")

        def _noop_complete_transcript(*_a, **_k):
            return None

        def _raising_finalize(*_a, **_k):
            raise StatusConflictError(sid, "running", "completed", reason="raced by another actor")

        with (
            _patch_runner(),
            patch("bridge.session_transcript.complete_transcript", _noop_complete_transcript),
            patch("models.session_lifecycle.finalize_session", _raising_finalize),
            caplog.at_level(logging.WARNING),
        ):
            # Must not raise.
            await _execute_agent_session(session)

        fail_logs = [
            r.getMessage()
            for r in caplog.records
            if "Completion-exit finalize guard failed" in r.getMessage()
        ]
        assert not fail_logs, (
            "StatusConflictError must be swallowed as success, not logged as a failure"
        )


# ---------------------------------------------------------------------------
# Defect A — enqueue-time reconciliation (child-guarded delete)
# ---------------------------------------------------------------------------


class TestDefectAEnqueueReconciliation:
    """_delete_stale_terminal_duplicates() replaces the old no-op
    _mark_superseded — deletes stale terminal duplicates, child-guarded."""

    def test_delete_stale_terminal_duplicates_removes_stale_failed_record(self, redis_test_db):
        sid = _sid("reconcile-delete")
        pending = AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="reconcile",
            working_dir="/tmp",
            status="pending",
            chat_id="chat-reconcile",
            message_text="undelivered reply",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="reconcile",
            working_dir="/tmp",
            status="failed",
            chat_id="chat-reconcile",
            message_text="stale",
            sender_name="tester",
            created_at=datetime.now(tz=UTC) - timedelta(seconds=5),
            turn_count=0,
            tool_call_count=0,
        )

        deleted = _delete_stale_terminal_duplicates(sid)

        assert deleted == 1
        remaining = list(AgentSession.query.filter(session_id=sid))
        assert len(remaining) == 1
        assert remaining[0].id == pending.id
        assert remaining[0].status == "pending"
        assert remaining[0].message_text == "undelivered reply"

    def test_child_guarded_duplicate_not_deleted_and_new_pending_still_created(self, redis_test_db):
        """A terminal duplicate with a child session is skipped by the
        child-guard. The reconciler at enqueue time (_push_agent_session) must
        still create the new pending record even though the stale duplicate
        survives."""
        sid = _sid("reconcile-childguard")
        terminal_with_child = AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="reconcile-cg",
            working_dir="/tmp",
            status="failed",
            chat_id="chat-cg",
            message_text="parent of a child",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        AgentSession.create(
            session_id=_sid("child"),
            session_type="eng",
            project_key="reconcile-cg",
            working_dir="/tmp",
            status="pending",
            chat_id="chat-cg-child",
            message_text="child work",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            parent_agent_session_id=terminal_with_child.id,
            turn_count=0,
            tool_call_count=0,
        )

        # Direct call to the shared helper: child-guard must skip the delete.
        deleted = _delete_stale_terminal_duplicates(sid)
        assert deleted == 0
        still_present = list(AgentSession.query.filter(session_id=sid, status="failed"))
        assert len(still_present) == 1
        # The enqueue-time behavior (a delete-skip must never block new
        # record creation) is covered end-to-end by
        # TestDefectAEnqueuePushCreatesNewPendingDespiteChildGuard below,
        # which drives the real async _push_agent_session() reconciliation
        # path against this same helper.


class TestDefectAEnqueuePushCreatesNewPendingDespiteChildGuard:
    @pytest.mark.asyncio
    async def test_push_agent_session_creates_pending_when_duplicate_child_guarded(
        self, redis_test_db
    ):
        sid = _sid("reconcile-push")
        terminal_with_child = AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="reconcile-push",
            working_dir="/tmp",
            status="failed",
            chat_id="chat-push",
            message_text="parent of a child",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        AgentSession.create(
            session_id=_sid("child"),
            session_type="eng",
            project_key="reconcile-push",
            working_dir="/tmp",
            status="pending",
            chat_id="chat-push-child",
            message_text="child work",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            parent_agent_session_id=terminal_with_child.id,
            turn_count=0,
            tool_call_count=0,
        )

        await _push_agent_session(
            project_key="reconcile-push",
            session_id=sid,
            working_dir="/tmp",
            message_text="new pending work",
            sender_name="tester",
            chat_id="chat-push",
            telegram_message_id=1,
            session_type="teammate",
        )

        records = list(AgentSession.query.filter(session_id=sid))
        statuses = sorted(s.status for s in records)
        # The child-guarded failed duplicate survives; the new pending record
        # was created regardless.
        assert statuses == ["failed", "pending"]


# ---------------------------------------------------------------------------
# Defect A — bounded pop-loop StatusConflictError escalation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_worker_loop_state():
    """Reset shutdown flag / slot registry around every test in this module.

    Follows the established pattern in tests/unit/test_worker_persistent.py —
    the canonical binding lives at agent.session_state, not the (stale)
    import-time copy on the queue module.
    """
    asq._session_state._shutdown_requested = False
    original_registry = asq._session_state._slot_registry
    asq._session_state._slot_registry = None
    yield
    asq._session_state._shutdown_requested = False
    asq._session_state._slot_registry = original_registry


class TestDefectAPopLoopEscalation:
    """Loop-local conflict counter + escalated set in _worker_loop, keyed off
    StatusConflictError.session_id (spike-4)."""

    @pytest.mark.asyncio
    async def test_primary_escalation_deletes_terminal_duplicate_pending_preserved_error_once(
        self, redis_test_db, caplog
    ):
        """At N=3 the stale terminal duplicate is deleted (via the real,
        unmocked _delete_stale_terminal_duplicates against real Redis); this
        delete runs every tick past threshold (ticks 3 and 4 here), but the
        ERROR log fires exactly once. The pending record — the work-bearing
        queued session with its undelivered reply — is NEVER cancelled."""
        import asyncio

        sid = _sid("escalation-primary")
        worker_key = f"wk-{uuid.uuid4().hex[:10]}"
        pending = AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="escalation-primary",
            working_dir="/tmp",
            status="pending",
            chat_id=worker_key,
            message_text="IMPORTANT UNDELIVERED REPLY",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="escalation-primary",
            working_dir="/tmp",
            status="failed",
            chat_id=worker_key,
            message_text="stale terminal duplicate",
            sender_name="tester",
            created_at=datetime.now(tz=UTC) - timedelta(seconds=5),
            turn_count=0,
            tool_call_count=0,
        )

        calls = {"n": 0}
        stop_after = 4

        def pop_side_effect(*_a, **_k):
            calls["n"] += 1
            if calls["n"] >= stop_after:
                asq._session_state._shutdown_requested = True
            raise StatusConflictError(
                sid, "pending", "failed", reason="ambiguous terminal duplicate"
            )

        event = asyncio.Event()
        with (
            patch(
                "agent.agent_session_queue._pop_agent_session",
                new=AsyncMock(side_effect=pop_side_effect),
            ),
            caplog.at_level(logging.ERROR, logger="agent.agent_session_queue"),
        ):
            await _worker_loop(worker_key, event)

        assert calls["n"] == stop_after

        # The stale terminal duplicate is gone; the pending record survives
        # untouched, with its reply intact.
        remaining = list(AgentSession.query.filter(session_id=sid))
        assert len(remaining) == 1
        assert remaining[0].id == pending.id
        assert remaining[0].status == "pending"
        assert remaining[0].message_text == "IMPORTANT UNDELIVERED REPLY"

        error_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "StatusConflictError repeated" in r.getMessage()
        ]
        assert len(error_logs) == 1, (
            f"expected exactly one escalation ERROR log, got {len(error_logs)}: "
            f"{[r.getMessage() for r in error_logs]}"
        )

    @pytest.mark.asyncio
    async def test_last_resort_cancels_pending_with_reason_when_terminal_duplicate_has_children(
        self, redis_test_db, caplog
    ):
        """Residual case: the terminal duplicate has a child session, so the
        child-guard skips its delete and the conflict persists past the
        primary remediation. At N=6, the pending record is cancelled via a
        terminal transition with cancel_reason == "conflict_escalation"."""
        import asyncio

        sid = _sid("escalation-lastresort")
        worker_key = f"wk-{uuid.uuid4().hex[:10]}"
        pending = AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="escalation-lastresort",
            working_dir="/tmp",
            status="pending",
            chat_id=worker_key,
            message_text="stuck reply",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        terminal_with_child = AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="escalation-lastresort",
            working_dir="/tmp",
            status="failed",
            chat_id=worker_key,
            message_text="terminal duplicate with a child",
            sender_name="tester",
            created_at=datetime.now(tz=UTC) - timedelta(seconds=5),
            turn_count=0,
            tool_call_count=0,
        )
        AgentSession.create(
            session_id=_sid("child"),
            session_type="eng",
            project_key="escalation-lastresort",
            working_dir="/tmp",
            status="pending",
            chat_id="child-chat",
            message_text="child work",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            parent_agent_session_id=terminal_with_child.id,
            turn_count=0,
            tool_call_count=0,
        )

        calls = {"n": 0}
        stop_after = 6

        def pop_side_effect(*_a, **_k):
            calls["n"] += 1
            if calls["n"] >= stop_after:
                asq._session_state._shutdown_requested = True
            raise StatusConflictError(
                sid, "pending", "failed", reason="ambiguous terminal duplicate (child-guarded)"
            )

        event = asyncio.Event()
        with (
            patch(
                "agent.agent_session_queue._pop_agent_session",
                new=AsyncMock(side_effect=pop_side_effect),
            ),
            caplog.at_level(logging.ERROR, logger="agent.agent_session_queue"),
        ):
            await _worker_loop(worker_key, event)

        assert calls["n"] == stop_after

        # Terminal duplicate (child-guarded) is never deleted.
        terminal_reloaded = list(AgentSession.query.filter(session_id=sid, status="failed"))
        assert len(terminal_reloaded) == 1
        assert terminal_reloaded[0].id == terminal_with_child.id

        # The stuck pending record is cancelled as the bounded last resort.
        cancelled = [s for s in AgentSession.query.filter(session_id=sid) if s.id == pending.id]
        assert len(cancelled) == 1
        assert cancelled[0].status == "cancelled"
        assert get_cancel_reason(sid) == "conflict_escalation"

        last_resort_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "terminal duplicate could not be" in r.getMessage()
        ]
        assert len(last_resort_logs) == 1

    @pytest.mark.asyncio
    async def test_status_conflict_error_carries_real_session_id_on_pop_path(self, redis_test_db):
        """spike-4 / round-2 CONCERN 1: StatusConflictError raised by the real
        (unmocked) _pop_agent_session on a genuine divergent-duplicate CAS
        conflict must carry the real session_id (not "?"), so the pop-loop's
        counter keys correctly."""
        from agent.session_pickup import _pop_agent_session

        sid = _sid("spike4")
        worker_key = f"wk-{uuid.uuid4().hex[:10]}"
        AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="spike4",
            working_dir="/tmp",
            status="pending",
            chat_id=worker_key,
            message_text="pending work",
            sender_name="tester",
            created_at=datetime.now(tz=UTC) - timedelta(seconds=5),
            turn_count=0,
            tool_call_count=0,
        )
        # Terminal duplicate created LATER so get_authoritative_session's
        # tie-break (prefer running, else most-recent) resolves to it — this
        # is exactly the ambiguity that produces the pop-path CAS conflict.
        AgentSession.create(
            session_id=sid,
            session_type="teammate",
            project_key="spike4",
            working_dir="/tmp",
            status="failed",
            chat_id=worker_key,
            message_text="stale duplicate, more recent created_at",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        with pytest.raises(StatusConflictError) as excinfo:
            await _pop_agent_session(worker_key, is_project_keyed=False)

        assert excinfo.value.session_id == sid
        assert excinfo.value.session_id not in ("?", "", None)
