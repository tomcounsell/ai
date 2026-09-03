"""Unit tests for tools.sdlc_session_ensure: run-id reuse and supervised-run identity (#2879)."""

import json
from unittest.mock import MagicMock, patch


class TestVerifiedRunIdReuse:
    """#2003 cycle-3 BLOCKER 1: the per-stage /sdlc router re-runs
    session-ensure at every stage boundary while its OWN prior stage's lock
    is still live (the stage's completion marker renews it to the full TTL).
    A bare re-ensure mints a fresh candidate, loses SET NX to itself, and
    self-wedges the pipeline. --reuse-run-id is the escape: a claim the
    caller already carries is verified against the live lock (owner match)
    or, on a free lock, against the record mirror -- and only then honored.
    No-adopt stays intact for foreign/stale claims.
    """

    @staticmethod
    def _readback_as(session):
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [session]
        mock_as.query.get.return_value = session  # post-save readback (primary-key lookup)
        return mock_as

    def test_consecutive_stage_reuse_survives_own_live_lock(self):
        """The judge-mandated regression: ensure -> stage-completion renewal
        -> second ensure WITHIN the TTL. With --reuse-run-id the second
        ensure returns the SAME run_id instead of wedging on ISSUE_LOCKED.
        Real Redis lock throughout."""
        from tools._sdlc_utils import renew_issue_lock_for_session
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2060
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"
        session.issue_number = issue_number

        # Stage N: first ensure mints run_id A.
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_a = ensure_session(issue_number=issue_number)
        run_id_a = result_a["run_id"]
        assert run_id_a

        # Stage N's final `stage-marker --status completed` renews the lock
        # to the full TTL (the exact write_marker side effect).
        renew_issue_lock_for_session(session, run_id=run_id_a)

        # Stage N+1: the router re-ensures seconds later, carrying the
        # conversation's run_id. Must NOT wedge; must return the same id.
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_b = ensure_session(issue_number=issue_number, reuse_run_id=run_id_a)

        assert result_b.get("blocked") is None, result_b
        assert result_b["run_id"] == run_id_a
        assert result_b["session_id"] == f"sdlc-local-{issue_number}"

    def test_reuse_with_wrong_id_against_live_lock_still_blocked(self):
        """An unverifiable claim while a foreign lock is live falls through
        to the fresh-mint contest and stays ISSUE_LOCKED (no adopt)."""
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2061
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_a = ensure_session(issue_number=issue_number)
        run_id_a = result_a["run_id"]

        intruder = MagicMock()
        intruder.session_id = f"sdlc-local-{issue_number}"
        intruder.active_run_id = None
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=intruder),
            patch("models.agent_session.AgentSession", self._readback_as(intruder)),
        ):
            result_b = ensure_session(issue_number=issue_number, reuse_run_id="bogus-claim")

        assert result_b["blocked"] is True
        assert result_b["reason"] == "ISSUE_LOCKED"
        assert result_b["owner_run_id"] == run_id_a
        assert "orphaned_lock" in result_b

    def test_reuse_on_free_lock_with_record_match_reacquires_same_id(self):
        """TTL lapsed but the record mirror corroborates the claim: the
        ensure re-acquires under the SAME run_id (lossless recovery)."""
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2062
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"
        session.active_run_id = "aabbccdd" * 4  # prior mint, mirrored on the record

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result = ensure_session(issue_number=issue_number, reuse_run_id="aabbccdd" * 4)

        assert result["run_id"] == "aabbccdd" * 4

    def test_reuse_on_free_lock_with_record_mismatch_mints_fresh(self):
        """A claim the record does NOT corroborate is ignored on a free
        lock: fresh mint, never claim-echo."""
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2063
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"
        session.active_run_id = "11112222" * 4

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result = ensure_session(issue_number=issue_number, reuse_run_id="deadbeef" * 4)

        assert result["run_id"] != "deadbeef" * 4
        assert len(result["run_id"]) == 32


class TestSupervisedRunSignal:
    """WS1 (#2026): the supervised-run signal drives fork inheritance.

    A bare ``session-ensure`` under a LIVE supervised-run signal returns the
    named ``SUPERVISED_RUN_ACTIVE`` refusal (carrying the supervisor's run_id)
    and mints NOTHING. A stale/expired signal falls back to normal standalone
    mint semantics. Enforcement lives in the tool, not prose (Risk 3).
    """

    @staticmethod
    def _readback_as(session):
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [session]
        mock_as.query.get.return_value = session  # post-save readback (primary-key lookup)
        return mock_as

    def test_bare_ensure_under_live_signal_refuses_and_mints_nothing(self):
        """A live signal short-circuits the bare ensure to SUPERVISED_RUN_ACTIVE
        before any lock contest or mint."""
        from agent.supervised_run import SupervisedRunStatus
        from tools.sdlc_session_ensure import ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2070"
        session.working_dir = None

        live = SupervisedRunStatus(True, "supervisor-run-abc", "sdlc-local-2070")

        lock_mock = MagicMock()
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
            patch("agent.supervised_run.supervised_run_status", return_value=live),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2070)

        assert result["blocked"] is True
        assert result["reason"] == "SUPERVISED_RUN_ACTIVE"
        assert result["run_id"] == "supervisor-run-abc"
        assert result["owner_run_id"] == "supervisor-run-abc"
        assert result.get("created") is None
        # Mints nothing: the lock is never contested.
        lock_mock.assert_not_called()
        # No run_id bound onto the record.
        assert getattr(session, "active_run_id", None) in (None,) or not isinstance(
            session.active_run_id, str
        )

    def test_bare_ensure_under_stale_signal_falls_back_to_standalone(self):
        """A stale/expired signal (not live) never refuses -- the bare ensure
        mints fresh via the normal lock contest."""
        from agent.supervised_run import SupervisedRunStatus
        from tools.sdlc_session_ensure import ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2071"
        session.working_dir = None

        stale = SupervisedRunStatus(False, "dead-run", None)

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
            patch("agent.supervised_run.supervised_run_status", return_value=stale),
        ):
            result = ensure_session(issue_number=2071)

        assert result.get("blocked") is None
        assert result["run_id"]
        assert len(result["run_id"]) == 32
        assert session.active_run_id == result["run_id"]

    def test_reuse_ensure_is_exempt_from_signal_refusal(self):
        """A --reuse-run-id ensure is the supervisor's own consecutive-stage
        re-ensure: it skips the signal refusal entirely (verified against the
        live lock further down instead)."""
        from agent.supervised_run import SupervisedRunStatus
        from tools.sdlc_session_ensure import ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2072"
        session.working_dir = None
        session.active_run_id = "aa11bb22" * 4

        live = SupervisedRunStatus(True, "aa11bb22" * 4, "sdlc-local-2072")
        status_mock = MagicMock(return_value=live)

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
            patch("agent.supervised_run.supervised_run_status", status_mock),
        ):
            result = ensure_session(issue_number=2072, reuse_run_id="aa11bb22" * 4)

        # The reuse path never consults the supervised-run signal.
        status_mock.assert_not_called()
        assert result.get("reason") != "SUPERVISED_RUN_ACTIVE"


class TestSupervisedRunModule:
    """Direct tests of agent.supervised_run against the test Redis db."""

    def test_status_live_when_lock_held_by_signal_run_id(self):
        from agent.supervised_run import (
            supervised_run_status,
            write_supervised_run_signal,
        )
        from models.session_lifecycle import touch_issue_lock

        issue_number = 2080
        run_id = "runsig-2080"
        # Supervisor holds the lease under run_id, then publishes the signal.
        assert touch_issue_lock(issue_number, run_id, session_id="s").acquired is True
        write_supervised_run_signal(issue_number, run_id, session_id="s")

        status = supervised_run_status(issue_number)
        assert status.live is True
        assert status.run_id == run_id

    def test_status_stale_when_lock_released(self):
        from agent.supervised_run import (
            supervised_run_status,
            write_supervised_run_signal,
        )
        from models.session_lifecycle import release_issue_lock, touch_issue_lock

        issue_number = 2081
        run_id = "runsig-2081"
        touch_issue_lock(issue_number, run_id, session_id="s")
        write_supervised_run_signal(issue_number, run_id, session_id="s")
        # Supervisor releases the lease at run end: the signal goes stale even
        # though its key may still exist until its own TTL lapses.
        release_issue_lock(issue_number, run_id)

        status = supervised_run_status(issue_number)
        assert status.live is False

    def test_status_stale_when_lock_owned_by_different_run(self):
        from agent.supervised_run import (
            supervised_run_status,
            write_supervised_run_signal,
        )
        from models.session_lifecycle import touch_issue_lock

        issue_number = 2082
        # A stale signal names run A, but the lock is now held by run B.
        write_supervised_run_signal(issue_number, "old-run-A", session_id="s")
        touch_issue_lock(issue_number, "new-run-B", session_id="s")

        status = supervised_run_status(issue_number)
        assert status.live is False

    def test_no_signal_returns_not_live(self):
        from agent.supervised_run import supervised_run_status

        status = supervised_run_status(2083)
        assert status.live is False
        assert status.run_id is None

    def test_clear_signal_is_compare_and_delete(self):
        from agent.supervised_run import (
            clear_supervised_run_signal,
            read_supervised_run_signal,
            write_supervised_run_signal,
        )

        issue_number = 2084
        write_supervised_run_signal(issue_number, "owner-run", session_id="s")
        # A foreign run_id must not clear the signal.
        clear_supervised_run_signal(issue_number, "foreign-run")
        assert read_supervised_run_signal(issue_number) is not None
        # The owner clears it.
        clear_supervised_run_signal(issue_number, "owner-run")
        assert read_supervised_run_signal(issue_number) is None

    def test_heartbeat_restores_a_signal_that_lapsed_under_a_live_lease(self):
        """Issue #2659 acceptance: a pipeline outliving the signal TTL still
        lets its stage forks inherit.

        This is the wedge that was observed twice on 2026-08-07 (#2642, #2629).
        The signal shares the lease's 1800s TTL but was written only at
        acquire, while ``tools/sdlc_lease_heartbeat.py`` renewed the lease
        forever. Thirty minutes in, the signal lapsed under a live lease and
        every stage fork the supervisor dispatched got a bare ``ISSUE_LOCKED``
        from its own supervisor's lock -- and stood down, correctly, per the
        substrate-probe rule. Live lock, live heartbeat, no error anywhere.

        ``clear_supervised_run_signal`` stands in for the TTL lapsing: both
        leave a held lease with no signal, which is the state that matters.
        """
        from agent.supervised_run import (
            clear_supervised_run_signal,
            supervised_run_status,
            write_supervised_run_signal,
        )
        from models.session_lifecycle import touch_issue_lock
        from tools.sdlc_lease_heartbeat import run_heartbeat

        issue_number = 2659
        run_id = "runsig-2659"

        # Supervisor acquires the lease and publishes the signal.
        assert touch_issue_lock(issue_number, run_id, session_id="s").acquired is True
        write_supervised_run_signal(issue_number, run_id, session_id="s")
        assert supervised_run_status(issue_number).live is True

        # 30 minutes pass: the signal lapses, the lease does not.
        clear_supervised_run_signal(issue_number, run_id)
        assert supervised_run_status(issue_number).live is False  # the wedge

        # One heartbeat tick against the still-held lease.
        ticks = iter([0.0, 1.0, 999.0])
        rc = run_heartbeat(
            issue_number=issue_number,
            run_id=run_id,
            session_id="s",
            interval=1,
            max_lifetime=2,
            _sleep=lambda s: None,
            _monotonic=lambda: next(ticks),
        )

        assert rc == 0
        # Inheritance is restored: a stage fork now reads SUPERVISED_RUN_ACTIVE.
        status = supervised_run_status(issue_number)
        assert status.live is True
        assert status.run_id == run_id

    def test_operations_fail_open_on_redis_error(self):
        """Every op degrades to a safe default (never raises) on Redis error."""
        from agent.supervised_run import (
            read_supervised_run_signal,
            supervised_run_status,
            write_supervised_run_signal,
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.side_effect = RuntimeError("redis down")
            mock_redis.set.side_effect = RuntimeError("redis down")
            # None of these raise.
            write_supervised_run_signal(2085, "run", session_id="s")
            assert read_supervised_run_signal(2085) is None
            assert supervised_run_status(2085).live is False


class TestOwnedRunIdsSelfRecognition:
    """Issue #2446/#2451: ``owned_run_ids`` records every run_id a logical
    supervision run has minted/bound, so self-recognition survives a lease
    lapse + re-mint. The new proof branches only WIDEN what counts as self;
    a genuine foreign run is still refused (no-adopt invariant preserved).
    """

    @staticmethod
    def _readback_as(session):
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [session]
        mock_as.query.get.return_value = session  # post-save readback (primary-key lookup)
        return mock_as

    def test_read_owned_run_ids_tolerant(self):
        """Malformed JSON / non-list / None all resolve to [] -- never raise."""
        from tools.sdlc_session_ensure import _read_owned_run_ids

        s = MagicMock()
        s.owned_run_ids = "{not valid json"
        assert _read_owned_run_ids(s) == []
        s.owned_run_ids = None
        assert _read_owned_run_ids(s) == []
        s.owned_run_ids = json.dumps({"a": 1})  # dict, not list
        assert _read_owned_run_ids(s) == []
        s.owned_run_ids = json.dumps(["a", "b"])
        assert _read_owned_run_ids(s) == ["a", "b"]

    def test_append_dedups_preserves_order_and_caps(self):
        from tools.sdlc_session_ensure import (
            OWNED_RUN_IDS_CAP,
            _append_owned_run_id,
            _read_owned_run_ids,
        )

        s = MagicMock()
        s.owned_run_ids = None
        _append_owned_run_id(s, "a")
        _append_owned_run_id(s, "a")  # dedup
        _append_owned_run_id(s, "b")
        assert _read_owned_run_ids(s) == ["a", "b"]

        for i in range(OWNED_RUN_IDS_CAP + 5):
            _append_owned_run_id(s, f"r{i}")
        got = _read_owned_run_ids(s)
        assert len(got) == OWNED_RUN_IDS_CAP  # bounded
        assert got[-1] == f"r{OWNED_RUN_IDS_CAP + 4}"  # most recent retained
        assert "a" not in got  # oldest dropped

    def test_owned_run_ids_accumulates_on_bind(self):
        """A successful mint records its run_id in owned_run_ids."""
        from tools.sdlc_session_ensure import _read_owned_run_ids, ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2446"
        session.owned_run_ids = None

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result = ensure_session(issue_number=2446)

        run_id = result["run_id"]
        assert run_id
        assert run_id in _read_owned_run_ids(session)

    def test_validated_reuse_recognizes_self_across_remint(self):
        """Live lock owned by a NEWER self id + claim carrying an OLDER self id
        (both in owned_run_ids) -> return the LIVE OWNER id so renewal runs
        under the identity actually holding the lock."""
        from tools.sdlc_session_ensure import _validated_reuse_candidate

        session = MagicMock()
        session.session_id = "sdlc-local-2446"
        session.owned_run_ids = json.dumps(["old-id", "new-id"])

        peek = MagicMock()
        peek.acquired = False  # claim != live owner -> looks foreign
        peek.owner_run_id = "new-id"

        with patch("models.session_lifecycle.touch_issue_lock", return_value=peek):
            got = _validated_reuse_candidate(2446, session, "old-id")
        assert got == "new-id"

    def test_validated_reuse_free_lock_prior_self_id_reacquires(self):
        """Free lock + claim in owned_run_ids (but != active_run_id) ->
        re-acquire under the prior self identity."""
        from tools.sdlc_session_ensure import _validated_reuse_candidate

        session = MagicMock()
        session.session_id = "sdlc-local-2446"
        session.active_run_id = "new-id"
        session.owned_run_ids = json.dumps(["old-id", "new-id"])

        peek = MagicMock()
        peek.acquired = True
        peek.owner_run_id = None  # free lock

        with patch("models.session_lifecycle.touch_issue_lock", return_value=peek):
            got = _validated_reuse_candidate(2446, session, "old-id")
        assert got == "old-id"

    def test_validated_reuse_foreign_owner_not_recognized(self):
        """A live lock owned by an id NOT in owned_run_ids stays foreign ->
        None (no-adopt preserved)."""
        from tools.sdlc_session_ensure import _validated_reuse_candidate

        session = MagicMock()
        session.session_id = "sdlc-local-2446"
        session.owned_run_ids = json.dumps(["mine"])

        peek = MagicMock()
        peek.acquired = False
        peek.owner_run_id = "foreign-run"

        with patch("models.session_lifecycle.touch_issue_lock", return_value=peek):
            got = _validated_reuse_candidate(2446, session, "mine")
        assert got is None

    def test_supervised_self_recognized_inherits_not_refused(self):
        """The #2421 case: a bare ensure under a LIVE supervised signal whose
        run_id is in owned_run_ids inherits/renews and returns a NORMAL success
        payload -- never SUPERVISED_RUN_ACTIVE."""
        from agent.supervised_run import SupervisedRunStatus
        from tools.sdlc_session_ensure import ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2421"
        session.working_dir = None
        session.active_run_id = None
        session.owned_run_ids = json.dumps(["supervisor-run-abc"])

        live = SupervisedRunStatus(True, "supervisor-run-abc", "sdlc-local-2421")

        lock_mock = MagicMock()
        lock_mock.return_value.acquired = True
        lock_mock.return_value.owner_run_id = None

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
            patch("agent.supervised_run.supervised_run_status", return_value=live),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2421)

        assert result.get("blocked") is None, result
        assert result.get("reason") != "SUPERVISED_RUN_ACTIVE"
        assert result["run_id"] == "supervisor-run-abc"
        assert result["session_id"] == "sdlc-local-2421"

    def test_supervised_foreign_run_still_refused(self):
        """A LIVE supervised signal whose run_id is NOT in owned_run_ids is a
        genuine foreign owner -> SUPERVISED_RUN_ACTIVE (regression)."""
        from agent.supervised_run import SupervisedRunStatus
        from tools.sdlc_session_ensure import ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2421"
        session.working_dir = None
        session.owned_run_ids = json.dumps(["something-else"])

        live = SupervisedRunStatus(True, "foreign-supervisor", "sdlc-local-2421")

        lock_mock = MagicMock()
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
            patch("agent.supervised_run.supervised_run_status", return_value=live),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2421)

        assert result["blocked"] is True
        assert result["reason"] == "SUPERVISED_RUN_ACTIVE"
        assert result["run_id"] == "foreign-supervisor"
        lock_mock.assert_not_called()  # never contests for a foreign owner

    def test_supervised_corrupt_owned_falls_through_to_refusal(self):
        """Malformed owned_run_ids must never raise -- it degrades to [] and
        the foreign refusal is emitted (existing behavior)."""
        from agent.supervised_run import SupervisedRunStatus
        from tools.sdlc_session_ensure import ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2421"
        session.working_dir = None
        session.owned_run_ids = "{corrupt json ["

        live = SupervisedRunStatus(True, "supervisor-run-abc", "sdlc-local-2421")

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
            patch("agent.supervised_run.supervised_run_status", return_value=live),
            patch("models.session_lifecycle.touch_issue_lock", MagicMock()),
        ):
            result = ensure_session(issue_number=2421)

        assert result["blocked"] is True
        assert result["reason"] == "SUPERVISED_RUN_ACTIVE"
