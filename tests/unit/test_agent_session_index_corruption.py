"""Tests for AgentSession status index corruption fixes.

Validates that status transitions on lazy-loaded sessions correctly remove the
old index entry from Redis, preventing ghost "running" entries.

Root cause (Bug 1): Popoto's _create_lazy_model() initialises _saved_field_values
with only KeyFields populated. IndexedFieldMixin.on_save() skips the srem() call
when the old value is not present in _saved_field_values. The fix backfills
_saved_field_values["status"] in both transition_status() and finalize_session()
before calling session.save().
"""

from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lazy_session(status="running"):
    """Simulate a lazy-loaded AgentSession with minimal _saved_field_values.

    Popoto's _create_lazy_model seeds _saved_field_values with KeyFields only
    (e.g., agent_session_id). Status is an IndexedField and is NOT in
    _saved_field_values after lazy load.
    """
    session = MagicMock()
    session.status = status
    session.session_id = "sess-lazy-001"
    session.agent_session_id = "ase-lazy-001"
    session.parent_agent_session_id = None
    # Simulate lazy-loaded state: only key field is seeded, not status
    session._saved_field_values = {"agent_session_id": "ase-lazy-001"}
    return session


def _make_fully_loaded_session(status="running"):
    """Simulate a fully-loaded AgentSession with all field values populated.

    This represents a session that was saved/loaded normally — _saved_field_values
    includes the current status, so on_save() would already work correctly.
    """
    session = MagicMock()
    session.status = status
    session.session_id = "sess-full-001"
    session.agent_session_id = "ase-full-001"
    session.parent_agent_session_id = None
    # Fully-loaded: _saved_field_values includes status
    session._saved_field_values = {"agent_session_id": "ase-full-001", "status": status}
    return session


# ---------------------------------------------------------------------------
# transition_status — lazy-load backfill
# ---------------------------------------------------------------------------


class TestTransitionStatusLazyLoadBackfill:
    def test_backfills_saved_field_values_on_lazy_session(self):
        """transition_status() must set _saved_field_values['status'] before save().

        This is the core Bug 1 fix: ensures Popoto's on_save() guard can remove
        the old index entry even when the session was lazy-loaded.
        """
        from models.session_lifecycle import transition_status

        session = _make_lazy_session(status="running")

        transition_status(session, "dormant")

        # _saved_field_values["status"] must be set to the OLD status before save
        assert session._saved_field_values["status"] == "running"
        session.save.assert_called_once()

    def test_backfills_even_when_current_status_is_pending(self):
        """Backfill works for pending -> running transition too."""
        from models.session_lifecycle import transition_status

        session = _make_lazy_session(status="pending")

        transition_status(session, "running")

        assert session._saved_field_values["status"] == "pending"
        session.save.assert_called_once()

    def test_does_not_crash_when_no_saved_field_values_attr(self):
        """If session has no _saved_field_values attribute, backfill is skipped silently."""
        from models.session_lifecycle import transition_status

        session = MagicMock()
        session.status = "pending"
        session.session_id = "sess-no-attr"
        session.agent_session_id = "ase-no-attr"
        session.parent_agent_session_id = None
        # Simulate session object without _saved_field_values (e.g., future Popoto change)
        del session._saved_field_values

        # Should not raise
        transition_status(session, "running")
        session.save.assert_called_once()

    def test_fully_loaded_session_backfill_is_still_correct(self):
        """For fully-loaded sessions, backfill sets _saved_field_values to old status.

        Overwriting with the same value is harmless — it's idempotent.
        """
        from models.session_lifecycle import transition_status

        session = _make_fully_loaded_session(status="pending")

        transition_status(session, "running")

        assert session._saved_field_values["status"] == "pending"
        session.save.assert_called_once()

    def test_idempotent_saves_companion_fields_when_already_in_target_status(self):
        """transition_status() saves companion fields even when already in the target state.

        Changed in #875: the idempotent path now calls save() to persist any
        companion fields the caller may have set before calling transition_status().
        """
        from models.session_lifecycle import transition_status

        session = _make_lazy_session(status="running")

        transition_status(session, "running")

        session.save.assert_called_once()


# ---------------------------------------------------------------------------
# finalize_session — lazy-load backfill
# ---------------------------------------------------------------------------


class TestFinalizeSessionLazyLoadBackfill:
    def test_backfills_saved_field_values_on_lazy_session(self):
        """finalize_session() must set _saved_field_values['status'] before save().

        Both transition_status() and finalize_session() use session.save(), so
        both need the backfill to ensure the old index entry is removed.
        """
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="running")

        with (
            patch.object(session, "log_lifecycle_transition"),
            patch("models.session_lifecycle._finalize_parent_sync"),
        ):
            finalize_session(
                session,
                "killed",
                reason="CLI kill",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

        assert session._saved_field_values["status"] == "running"
        session.save.assert_called_once()

    def test_backfills_for_abandoned_transition(self):
        """Backfill works when finalizing as abandoned."""
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="running")

        with patch.object(session, "log_lifecycle_transition"):
            finalize_session(
                session,
                "abandoned",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

        assert session._saved_field_values["status"] == "running"
        session.save.assert_called_once()

    def test_idempotent_noop_when_already_in_terminal_state(self):
        """finalize_session() is a no-op when session is already in that terminal state."""
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="killed")

        finalize_session(session, "killed", skip_auto_tag=True, skip_checkpoint=True)

        session.save.assert_not_called()

    def test_does_not_crash_when_no_saved_field_values_attr(self):
        """If session has no _saved_field_values attribute, backfill is skipped silently."""
        from models.session_lifecycle import finalize_session

        session = MagicMock()
        session.status = "running"
        session.session_id = "sess-no-attr"
        session.agent_session_id = "ase-no-attr"
        session.parent_agent_session_id = None
        del session._saved_field_values

        finalize_session(
            session,
            "killed",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )
        session.save.assert_called_once()


# ---------------------------------------------------------------------------
# Defensive srem in finalize_session (#950)
# ---------------------------------------------------------------------------


class TestDefensiveSremInFinalize:
    """finalize_session must clean orphan index entries via defensive srem (#950).

    After session.save(), finalize_session calls srem on every status index set
    except the target terminal status. This cleans up orphans left by stale-object
    full saves that clobbered status before the finalize ran.
    """

    @pytest.mark.parametrize(
        "terminal_status",
        ["completed", "failed", "killed", "abandoned", "cancelled"],
    )
    def test_defensive_srem_fires_for_all_non_target_statuses(self, terminal_status):
        """Defensive srem removes session from all non-target status index sets."""
        from models.session_lifecycle import ALL_STATUSES, finalize_session

        session = _make_lazy_session(status="running")

        mock_redis = MagicMock()
        mock_db_key_cls = MagicMock()

        with (
            patch.object(session, "log_lifecycle_transition"),
            patch("models.session_lifecycle._finalize_parent_sync"),
            patch("models.session_lifecycle.POPOTO_REDIS_DB", mock_redis, create=True),
            patch("models.session_lifecycle.DB_key", mock_db_key_cls, create=True),
        ):
            finalize_session(
                session,
                terminal_status,
                reason="test",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

        # srem should be called for every status EXCEPT the target terminal status
        expected_srem_count = len(ALL_STATUSES) - 1
        assert mock_redis.srem.call_count == expected_srem_count, (
            f"Expected {expected_srem_count} srem calls for terminal_status={terminal_status}, "
            f"got {mock_redis.srem.call_count}"
        )

    def test_defensive_srem_failure_is_nonfatal(self):
        """Defensive srem failure must not crash finalize_session."""
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="running")

        with (
            patch.object(session, "log_lifecycle_transition"),
            patch("models.session_lifecycle._finalize_parent_sync"),
            patch(
                "models.session_lifecycle.POPOTO_REDIS_DB",
                side_effect=ImportError("Redis unavailable"),
                create=True,
            ),
        ):
            # Should not raise despite import error
            finalize_session(
                session,
                "killed",
                reason="test",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

        assert session.status == "killed"
        session.save.assert_called_once()


# ---------------------------------------------------------------------------
# Stale-object full save then kill — orphan prevention (#950)
# ---------------------------------------------------------------------------


class TestStaleSaveThenKillOrphanPrevention:
    """Validates the full attack sequence from #950: stale save clobbers status,
    then kill fires and leaves an orphan in the old status index set.

    With the fix (partial saves), the stale save never writes status, so the
    kill path's srem targets the correct old value and no orphan is created.
    """

    def test_partial_save_on_set_link_does_not_write_status(self):
        """set_link partial save must only write the link field + updated_at."""
        from models.agent_session import AgentSession

        session = MagicMock(spec=AgentSession)
        session.session_id = "stale-save-test-001"
        session.issue_url = None
        session.plan_url = None
        session.pr_url = None
        session.status = "running"

        # Call the real set_link method
        AgentSession.set_link(session, "issue", "https://example.com/issue/1")

        session.save.assert_called_once()
        call_kwargs = session.save.call_args.kwargs
        assert "update_fields" in call_kwargs, (
            "set_link must use partial save (update_fields)"
        )
        assert "issue_url" in call_kwargs["update_fields"]
        assert "updated_at" in call_kwargs["update_fields"]
        # status must NOT be in update_fields
        assert "status" not in call_kwargs["update_fields"], (
            "set_link must not include 'status' in update_fields"
        )

    def test_partial_save_on_push_steering_does_not_write_status(self):
        """push_steering_message partial save must only write queued_steering_messages + updated_at."""
        from models.agent_session import AgentSession

        session = MagicMock(spec=AgentSession)
        session.session_id = "stale-save-test-002"
        session.queued_steering_messages = []
        session.status = "running"

        AgentSession.push_steering_message(session, "test message")

        session.save.assert_called_once()
        call_kwargs = session.save.call_args.kwargs
        assert "update_fields" in call_kwargs
        assert "queued_steering_messages" in call_kwargs["update_fields"]
        assert "status" not in call_kwargs["update_fields"]

    def test_partial_save_on_pop_steering_does_not_write_status(self):
        """pop_steering_messages partial save must only write queued_steering_messages + updated_at."""
        from models.agent_session import AgentSession

        session = MagicMock(spec=AgentSession)
        session.session_id = "stale-save-test-003"
        session.queued_steering_messages = ["msg1", "msg2"]
        session.status = "running"

        messages = AgentSession.pop_steering_messages(session)

        assert messages == ["msg1", "msg2"]
        session.save.assert_called_once()
        call_kwargs = session.save.call_args.kwargs
        assert "update_fields" in call_kwargs
        assert "queued_steering_messages" in call_kwargs["update_fields"]
        assert "status" not in call_kwargs["update_fields"]
