"""Unit tests for tools.sdlc_session_ensure: issue-lock wiring at every return point (#2879)."""

import json
import time
from unittest.mock import MagicMock, patch


class TestIssueLockWiring:
    """Issues #1954/#2003: every return point of ensure_session() -- the 4
    early-return branches (env-owns-issue, env-diverges-but-issue-owned,
    find_session_by_issue match, idempotent existing_by_id match) plus the
    final create-and-claim path -- goes through one shared helper that mints
    a FRESH run_id candidate, contests the issue lock, and binds the winner
    to the session record. No branch can skip the contest, and there is NO
    adopt-from-record branch.
    """

    @staticmethod
    def _lock_result(acquired: bool, owner_session_id=None, owner_run_id=None):
        from models.session_lifecycle import IssueLockResult

        return IssueLockResult(
            acquired=acquired,
            owner_session_id=owner_session_id,
            owner_run_id=owner_run_id,
        )

    @staticmethod
    def _readback_as(session):
        """Mock AgentSession whose readback query returns the bound session."""
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [session]
        return mock_as

    def test_mint_on_env_owns_issue_return(self, monkeypatch):
        """Return point 1: env session owns the issue (true no-op short-circuit)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_valor_-100_691")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        bridge_session = MagicMock()
        bridge_session.session_id = "tg_valor_-100_691"
        bridge_session.session_type = "eng"
        bridge_session.status = "running"
        bridge_session.issue_url = "https://github.com/tomcounsell/ai/issues/2001"

        lock_mock = MagicMock(return_value=self._lock_result(True, "tg_valor_-100_691"))

        with (
            patch("tools._sdlc_utils.find_session", return_value=bridge_session),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("models.agent_session.AgentSession", self._readback_as(bridge_session)),
        ):
            result = ensure_session(issue_number=2001)

        assert result["session_id"] == "tg_valor_-100_691"
        assert result["created"] is False
        lock_mock.assert_called_once()
        args, kwargs = lock_mock.call_args
        assert args[0] == 2001
        # A FRESH uuid-hex candidate is minted per top-level call and emitted.
        assert isinstance(args[1], str) and len(args[1]) == 32
        assert result["run_id"] == args[1]
        assert kwargs.get("session_id") == "tg_valor_-100_691"
        assert bridge_session.active_run_id == args[1]

    def test_mint_on_env_diverges_but_issue_owned_return(self, monkeypatch):
        """Return point 2: env session diverges; an existing issue-scoped
        session is preferred (C1, #1671)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-other-issue")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        env_session = MagicMock()
        env_session.session_id = "parent-pm-other-issue"
        env_session.session_type = "eng"
        env_session.status = "running"
        env_session.issue_url = "https://github.com/tomcounsell/ai/issues/9999"

        issue_session = MagicMock()
        issue_session.session_id = "sdlc-local-2002"

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2002"))

        with (
            patch("tools._sdlc_utils.find_session", return_value=env_session),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("models.agent_session.AgentSession", self._readback_as(issue_session)),
        ):
            result = ensure_session(issue_number=2002)

        assert result["session_id"] == "sdlc-local-2002"
        assert result["created"] is False
        lock_mock.assert_called_once()
        args, kwargs = lock_mock.call_args
        assert args[0] == 2002
        assert result["run_id"] == args[1]
        assert kwargs.get("session_id") == "sdlc-local-2002"

    def test_mint_on_find_session_by_issue_match_return(self):
        """Return point 3: the main issue-based lookup (no env var)."""
        from tools.sdlc_session_ensure import ensure_session

        existing = MagicMock()
        existing.session_id = "sdlc-local-2003"

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2003"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=existing),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("models.agent_session.AgentSession", self._readback_as(existing)),
        ):
            result = ensure_session(issue_number=2003)

        assert result["session_id"] == "sdlc-local-2003"
        assert result["created"] is False
        lock_mock.assert_called_once()
        args, kwargs = lock_mock.call_args
        assert args[0] == 2003
        assert result["run_id"] == args[1]
        assert kwargs.get("session_id") == "sdlc-local-2003"

    def test_mint_on_idempotent_existing_by_id_return(self):
        """Return point 4: a session with sdlc-local-{N} already exists (no
        find_session_by_issue hit, matched by deterministic id instead)."""
        from tools.sdlc_session_ensure import ensure_session

        existing = MagicMock()
        existing.session_id = "sdlc-local-2004"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [existing]

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2004"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2004)

        assert result["session_id"] == "sdlc-local-2004"
        assert result["created"] is False
        lock_mock.assert_called_once()
        args, _ = lock_mock.call_args
        assert args[0] == 2004
        assert result["run_id"] == args[1]

    def test_mint_on_create_and_claim_return(self):
        """Return point 5: the final create-and-claim path (cold start)."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-2005"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2005"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2005)

        assert result["session_id"] == "sdlc-local-2005"
        assert result["created"] is True
        lock_mock.assert_called_once()
        args, _ = lock_mock.call_args
        assert args[0] == 2005
        assert result["run_id"] == args[1]
        # issue_number is written ONCE, only on this creation path.
        _, kwargs = mock_as.create_local.call_args
        assert kwargs.get("issue_number") == 2005

    def test_acquire_run_lock_and_bind_pins_target_repo_from_resolver(self):
        """Issue #2012: target_repo is resolved exactly once, in
        _acquire_run_lock_and_bind, and passed through to every
        touch_issue_lock call -- never re-resolved per write downstream."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-2006"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2006"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools._sdlc_utils._resolve_target_repo", return_value="tomcounsell/ai"),
        ):
            ensure_session(issue_number=2006)

        lock_mock.assert_called_once()
        _args, kwargs = lock_mock.call_args
        assert kwargs.get("target_repo") == "tomcounsell/ai"

    def test_acquire_run_lock_and_bind_passes_through_none_target_repo(self):
        """A resolver miss (None) must not block lock acquisition -- it is
        passed through as-is; downstream degradation is the ledger writer's
        job, not this function's."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-2007"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2007"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools._sdlc_utils._resolve_target_repo", return_value=None),
        ):
            result = ensure_session(issue_number=2007)

        assert result["session_id"] == "sdlc-local-2007"
        lock_mock.assert_called_once()
        _args, kwargs = lock_mock.call_args
        assert kwargs.get("target_repo") is None

    def test_issue_number_not_rewritten_on_continuing_session_returns(self):
        """The 4 early-return (continuing-session) branches must NEVER write
        issue_number -- it is a write-once mirror field set only at creation."""
        from tools.sdlc_session_ensure import ensure_session

        existing = MagicMock()
        existing.session_id = "sdlc-local-2006"

        mock_as = self._readback_as(existing)  # create_local must never be called.

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=existing),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._lock_result(True, "sdlc-local-2006"),
            ),
        ):
            result = ensure_session(issue_number=2006)

        assert result["session_id"] == "sdlc-local-2006"
        assert result["created"] is False
        mock_as.create_local.assert_not_called()

    def test_blocked_shape_includes_owning_run_id(self):
        """When touch_issue_lock() reports a foreign live holder,
        ensure_session() propagates ISSUE_LOCKED with BOTH the owning run_id
        and session_id -- never silently returning the session."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-2007"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._lock_result(
                    False,
                    owner_session_id="sdlc-local-2007-other-owner",
                    owner_run_id="foreign-run-hex",
                ),
            ),
        ):
            result = ensure_session(issue_number=2007)

        assert result == {
            "blocked": True,
            "reason": "ISSUE_LOCKED",
            "owner_run_id": "foreign-run-hex",
            "owner_session_id": "sdlc-local-2007-other-owner",
            # Cycle-3 nit: the refusal carries the orphan signal (from the
            # follow-up peek; the mocked lock reports not-orphaned).
            "orphaned_lock": False,
        }

    def test_second_bare_ensure_under_live_signal_inherits_run_id(self):
        """WS1 (#2026) supersedes the old #2003 ISSUE_LOCKED-on-second-bare-
        ensure behavior: Call A mints run_id_a, acquires the lease, AND writes
        the supervised-run signal. A second BARE ensure now finds the LIVE
        signal and returns the named SUPERVISED_RUN_ACTIVE refusal carrying
        run_id_a to inherit -- it mints NOTHING (no fresh candidate, no
        adoption from the record). Exercises the REAL touch_issue_lock() and
        the real signal against the test Redis db."""
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2050
        local_session_id = f"sdlc-local-{issue_number}"

        session = MagicMock()
        session.session_id = local_session_id
        session.working_dir = None  # anchor session: no slug worktree file

        # Call A: fresh key, must acquire and bind its run_id + write the signal.
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_a = ensure_session(issue_number=issue_number)

        assert result_a["created"] is False
        run_id_a = result_a["run_id"]
        assert run_id_a
        assert session.active_run_id == run_id_a

        # Call B: a bare ensure under the live supervised-run signal inherits
        # run_id_a via SUPERVISED_RUN_ACTIVE and mints nothing.
        session_b = MagicMock()
        session_b.session_id = local_session_id
        session_b.active_run_id = None
        session_b.working_dir = None
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session_b),
            patch("models.agent_session.AgentSession", self._readback_as(session_b)),
        ):
            result_b = ensure_session(issue_number=issue_number)

        assert result_b["blocked"] is True
        assert result_b["reason"] == "SUPERVISED_RUN_ACTIVE"
        assert result_b["run_id"] == run_id_a
        assert result_b["owner_run_id"] == run_id_a
        # No fresh mint and no adoption onto the second session's record.
        assert result_b.get("created") is None
        assert session_b.active_run_id is None

    def test_save_failure_releases_lock_next_caller_acquires_immediately(self):
        """Race 3 (cycle-2 CONCERN 2): a save failure after lock acquire
        releases the lock via COMPARE-AND-DELETE -- the next caller acquires
        immediately instead of waiting out the 300s TTL. Real Redis."""
        import popoto.redis_db as rdb

        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2051

        broken = MagicMock()
        broken.session_id = f"sdlc-local-{issue_number}"
        broken.save.side_effect = RuntimeError("redis save exploded")

        with patch("tools._sdlc_utils.find_session_by_issue", return_value=broken):
            result = ensure_session(issue_number=issue_number)

        assert result.get("error") == "RUN_BIND_FAILED"
        # Lock released: key gone from the test Redis db.
        assert rdb.POPOTO_REDIS_DB.get(f"session:issuelock:{issue_number}") is None

        # Next caller acquires immediately -- no 300s wedge.
        healthy = MagicMock()
        healthy.session_id = f"sdlc-local-{issue_number}"
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=healthy),
            patch("models.agent_session.AgentSession", self._readback_as(healthy)),
        ):
            result2 = ensure_session(issue_number=issue_number)

        assert result2["run_id"]
        assert result2["session_id"] == f"sdlc-local-{issue_number}"

    def test_readback_mismatch_releases_lock(self):
        """Post-save readback mismatch (the record does not carry the lock's
        run_id) releases the lock and surfaces the error. Real Redis."""
        import popoto.redis_db as rdb

        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2052

        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"

        stale = MagicMock()
        stale.session_id = f"sdlc-local-{issue_number}"
        stale.active_run_id = "some-other-run-entirely"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [stale]  # readback sees a stale value

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=issue_number)

        assert result.get("error") == "RUN_BIND_FAILED"
        assert rdb.POPOTO_REDIS_DB.get(f"session:issuelock:{issue_number}") is None

    def test_orphaned_lock_flagged_on_peek(self):
        """A lock whose recorded owner pid is dead is reported
        orphaned_lock=True by the peek path (issue #2305 defect 1:
        authoritative liveness is the lock payload's pid, not any
        AgentSession's status). Real Redis lock, payload written directly
        with a pid that is not alive on this host."""
        import json
        import socket

        import popoto.redis_db as rdb

        from models.session_lifecycle import touch_issue_lock

        issue_number = 2053
        rdb.POPOTO_REDIS_DB.set(
            f"session:issuelock:{issue_number}",
            json.dumps(
                {
                    "run_id": "ghost-run",
                    "session_id": "sdlc-local-2053",
                    "pid": 424242,
                    "hostname": socket.gethostname(),
                    "create_time": 1000.0,
                }
            ),
            ex=300,
        )

        peek = touch_issue_lock(issue_number, None, session_id="sdlc-local-2053", peek=True)
        assert peek.acquired is False
        assert peek.owner_run_id == "ghost-run"
        assert peek.orphaned_lock is True

    def test_legacy_record_without_active_run_id_never_crashes(self):
        """A legacy session record with no active_run_id (pre-#2003 rows)
        contests the lock normally -- reads never crash on the missing
        field, and the fresh mint binds onto it."""
        from tools.sdlc_session_ensure import ensure_session

        legacy = MagicMock()
        legacy.session_id = "sdlc-local-2054"
        legacy.active_run_id = None  # legacy row: field absent/None

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=legacy),
            patch("models.agent_session.AgentSession", self._readback_as(legacy)),
        ):
            result = ensure_session(issue_number=2054)

        assert result["session_id"] == "sdlc-local-2054"
        assert result["run_id"]
        assert legacy.active_run_id == result["run_id"]


class TestBindDoesNotRestampUpdatedAt:
    """#2660: the run-lock bind is a partial save (update_fields=["active_run_id",
    "owned_run_ids"]) precisely so a stage dispatch that only re-binds the run
    lock -- and changes nothing else -- does not restamp updated_at on the
    whole row. This is the writer that kept the #2660 ledger anchors
    permanently fresh, so it needs a REAL AgentSession (not a MagicMock,
    whose .save() is a no-op that would pass this assertion vacuously either
    way) to prove the partial save actually reaches Redis.
    """

    @staticmethod
    def _lock_result(acquired: bool, owner_session_id=None, owner_run_id=None):
        from models.session_lifecycle import IssueLockResult

        return IssueLockResult(
            acquired=acquired,
            owner_session_id=owner_session_id,
            owner_run_id=owner_run_id,
        )

    def test_bind_leaves_updated_at_unmoved_but_persists_run_identity(self):
        from models.agent_session import AgentSession
        from tools.sdlc_session_ensure import _acquire_run_lock_and_bind

        session = AgentSession(session_id="sdlc-local-266001", project_key="test", status="running")
        session.save()
        session_id = session.agent_session_id
        updated_before = AgentSession.get_by_id(session_id).updated_at

        with (
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._lock_result(True, session.session_id),
            ),
            patch("agent.supervised_run.supervised_run_status", return_value=None),
            patch("tools._sdlc_utils._resolve_target_repo", return_value=None),
        ):
            run_id, error = _acquire_run_lock_and_bind(266001, session)

        assert error is None
        assert run_id

        after = AgentSession.get_by_id(session_id)
        assert after.updated_at == updated_before, (
            "a bind that only changes active_run_id/owned_run_ids must not "
            "restamp updated_at -- this is the writer #2660 narrows"
        )
        assert after.active_run_id == run_id, (
            "the bind must still persist active_run_id despite the partial save"
        )
        assert after.owned_run_ids, (
            "the bind must still persist owned_run_ids despite the partial save"
        )
        assert run_id in json.loads(after.owned_run_ids)

    def test_bind_that_advances_a_stage_state_still_refreshes_updated_at(self):
        """Contrast case: a writer OTHER than the bind (a genuine stage-state
        advance, simulated here by a plain full save()) still moves
        updated_at as before -- #2660 narrows only the bind, not every
        writer on the row.
        """
        from models.agent_session import AgentSession

        session = AgentSession(session_id="sdlc-local-266002", project_key="test", status="running")
        session.save()
        session_id = session.agent_session_id
        updated_before = AgentSession.get_by_id(session_id).updated_at

        time.sleep(0.05)
        session.status = "running"  # no-op field change, but a full save()
        session.save()

        after = AgentSession.get_by_id(session_id)
        assert after.updated_at != updated_before, (
            "a genuine full save() (stage-state advance) must still refresh "
            "updated_at -- only the run-lock bind is narrowed"
        )
