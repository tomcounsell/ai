"""Tests for AgentSession status index corruption fixes.

Validates that status transitions on lazy-loaded sessions correctly remove the
old index entry from Redis, preventing ghost "running" entries.

Root cause (Bug 1): Popoto's _create_lazy_model() initialises _saved_field_values
with only KeyFields populated. IndexedFieldMixin.on_save() skips the srem() call
when the old value is not present in _saved_field_values. The fix backfills
_saved_field_values["status"] in both transition_status() and finalize_session()
before calling session.save().
"""

from unittest.mock import MagicMock, patch

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

    Note: The defensive srem code uses local imports (`from popoto.redis_db import ...`)
    inside a try/except block. Unit tests verify the code exists and is non-fatal.
    Full index-level verification is in the integration tests (test_nudge_stomp_regression.py).
    """

    def test_defensive_srem_code_exists_in_finalize(self):
        """Verify the defensive srem code is present in finalize_session source."""
        import inspect

        from models.session_lifecycle import finalize_session

        source = inspect.getsource(finalize_session)
        assert "srem" in source, "finalize_session must contain defensive srem code"
        assert "ALL_STATUSES" in source, "finalize_session must iterate ALL_STATUSES"
        assert "Defensive srem" in source, "finalize_session must have defensive srem comment"

    @pytest.mark.parametrize(
        "terminal_status",
        ["completed", "failed", "killed", "abandoned", "cancelled"],
    )
    def test_finalize_completes_for_all_terminal_statuses(self, terminal_status):
        """finalize_session completes (including defensive srem) for all terminal statuses."""
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="running")

        with (
            patch.object(session, "log_lifecycle_transition"),
            patch("models.session_lifecycle._finalize_parent_sync"),
        ):
            # Should not raise — defensive srem may fail on mocked objects
            # but the try/except makes it non-fatal
            finalize_session(
                session,
                terminal_status,
                reason="test",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

        assert session.status == terminal_status
        session.save.assert_called_once()

    def test_defensive_srem_failure_is_nonfatal(self):
        """Defensive srem failure must not crash finalize_session."""
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="running")

        with (
            patch.object(session, "log_lifecycle_transition"),
            patch("models.session_lifecycle._finalize_parent_sync"),
        ):
            # Should not raise despite mock objects lacking real Popoto attrs
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
        assert "update_fields" in call_kwargs, "set_link must use partial save (update_fields)"
        assert "issue_url" in call_kwargs["update_fields"]
        assert "updated_at" in call_kwargs["update_fields"]
        # status must NOT be in update_fields
        assert "status" not in call_kwargs["update_fields"], (
            "set_link must not include 'status' in update_fields"
        )

    # push_steering_message/pop_steering_messages partial-save coverage was
    # removed here — issue #1817 A1 deleted both AgentSession instance
    # methods (and the queued_steering_messages ListField) in favor of the
    # Redis-list primitive in agent/steering.py, which has no stale-save
    # race to guard against (RPUSH/LPOP are independent of AgentSession.save()).


# ---------------------------------------------------------------------------
# Killed transition path tests (#950)
# ---------------------------------------------------------------------------


class TestKilledTransitionPathBackfill:
    """The killed transition path must backfill _saved_field_values['status']
    so that Popoto's on_save() srem targets the correct old index set.

    Prior to #950, only the completed transition was tested. The killed path
    exercises the same backfill logic in finalize_session but through a
    different caller (valor-session kill vs worker completion).
    """

    def test_backfills_for_pending_to_killed(self):
        """finalize_session backfills for pending -> killed transition.

        This is the exact path that caused the #950 regression: a session
        that was pending when killed must have srem(pending) fire correctly.
        """
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="pending")

        with (
            patch.object(session, "log_lifecycle_transition"),
            patch("models.session_lifecycle._finalize_parent_sync"),
        ):
            finalize_session(
                session,
                "killed",
                reason="valor-session kill --all",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

        # The key assertion: _saved_field_values["status"] must be set to "pending"
        # (the old status) before save() is called, so on_save() fires srem(pending).
        assert session._saved_field_values["status"] == "pending"
        session.save.assert_called_once()
        assert session.status == "killed"

    @pytest.mark.parametrize("old_status", ["pending", "running", "active", "dormant"])
    def test_backfills_from_any_non_terminal_to_killed(self, old_status):
        """finalize_session backfills correctly from any non-terminal status to killed."""
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status=old_status)

        with (
            patch.object(session, "log_lifecycle_transition"),
            patch("models.session_lifecycle._finalize_parent_sync"),
        ):
            finalize_session(
                session,
                "killed",
                reason="test kill",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

        assert session._saved_field_values["status"] == old_status
        assert session.status == "killed"
        session.save.assert_called_once()


# ---------------------------------------------------------------------------
# waiting_for_children → terminal status: real-Popoto coverage (issue #1361)
# ---------------------------------------------------------------------------
#
# Existing tests above operate on MagicMock sessions and validate the
# _saved_field_values backfill mechanism. The acceptance criterion in #1361
# requires real Popoto-backed coverage for the `waiting_for_children` index:
# after exiting that status (via finalize_session OR transition_status), the
# `$IndexF:AgentSession:status:waiting_for_children` set must NOT contain
# the session's redis_key.
#
# These tests follow the real-Popoto pattern from
# tests/unit/test_session_health_phantom_guard.py (TestCleanupCorruptedAgentSessions).
# They construct real AgentSession instances, call .save(), then exercise the
# lifecycle module against the live Redis indexes.


class TestWaitingForChildrenExitTransition:
    """Real-Popoto coverage for `waiting_for_children` index hygiene (#1361)."""

    def test_finalize_session_clears_waiting_for_children_index(self):
        """finalize_session(s, "killed") removes s from the waiting_for_children index."""
        from popoto.redis_db import POPOTO_REDIS_DB

        from models.agent_session import AgentSession
        from models.session_lifecycle import finalize_session, transition_status

        s = AgentSession(
            session_id="wfc-finalize-1361",
            project_key="test-1361",
            status="pending",
        )
        s.save()
        transition_status(s, "waiting_for_children")
        s_key = s._redis_key

        # Sanity: after the transition, s IS a member of the
        # waiting_for_children index.
        members = POPOTO_REDIS_DB.smembers("$IndexF:AgentSession:status:waiting_for_children")
        member_strs = {m.decode() if isinstance(m, bytes) else m for m in members}
        assert s_key in member_strs, (
            f"Pre-condition failed: {s_key} not in waiting_for_children index. "
            f"Got members: {member_strs}"
        )

        # Exit via finalize_session.
        finalize_session(
            s,
            "killed",
            reason="test #1361",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )

        # Assertion: index is clean.
        members = POPOTO_REDIS_DB.smembers("$IndexF:AgentSession:status:waiting_for_children")
        member_strs = {m.decode() if isinstance(m, bytes) else m for m in members}
        assert s_key not in member_strs, (
            f"Stale member: {s_key} should NOT be in waiting_for_children index "
            f"after finalize_session(s, 'killed'). Got: {member_strs}"
        )

    def test_transition_to_completed_clears_waiting_for_children_index(self):
        """transition_status to a non-finalize path also clears the index member."""
        from popoto.redis_db import POPOTO_REDIS_DB

        from models.agent_session import AgentSession
        from models.session_lifecycle import transition_status

        s = AgentSession(
            session_id="wfc-transition-1361",
            project_key="test-1361",
            status="pending",
        )
        s.save()
        transition_status(s, "waiting_for_children")
        s_key = s._redis_key

        # Move out of waiting_for_children to running (a non-terminal,
        # non-finalize transition path).
        transition_status(s, "running")

        members = POPOTO_REDIS_DB.smembers("$IndexF:AgentSession:status:waiting_for_children")
        member_strs = {m.decode() if isinstance(m, bytes) else m for m in members}
        assert s_key not in member_strs, (
            f"Stale member: {s_key} should NOT be in waiting_for_children index "
            f"after transition_status(s, 'running'). Got: {member_strs}"
        )

    @pytest.mark.parametrize(
        "terminal_status",
        ["completed", "failed", "killed", "abandoned", "cancelled"],
    )
    def test_finalize_from_waiting_for_children_to_each_terminal(self, terminal_status):
        """Every terminal exit from waiting_for_children clears the index."""
        from popoto.redis_db import POPOTO_REDIS_DB

        from models.agent_session import AgentSession
        from models.session_lifecycle import finalize_session, transition_status

        s = AgentSession(
            session_id=f"wfc-{terminal_status}-1361",
            project_key="test-1361",
            status="pending",
        )
        s.save()
        transition_status(s, "waiting_for_children")
        s_key = s._redis_key

        finalize_session(
            s,
            terminal_status,
            reason=f"test #1361 {terminal_status}",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )

        members = POPOTO_REDIS_DB.smembers("$IndexF:AgentSession:status:waiting_for_children")
        member_strs = {m.decode() if isinstance(m, bytes) else m for m in members}
        assert s_key not in member_strs, (
            f"Stale waiting_for_children member after finalize to {terminal_status}: {member_strs}"
        )


# ---------------------------------------------------------------------------
# B1/B2 shared-disposition structural guard (#2083)
# ---------------------------------------------------------------------------


class TestBackfillSitesShareDisposition:
    """The two `_saved_field_values["status"]` backfills — B1 in finalize_session
    and B2 in transition_status — share a single disposition (#2083 ledger).

    This structural guard fails if either backfill site is removed without the
    other: exactly the half-removal defect the #2083 plan critique flagged. It is
    the committed enforcement referenced by the ledger's B1/B2 KEEP verdict.
    """

    def test_both_backfill_sites_move_together(self):
        """B1 (finalize_session) and B2 (transition_status) backfills must co-exist."""
        import inspect

        from models.session_lifecycle import finalize_session, transition_status

        needle = '_saved_field_values["status"] = current_status'
        b1_present = needle in inspect.getsource(finalize_session)
        b2_present = needle in inspect.getsource(transition_status)

        assert b1_present == b2_present, (
            "B1 (finalize_session) and B2 (transition_status) status backfills must "
            f"share a disposition; found B1={b1_present} B2={b2_present}. Removing one "
            "without the other is the half-removal defect flagged in the #2083 critique."
        )
        # Under the current #2083 KEEP-all verdict, both sites must be present.
        assert b1_present and b2_present, (
            "Both backfill sites must be present under the #2083 KEEP verdict; "
            "a post-migration removal must delete both sites and this test together."
        )


# ---------------------------------------------------------------------------
# #950 stale-object full-save red-state — B3 defensive srem (#2083)
# ---------------------------------------------------------------------------


class TestStaleFullSaveRedState950:
    """#950 stale-object full-save red-state: proves the B3 defensive srem is the
    sole path that scrubs a compound-legacy orphan out of a wrong status index set.

    Each arm runs WITH B3 (real client-side srem) and WITHOUT it (client-side srem
    monkeypatched to a no-op — Popoto 1.8.0's server-side Lua index swap is left
    intact, isolating B3's contribution exactly as the #2083 ledger's empirical
    repro did):

      * steady-state arm: a cleanly-tracked row finalized to a terminal status
        lands in exactly the target set WITH and WITHOUT B3 — B3 makes no
        observable difference (the ledger's "B3 makes no observable difference"
        finding).
      * compound-legacy arm: a row carrying a pre-seeded orphan in an unrelated
        status set (as a stale full-save that clobbered status, or historical
        index drift, would leave — one Popoto's on_save Lua cannot know about)
        is scrubbed clean only WITH B3; WITHOUT B3 the orphan strands.

    Guards the #2083 B3 KEEP verdict: if a future change neuters B3, the
    compound-legacy WITH/WITHOUT arms converge and this test fails.
    """

    _PROJECT = "test-2083-950"

    @staticmethod
    def _members(status):
        from popoto.redis_db import POPOTO_REDIS_DB

        members = POPOTO_REDIS_DB.smembers(f"$IndexF:AgentSession:status:{status}")
        return {m.decode() if isinstance(m, bytes) else m for m in members}

    @staticmethod
    def _finalize(session, terminal, *, disable_b3):
        from unittest.mock import patch as _patch

        from popoto.redis_db import POPOTO_REDIS_DB

        from models.session_lifecycle import finalize_session

        kwargs = dict(
            reason="#950 red-state",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )
        if disable_b3:
            # Neuter ONLY the client-side defensive srem (B3). Popoto's on_save
            # index swap runs server-side via Lua eval, untouched by this patch.
            with _patch.object(POPOTO_REDIS_DB, "srem", return_value=0):
                finalize_session(session, terminal, **kwargs)
        else:
            finalize_session(session, terminal, **kwargs)

    @pytest.mark.parametrize("disable_b3", [False, True])
    def test_steady_state_no_stranding(self, disable_b3):
        """Clean row: running -> completed lands only in `completed`, B3 irrelevant."""
        from models.agent_session import AgentSession

        s = AgentSession(
            session_id=f"redstate-steady-{disable_b3}",
            project_key=self._PROJECT,
            status="running",
        )
        s.save()
        s_key = s._redis_key
        try:
            assert s_key in self._members("running")

            self._finalize(s, "completed", disable_b3=disable_b3)

            # Clean swap in BOTH arms: Popoto's Lua on_save handles running->completed.
            assert s_key in self._members("completed")
            assert s_key not in self._members("running")
        finally:
            s.delete()

    def test_compound_legacy_orphan_scrubbed_only_with_b3(self):
        """Compound-legacy orphan in an unrelated set is scrubbed only WITH B3."""
        from popoto.redis_db import POPOTO_REDIS_DB

        from models.agent_session import AgentSession

        # --- WITH B3: the orphan is scrubbed. ---
        s = AgentSession(
            session_id="redstate-legacy-withb3",
            project_key=self._PROJECT,
            status="running",
        )
        s.save()
        s_key = s._redis_key
        # Seed a compound-legacy orphan: strand the member in an UNRELATED status
        # set. Popoto's on_save Lua only swaps the tracked old->new set and cannot
        # know about this; only B3's ALL_STATUSES sweep can remove it.
        POPOTO_REDIS_DB.sadd("$IndexF:AgentSession:status:dormant", s_key)
        try:
            assert s_key in self._members("dormant")

            self._finalize(s, "completed", disable_b3=False)

            assert s_key not in self._members("dormant"), (
                "B3 must scrub the compound-legacy orphan out of the `dormant` set"
            )
        finally:
            POPOTO_REDIS_DB.srem("$IndexF:AgentSession:status:dormant", s_key)
            s.delete()

        # --- WITHOUT B3: the orphan strands (the red state #950 documents). ---
        s2 = AgentSession(
            session_id="redstate-legacy-nob3",
            project_key=self._PROJECT,
            status="running",
        )
        s2.save()
        s2_key = s2._redis_key
        POPOTO_REDIS_DB.sadd("$IndexF:AgentSession:status:dormant", s2_key)
        try:
            self._finalize(s2, "completed", disable_b3=True)

            assert s2_key in self._members("dormant"), (
                "Red state: WITHOUT B3 the compound-legacy orphan strands in `dormant` "
                "— this is the #950 failure B3 exists to prevent"
            )
        finally:
            # Scrub the deliberately-stranded orphan we seeded for the red arm.
            POPOTO_REDIS_DB.srem("$IndexF:AgentSession:status:dormant", s2_key)
            s2.delete()
