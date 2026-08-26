"""Unit tests for tools.sdlc_session_ensure: lease heartbeat spawn, run-identity anchor (#2879)."""

import json
from unittest.mock import MagicMock, patch


class TestLeaseHeartbeatSpawnIdentity:
    """Issue #2714 L2: the spawner hands the heartbeat its supervisor identity.

    The heartbeat is detached (``start_new_session=True``) and reparented to
    pid 1 within seconds, so it can only ever watch an EXPLICITLY RECORDED
    supervisor ``(pid, create_time)`` -- spike-2 proved the parent-pid shortcut
    is unsound because it reads a HEALTHY heartbeat as orphaned.
    """

    def _spawn(self, monkeypatch, detailed):
        from tools import sdlc_session_ensure as se
        from tools import sdlc_supervisor_identity as si

        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("VALOR_WORKER_MODE", raising=False)
        with (
            patch.object(si, "resolve_supervisor_identity_detailed", detailed),
            patch("subprocess.Popen") as popen,
        ):
            se._maybe_launch_lease_heartbeat(2714, "run-abc", "sdlc-local-2714")
        return popen

    def test_argv_carries_identity_when_both_halves_resolve(self, monkeypatch):
        from tools import sdlc_supervisor_identity as si

        popen = self._spawn(monkeypatch, MagicMock(return_value=(si.SOURCE_ENV, 32886, 111.5)))

        popen.assert_called_once()
        argv = popen.call_args.args[0]
        assert "--supervisor-pid" in argv
        assert argv[argv.index("--supervisor-pid") + 1] == "32886"
        assert argv[argv.index("--supervisor-create-time") + 1] == "111.5"
        assert argv[argv.index("--supervisor-source") + 1] == si.SOURCE_ENV

    def test_argv_omits_identity_when_unresolved(self, monkeypatch):
        from tools import sdlc_supervisor_identity as si

        popen = self._spawn(monkeypatch, MagicMock(return_value=(si.SOURCE_UNRESOLVED, None, None)))

        popen.assert_called_once()
        argv = popen.call_args.args[0]
        assert "--supervisor-pid" not in argv
        assert "--supervisor-create-time" not in argv

    def test_resolver_raising_still_spawns_the_heartbeat(self, monkeypatch):
        """The renewer is the load-bearing part; identity is an enhancement."""
        popen = self._spawn(monkeypatch, MagicMock(side_effect=RuntimeError("psutil exploded")))

        popen.assert_called_once()
        argv = popen.call_args.args[0]
        assert "--supervisor-pid" not in argv
        assert "--issue-number" in argv

    def test_pytest_guard_still_blocks_every_spawn(self, monkeypatch):
        """The guard at sdlc_session_ensure.py:164 must stay intact -- a real
        detached process must never outlive the test suite."""
        from tools import sdlc_session_ensure as se

        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_x (call)")
        with patch("subprocess.Popen") as popen:
            se._maybe_launch_lease_heartbeat(2714, "run-abc", "sdlc-local-2714")
        popen.assert_not_called()


class TestDurableRunIdentityFourthProof:
    """The fourth reuse proof: the durable, issue-keyed run-identity anchor
    (issue #2675).

    The three pre-existing proofs all corroborate a reuse claim against the
    ``AgentSession`` -- the most fragile record in the system. ``ensure_session``
    ends in a **create fall-through**, and a freshly created record has no
    ``active_run_id`` and an empty ``owned_run_ids``, so after a lease lapse
    every corroborating branch is structurally unreachable and the run silently
    re-mints. The fourth proof consults the ledger instead, which is issue-keyed
    and has no TTL.

    Placed strictly AFTER the three existing proofs, and read fail-open: a read
    error degrades to exactly the pre-#2675 outcome.
    """

    _ISSUE = 2675
    _REPO = "test-owner/test-repo-2675"

    def _fresh_session(self):
        """A session as ``ensure_session``'s create fall-through leaves it."""
        session = MagicMock()
        session.session_id = "sdlc-local-2675"
        session.active_run_id = None
        session.owned_run_ids = None
        return session

    def _peek(self, *, acquired, owner_run_id):
        peek = MagicMock()
        peek.acquired = acquired
        peek.owner_run_id = owner_run_id
        return peek

    def _call(self, session, claim, *, peek, anchor=(), anchor_error=None):
        from tools.sdlc_session_ensure import _validated_reuse_candidate

        read = (
            MagicMock(side_effect=anchor_error)
            if anchor_error is not None
            else MagicMock(return_value=list(anchor))
        )
        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=peek),
            patch("tools._sdlc_utils._resolve_target_repo_fallback", return_value=self._REPO),
            patch("agent.pipeline_ledger.read_run_identities", read),
        ):
            return _validated_reuse_candidate(self._ISSUE, session, claim)

    # --- the new proof -----------------------------------------------------

    def test_free_lock_anchor_corroborates_claim_on_a_brand_new_session(self):
        """THE bug: free lock + a session that knows nothing (create
        fall-through) + the claim present in the durable anchor -> rebind to
        the claim instead of minting fresh."""
        got = self._call(
            self._fresh_session(),
            "old-id",
            peek=self._peek(acquired=True, owner_run_id=None),
            anchor=["old-id"],
        )
        assert got == "old-id"

    def test_free_lock_empty_anchor_still_falls_through_to_fresh_mint(self):
        """No anchor entry -> no corroboration -> None (today's behavior, and
        the backward-compat outcome for every pre-existing ledger)."""
        got = self._call(
            self._fresh_session(),
            "old-id",
            peek=self._peek(acquired=True, owner_run_id=None),
            anchor=[],
        )
        assert got is None

    def test_uncorroborated_claim_is_never_adopted_from_the_anchor(self):
        """NO-ADOPT INVARIANT. The anchor is full of identities this issue has
        seen, but the caller's claim is not one of them: the helper must return
        None -- never hand back an id the caller did not already carry."""
        got = self._call(
            self._fresh_session(),
            "never-mine",
            peek=self._peek(acquired=True, owner_run_id=None),
            anchor=["run-a", "run-b", "run-c"],
        )
        assert got is None
        assert got not in ("run-a", "run-b", "run-c")

    def test_foreign_live_holder_still_yields_no_candidate(self):
        """A live foreign holder is refused even when BOTH the claim and the
        live owner appear in the issue-keyed anchor. Unlike ``owned_run_ids``,
        the anchor is issue-scoped, not session-scoped -- it can legitimately
        contain a genuinely foreign run's id, so it must never be read as
        authority to take over a live lock (this is what keeps ISSUE_LOCKED
        meaning ISSUE_LOCKED)."""
        got = self._call(
            self._fresh_session(),
            "old-id",
            peek=self._peek(acquired=False, owner_run_id="foreign-live"),
            anchor=["old-id", "foreign-live"],
        )
        assert got is None

    def test_anchor_read_error_degrades_to_the_three_legacy_proofs(self):
        """Fail-open: a Redis hiccup on the anchor read must not raise and must
        leave the pre-#2675 outcome intact."""
        got = self._call(
            self._fresh_session(),
            "old-id",
            peek=self._peek(acquired=True, owner_run_id=None),
            anchor_error=RuntimeError("redis down"),
        )
        assert got is None

    def test_anchor_read_error_does_not_break_a_legacy_proof(self):
        session = self._fresh_session()
        session.owned_run_ids = json.dumps(["old-id"])
        got = self._call(
            session,
            "old-id",
            peek=self._peek(acquired=True, owner_run_id=None),
            anchor_error=RuntimeError("redis down"),
        )
        assert got == "old-id"

    def test_unresolvable_target_repo_degrades_to_the_three_legacy_proofs(self):
        """Without a repo slug there is no ledger key to read; the anchor is
        skipped rather than assembling a phantom ``None:{issue}`` key."""
        from tools.sdlc_session_ensure import _validated_reuse_candidate

        read = MagicMock(return_value=["old-id"])
        with (
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._peek(acquired=True, owner_run_id=None),
            ),
            patch("tools._sdlc_utils._resolve_target_repo_fallback", return_value=None),
            patch("agent.pipeline_ledger.read_run_identities", read),
        ):
            got = _validated_reuse_candidate(self._ISSUE, self._fresh_session(), "old-id")

        assert got is None
        read.assert_not_called()

    # --- controls: the three pre-existing proofs are unchanged -------------

    def test_control_proof_one_live_owner_match_never_consults_the_anchor(self):
        from tools.sdlc_session_ensure import _validated_reuse_candidate

        read = MagicMock(return_value=[])
        with (
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._peek(acquired=True, owner_run_id="mine"),
            ),
            patch("agent.pipeline_ledger.read_run_identities", read),
        ):
            got = _validated_reuse_candidate(self._ISSUE, self._fresh_session(), "mine")

        assert got == "mine"
        read.assert_not_called()

    def test_control_proof_two_remint_returns_live_owner_without_the_anchor(self):
        from tools.sdlc_session_ensure import _validated_reuse_candidate

        session = self._fresh_session()
        session.owned_run_ids = json.dumps(["old-id", "new-id"])
        read = MagicMock(return_value=[])
        with (
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._peek(acquired=False, owner_run_id="new-id"),
            ),
            patch("agent.pipeline_ledger.read_run_identities", read),
        ):
            got = _validated_reuse_candidate(self._ISSUE, session, "old-id")

        assert got == "new-id"
        read.assert_not_called()

    def test_control_proof_three_record_mirror_wins_before_the_anchor(self):
        from tools.sdlc_session_ensure import _validated_reuse_candidate

        session = self._fresh_session()
        session.active_run_id = "old-id"
        read = MagicMock(return_value=[])
        with (
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._peek(acquired=True, owner_run_id=None),
            ),
            patch("agent.pipeline_ledger.read_run_identities", read),
        ):
            got = _validated_reuse_candidate(self._ISSUE, session, "old-id")

        assert got == "old-id"
        read.assert_not_called()

    def test_control_peek_failure_still_returns_none(self):
        from tools.sdlc_session_ensure import _validated_reuse_candidate

        with patch("models.session_lifecycle.touch_issue_lock", side_effect=RuntimeError("boom")):
            got = _validated_reuse_candidate(self._ISSUE, self._fresh_session(), "old-id")
        assert got is None


class TestRunIdentityAnchorWriteOnLeaseConfirmation:
    """The anchor's write half (issue #2675).

    The identity is recorded from ``resolve_ledger_lease`` -- the one place a
    run is *confirmed* to be the live owner of an issue's lease with the pinned
    ``target_repo`` already in hand. Provenance is therefore identical to
    ``owned_run_ids``: self-written history only, never read out of a foreign
    payload.
    """

    def test_confirmed_lease_owner_is_recorded(self):
        from tools._sdlc_utils import resolve_ledger_lease

        peek = MagicMock()
        peek.acquired = True
        peek.owner_run_id = "mine"
        peek.target_repo = "test-owner/test-repo-2675"

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=peek),
            patch("agent.pipeline_ledger.record_run_identity") as rec,
        ):
            repo, err = resolve_ledger_lease(2675, "mine")

        assert (repo, err) == ("test-owner/test-repo-2675", None)
        rec.assert_called_once()
        assert rec.call_args.args[:3] == ("test-owner/test-repo-2675", 2675, "mine")

    def test_refused_lease_records_nothing(self):
        from tools._sdlc_utils import resolve_ledger_lease

        peek = MagicMock()
        peek.acquired = False
        peek.owner_run_id = "foreign"
        peek.target_repo = "test-owner/test-repo-2675"

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=peek),
            patch("agent.pipeline_ledger.record_run_identity") as rec,
        ):
            _repo, err = resolve_ledger_lease(2675, "mine")

        assert err["reason"] == "ISSUE_LOCKED"
        rec.assert_not_called()

    def test_unpinned_target_repo_records_nothing(self):
        """A legacy lease payload with no pinned repo has no ledger key to
        anchor against -- skip rather than mint a phantom ``None:{issue}``."""
        from tools._sdlc_utils import resolve_ledger_lease

        peek = MagicMock()
        peek.acquired = True
        peek.owner_run_id = "mine"
        peek.target_repo = None

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=peek),
            patch("agent.pipeline_ledger.record_run_identity") as rec,
        ):
            repo, err = resolve_ledger_lease(2675, "mine")

        assert (repo, err) == (None, None)
        rec.assert_not_called()

    def test_anchor_write_failure_never_fails_the_lease_resolution(self):
        from tools._sdlc_utils import resolve_ledger_lease

        peek = MagicMock()
        peek.acquired = True
        peek.owner_run_id = "mine"
        peek.target_repo = "test-owner/test-repo-2675"

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=peek),
            patch(
                "agent.pipeline_ledger.record_run_identity",
                side_effect=RuntimeError("redis down"),
            ),
        ):
            repo, err = resolve_ledger_lease(2675, "mine")

        assert (repo, err) == ("test-owner/test-repo-2675", None)
