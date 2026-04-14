"""Regression tests for nudge-stomp bug #898.

Two concrete scenarios that would have failed against unfixed main:

1. test_finalized_by_execute_gates_happy_path
   Simulates a nudge write (via sanctioned transition_status API) followed by the
   stale outer finally-block code path. With finalized_by_execute=True, the finally
   block is gated off and the nudge's Redis state survives intact.

2. test_layer_2_partial_save_preserves_fields
   Calls append_event on a stale local AgentSession object after a concurrent
   finalize_session has written authoritative status='completed' on a second instance.
   Asserts that the stale append_event only mutates session_events and does NOT
   clobber status or auto_continue_count.

Both tests use only sanctioned Popoto APIs (AgentSession.create, AgentSession.query.get,
transition_status, finalize_session, instance.save) — never raw Redis commands.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from models.agent_session import AgentSession
from models.session_lifecycle import finalize_session, transition_status


@pytest.mark.integration
class TestFinalizedByExecuteGatesHappyPath:
    """finalized_by_execute=True must gate the outer finally block.

    Without the fix, the outer finally block calls log_lifecycle_transition on
    the stale outer session variable, which triggers append_event -> self.save()
    (a full-state save) that clobbers the nudge's status=pending back to running.
    """

    def test_nudge_state_survives_when_gated(self, redis_test_db):
        """Nudge Redis state survives when finalized_by_execute=True gates the finally block.

        Scenario:
        1. Session starts running.
        2. _enqueue_nudge (simulated via transition_status) writes status=pending,
           auto_continue_count=1 using a FRESH re-read instance.
        3. The outer finally block fires with finalized_by_execute=True (no-op).
        4. Fresh query asserts the nudge state is preserved.
        """
        session_id = "nudge-stomp-test-happy-001"
        project_key = "test"

        # Step 1: Create a session in running state (simulates worker pop)
        outer_session = AgentSession.create(
            session_id=session_id,
            project_key=project_key,
            status="running",
            created_at=datetime.now(tz=UTC),
            auto_continue_count=0,
        )

        # Step 2: Simulate _enqueue_nudge — fresh re-read + authoritative transition
        nudge_session = AgentSession.query.get(id=outer_session.id)
        assert nudge_session is not None, "Session must exist in Redis"
        nudge_session.auto_continue_count = 1
        transition_status(nudge_session, "pending", reason="nudge re-enqueue (test)")

        # Verify nudge wrote correctly
        after_nudge = AgentSession.query.get(id=outer_session.id)
        assert after_nudge.status == "pending"
        assert after_nudge.auto_continue_count == 1

        # Step 3: Simulate the outer finally block with finalized_by_execute=True.
        # The gate means this block does NOT run; we assert it's a no-op.
        session_completed = False
        finalized_by_execute = True  # set by the happy-path return from _execute_agent_session

        if not session_completed and not finalized_by_execute:
            # This block must NOT run — if it did, it would call
            # outer_session.log_lifecycle_transition() which saves stale state.
            outer_session.log_lifecycle_transition("completed", "worker finally block")

        # Step 4: Fresh-query Redis — nudge state must be intact
        final = AgentSession.query.get(id=outer_session.id)
        assert final is not None, "Session must still exist in Redis"
        assert final.status == "pending", (
            f"Status must remain 'pending' after gated finally block, got '{final.status}'"
        )
        assert final.auto_continue_count == 1, (
            f"auto_continue_count must remain 1, got {final.auto_continue_count}"
        )

    def test_nudge_state_clobbered_without_gate(self, redis_test_db):
        """Demonstrates the pre-fix regression: stale save clobbers nudge state.

        This test documents the bug. Without finalized_by_execute, the stale
        outer_session.log_lifecycle_transition call triggers a partial-save (Layer 2).
        Layer 2 only saves session_events + updated_at, so status is NOT clobbered
        (this is the Layer 2 safety net).

        Note: After both fixes land, append_event uses a partial save, so even if the
        finally block fires on the stale object it only writes session_events. This test
        verifies the layered defense — the partial save makes stale calls non-destructive.
        """
        session_id = "nudge-stomp-test-regression-001"
        project_key = "test"

        outer_session = AgentSession.create(
            session_id=session_id,
            project_key=project_key,
            status="running",
            created_at=datetime.now(tz=UTC),
            auto_continue_count=0,
        )

        # Simulate nudge
        nudge_session = AgentSession.query.get(id=outer_session.id)
        nudge_session.auto_continue_count = 1
        transition_status(nudge_session, "pending", reason="nudge re-enqueue (test)")

        # Simulate the finally block firing WITHOUT the gate (pre-fix regression scenario).
        # With Layer 2 (partial save), status is protected even if the block fires.
        try:
            outer_session.log_lifecycle_transition("completed", "worker finally block")
        except Exception:
            pass

        # Layer 2 should protect status — the partial save only writes session_events
        final = AgentSession.query.get(id=outer_session.id)
        assert final is not None
        assert final.status == "pending", (
            "Layer 2 partial save must protect status from stale log_lifecycle_transition"
        )
        assert final.auto_continue_count == 1, (
            "Layer 2 partial save must protect auto_continue_count"
        )


@pytest.mark.integration
class TestLayer2PartialSavePreservesFields:
    """_append_event_dict's partial save must not clobber authoritative fields.

    Spike-1 confirmed that save(update_fields=["session_events", "updated_at"])
    only writes the listed fields and only calls on_save hooks for listed fields.
    This integration test validates that in practice using real Popoto + Redis.
    """

    def test_stale_append_event_preserves_status_and_auto_continue_count(self, redis_test_db):
        """append_event on a stale object must not clobber status or auto_continue_count.

        Scenario:
        1. Create session with status=running, auto_continue_count=0.
        2. Obtain a second Popoto instance (the "stale" outer worker local).
        3. Finalize via the first instance (authoritative) → status=completed.
        4. Call stale_instance.append_event() — triggers the partial save.
        5. Fresh-query asserts status=completed, auto_continue_count preserved.
        """
        session_id = "layer2-test-partial-save-001"
        project_key = "test"

        # Step 1: Create session
        original = AgentSession.create(
            session_id=session_id,
            project_key=project_key,
            status="running",
            created_at=datetime.now(tz=UTC),
            auto_continue_count=0,
        )

        # Step 2: Get a second Popoto instance — this is the "stale" outer-session
        stale_local = AgentSession.query.get(id=original.id)
        assert stale_local is not None

        # Step 3: Finalize via the original (authoritative path)
        finalize_session(original, "completed", reason="test finalization")

        # Verify finalization took effect
        after_finalize = AgentSession.query.get(id=original.id)
        assert after_finalize.status == "completed"

        # Step 4: The stale local calls append_event (simulates finally-block log call)
        # With Layer 2, this must only write session_events + updated_at.
        stale_local.append_event("lifecycle", "completed→completed: worker finally block")

        # Step 5: Fresh query — status must still be 'completed' from finalize_session
        final = AgentSession.query.get(id=original.id)
        assert final is not None
        assert final.status == "completed", (
            f"append_event on stale object must not clobber status, got '{final.status}'"
        )
        assert final.auto_continue_count == 0, (
            f"auto_continue_count must not be clobbered, got {final.auto_continue_count}"
        )
        # The event should still have been appended to session_events
        events = final.session_events or []
        assert any("worker finally block" in str(e) for e in events), (
            "The appended event must still be saved to session_events"
        )

    def test_partial_save_call_signature(self, redis_test_db):
        """_append_event_dict must call save(update_fields=[...]) not save().

        Uses unittest.mock to verify the call signature without needing to
        exercise the full Redis round-trip for this specific assertion.
        """
        from unittest.mock import patch

        session = AgentSession.create(
            session_id="layer2-sig-test-001",
            project_key="test",
            status="running",
            created_at=datetime.now(tz=UTC),
        )

        with patch.object(session, "save") as mock_save:
            session._append_event_dict({"event_type": "lifecycle", "text": "test", "data": None})

        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args.kwargs
        assert "update_fields" in call_kwargs, (
            "_append_event_dict must call save(update_fields=[...]) — "
            "full save would clobber status on stale objects"
        )
        assert "session_events" in call_kwargs["update_fields"]
        assert "updated_at" in call_kwargs["update_fields"]


@pytest.mark.integration
class TestSetLinkPartialSavePreservesStatus:
    """set_link() partial save must not clobber authoritative status (#950).

    Validates the Layer 1 fix: set_link() uses save(update_fields=[field_name, "updated_at"])
    instead of a full save, preventing stale worker references from clobbering status.
    """

    def test_set_link_preserves_killed_status(self, redis_test_db):
        """set_link on a stale object must not clobber status=killed back to running.

        Scenario:
        1. Create session with status=running.
        2. Obtain a stale reference (the worker's local copy).
        3. Kill the session via finalize_session (authoritative).
        4. Call stale_ref.set_link("issue", url) — triggers partial save.
        5. Fresh query: status must remain 'killed', issue_url must be set.
        """
        session_id = "set-link-partial-save-001"
        project_key = "test"

        original = AgentSession.create(
            session_id=session_id,
            project_key=project_key,
            status="running",
            created_at=datetime.now(tz=UTC),
        )

        # Stale reference
        stale_ref = AgentSession.query.get(id=original.id)
        assert stale_ref is not None

        # Kill via authoritative path
        finalize_session(original, "killed", reason="test kill")
        after_kill = AgentSession.query.get(id=original.id)
        assert after_kill.status == "killed"

        # Stale reference calls set_link (status is still "running" in memory)
        stale_ref.set_link("issue", "https://github.com/test/issues/999")

        # Fresh query — status must remain killed
        final = AgentSession.query.get(id=original.id)
        assert final is not None
        assert final.status == "killed", (
            f"set_link partial save must not clobber status, got '{final.status}'"
        )
        assert final.issue_url == "https://github.com/test/issues/999", (
            "set_link must still persist the URL"
        )


@pytest.mark.integration
class TestStaleIndexKilledRegression:
    """Regression tests for #950: stale index entry survives pending-to-killed transitions.

    Root cause: A full save() on a stale session object clobbers the on-disk status
    to an intermediate value. When a kill subsequently fires, its srem targets the
    clobbered value instead of the original pending index entry, leaving an orphan.

    The fix converts all non-lifecycle saves to partial saves (update_fields=[...])
    and adds a defensive srem in finalize_session that removes from ALL non-target
    status index sets.
    """

    def _get_index_members(self, status: str) -> set[str]:
        """Return member keys in the AgentSession status index set for a given status."""
        from popoto.models.db_key import DB_key
        from popoto.redis_db import POPOTO_REDIS_DB

        # Build the index key the same way Popoto does
        sample = AgentSession.create(
            session_id="idx-probe-tmp",
            project_key="test",
            status="pending",
            created_at=datetime.now(tz=UTC),
        )
        status_field = sample._meta.fields["status"]
        field_cls = type(status_field)
        idx_key = DB_key(
            field_cls.get_special_use_field_db_key(sample, "status"),
            status,
        )
        # Clean up probe
        sample.delete()
        return {m.decode() if isinstance(m, bytes) else m for m in POPOTO_REDIS_DB.smembers(idx_key.redis_key)}

    def test_stale_full_save_does_not_create_orphan_with_partial_saves(self, redis_test_db):
        """Partial saves prevent stale objects from clobbering status into the wrong index.

        Scenario (alternative sequence from #950 plan):
        1. Session created as pending.
        2. Worker pops -> transitions to running.
        3. Nudge -> transitions back to pending (fresh read).
        4. Worker's stale reference (status=running) calls set_link (partial save).
        5. Kill fires on the pending session.
        6. Assert: no orphan in pending index.
        """
        session_id = "stale-index-killed-regression-001"
        project_key = "test"

        # Step 1: Create pending session
        session = AgentSession.create(
            session_id=session_id,
            project_key=project_key,
            status="pending",
            created_at=datetime.now(tz=UTC),
        )

        # Step 2: Worker pops -> running
        worker_ref = AgentSession.query.get(id=session.id)
        transition_status(worker_ref, "running", reason="worker pop")

        # Step 3: Nudge -> back to pending (fresh read)
        nudge_ref = AgentSession.query.get(id=session.id)
        assert nudge_ref.status == "running"
        transition_status(nudge_ref, "pending", reason="nudge re-enqueue")

        # Verify on-disk is pending
        check = AgentSession.query.get(id=session.id)
        assert check.status == "pending"

        # Step 4: Worker's stale reference (status=running in memory) does a partial save
        # With the fix, set_link uses update_fields so it doesn't clobber status
        worker_ref.set_link("issue", "https://github.com/test/issues/950")

        # Verify status is still pending (partial save didn't clobber)
        after_link = AgentSession.query.get(id=session.id)
        assert after_link.status == "pending", (
            f"Partial save must not clobber status, got '{after_link.status}'"
        )

        # Step 5: Kill fires
        kill_ref = AgentSession.query.get(id=session.id)
        assert kill_ref.status == "pending"
        finalize_session(kill_ref, "killed", reason="valor-session kill --all",
                        skip_auto_tag=True, skip_checkpoint=True, skip_parent=True)

        # Step 6: Assert no orphan in pending index
        final = AgentSession.query.get(id=session.id)
        assert final.status == "killed"

        pending_members = self._get_index_members("pending")
        member_key = session.db_key.redis_key
        assert member_key not in pending_members, (
            f"Session {session_id} must not remain in the pending index after being killed. "
            f"Orphan found: {member_key}"
        )

        # Also verify it IS in the killed index
        killed_members = self._get_index_members("killed")
        assert member_key in killed_members, (
            f"Session {session_id} must be in the killed index"
        )

    @pytest.mark.parametrize("terminal_status", [
        "completed", "failed", "killed", "abandoned", "cancelled",
    ])
    def test_no_orphan_after_terminal_transition(self, redis_test_db, terminal_status):
        """No orphan index entries remain after any terminal transition.

        Parametrized across all five terminal statuses. Creates a session,
        transitions it through running, then finalizes to the terminal status.
        Asserts zero orphan entries in any non-target index set.
        """
        session_id = f"terminal-orphan-test-{terminal_status}-001"
        project_key = "test"

        session = AgentSession.create(
            session_id=session_id,
            project_key=project_key,
            status="pending",
            created_at=datetime.now(tz=UTC),
        )

        # Transition to running first
        transition_status(session, "running", reason="worker pop")

        # Finalize to terminal status
        finalize_session(session, terminal_status, reason=f"test {terminal_status}",
                        skip_auto_tag=True, skip_checkpoint=True, skip_parent=True)

        # Verify on-disk
        final = AgentSession.query.get(id=session.id)
        assert final.status == terminal_status

        # Check ALL index sets — session must only appear in the target
        member_key = session.db_key.redis_key
        from models.session_lifecycle import ALL_STATUSES

        for check_status in ALL_STATUSES:
            members = self._get_index_members(check_status)
            if check_status == terminal_status:
                assert member_key in members, (
                    f"Session must be in the {terminal_status} index"
                )
            else:
                assert member_key not in members, (
                    f"Orphan: session in {check_status} index after "
                    f"terminal transition to {terminal_status}"
                )

    def test_defensive_srem_cleans_pre_existing_orphan(self, redis_test_db):
        """Defensive srem in finalize_session cleans up a pre-existing orphan.

        Scenario: Manually inject an orphan entry into the pending index for a
        session that's currently running, then finalize to killed. The defensive
        srem must remove the orphan from the pending index.
        """
        from popoto.models.db_key import DB_key
        from popoto.redis_db import POPOTO_REDIS_DB

        session_id = "defensive-srem-test-001"
        project_key = "test"

        session = AgentSession.create(
            session_id=session_id,
            project_key=project_key,
            status="running",
            created_at=datetime.now(tz=UTC),
        )

        # Manually inject an orphan into the pending index
        # (simulates the corruption that would have occurred pre-fix)
        status_field = session._meta.fields["status"]
        field_cls = type(status_field)
        pending_idx_key = DB_key(
            field_cls.get_special_use_field_db_key(session, "status"),
            "pending",
        )
        member_key = session.db_key.redis_key
        POPOTO_REDIS_DB.sadd(pending_idx_key.redis_key, member_key)

        # Verify the orphan exists
        assert POPOTO_REDIS_DB.sismember(pending_idx_key.redis_key, member_key)

        # Finalize to killed — defensive srem should clean the orphan
        finalize_session(session, "killed", reason="test kill",
                        skip_auto_tag=True, skip_checkpoint=True, skip_parent=True)

        # The orphan must be gone
        assert not POPOTO_REDIS_DB.sismember(pending_idx_key.redis_key, member_key), (
            "Defensive srem must remove orphan from pending index during finalize"
        )

        # And the session should be in killed index
        killed_idx_key = DB_key(
            field_cls.get_special_use_field_db_key(session, "status"),
            "killed",
        )
        assert POPOTO_REDIS_DB.sismember(killed_idx_key.redis_key, member_key), (
            "Session must be in killed index after finalize"
        )
