"""Post-save readback identity and lock-release provenance (#3065 Cluster E).

Two defects, one test module, because they compose into the observed wedge:

1. The post-save readback re-queried the non-unique ``session_id`` index and
   took ``[0]``. ``AgentSession.session_id`` is a plain ``Field()``, not the
   primary key, so a lane whose row was recreated after a crash has two rows
   sharing one id and Popoto resolves that filter through an unordered Redis
   ``SMEMBERS`` -- ``[0]`` is a coin flip. Spike-5 measured four
   ``RUN_BIND_FAILED / post-save readback mismatch`` results in six
   consecutive ensures on one duplicated lane.
2. The mismatch cleanup handed ``release_issue_lock`` a ``candidate`` that may
   have been ADOPTED from the live lock rather than minted by this call. The
   compare-and-delete is correct, which is precisely the problem: an adopted
   candidate equals the live owner *by construction*, so the release always
   "succeeds" -- destroying a lease this call never created. The missing
   distinction is provenance, not identity.

Every lock assertion here reads the raw ``session:issuelock:{n}`` key. That is
deliberate and permitted: the issue lock is a plain Redis key, not a
Popoto-managed model key, so the raw-Redis rule does not govern it (the same
reads already appear in ``test_sdlc_session_ensure_issue_lock.py``).
"""

import json
from unittest.mock import MagicMock, patch

import pytest


def _lock_key(issue_number: int) -> str:
    return f"session:issuelock:{issue_number}"


def _live_lock_owner(issue_number: int) -> str | None:
    """The run_id currently holding the issue lock, or None if it is gone."""
    import popoto.redis_db as rdb

    raw = rdb.POPOTO_REDIS_DB.get(_lock_key(issue_number))
    if raw is None:
        return None
    return json.loads(raw).get("run_id")


@pytest.fixture
def _no_target_repo():
    """Keep the bind path off the network (``gh repo view``) and off signals."""
    with (
        patch("tools._sdlc_utils._resolve_target_repo", return_value=None),
        patch("agent.supervised_run.write_supervised_run_signal"),
    ):
        yield


@pytest.fixture
def _release_issue_lock_after():
    """Deterministic teardown for tests that acquire a REAL issue lock via
    ``touch_issue_lock`` against the shared Redis and assert it survives.

    Several agents test on this machine at once, so a lock a test forgets to
    release wedges another lane. The test body registers ``(issue_number,
    run_id)`` pairs via the returned callable; teardown releases every
    registered pair through ``release_issue_lock`` (the sanctioned
    compare-and-delete helper -- never a raw Redis ``DELETE`` on this
    Popoto-adjacent key), even if the test body raises.
    """
    from models.session_lifecycle import release_issue_lock

    registered: list[tuple[int, str]] = []

    def _register(issue_number: int, run_id: str) -> None:
        registered.append((issue_number, run_id))

    try:
        yield _register
    finally:
        for issue_number, run_id in registered:
            release_issue_lock(issue_number, run_id)


class TestReadbackByPrimaryKey:
    """The readback resolves the row THIS call wrote, not an arbitrary row
    sharing its ``session_id``."""

    def test_duplicate_rows_do_not_destabilize_repeated_ensures(self, _no_target_repo):
        """Demonstrated-red (spike-5): with two rows sharing one session_id,
        repeated binds onto a specific row must succeed EVERY time.

        Under the old ``filter(session_id=...)[0]`` readback each iteration was
        an independent coin flip between the row just written and its stale
        twin, so this loop failed with probability ~1 - 2**-6. Real Redis, real
        AgentSession rows -- a MagicMock would make the duplicate meaningless.
        """
        from models.agent_session import AgentSession
        from models.session_lifecycle import release_issue_lock
        from tools.sdlc_session_ensure import _acquire_run_lock_and_bind

        issue_number = 306501
        session_id = f"sdlc-local-{issue_number}"

        target = AgentSession(session_id=session_id, project_key="test-3065", status="running")
        target.save()
        twin = AgentSession(session_id=session_id, project_key="test-3065", status="running")
        twin.active_run_id = "stale-twin-run-id"
        twin.save()

        # The duplicate the whole defect rests on: one session_id, two rows.
        assert len(list(AgentSession.query.filter(session_id=session_id))) == 2
        assert target.agent_session_id != twin.agent_session_id

        run_ids = []
        for _ in range(6):
            run_id, error = _acquire_run_lock_and_bind(issue_number, target)
            assert error is None, f"bind failed on a duplicated-row lane: {error}"
            assert run_id
            run_ids.append(run_id)
            release_issue_lock(issue_number, run_id)

        # Every iteration minted a distinct id and bound it to the SAME row.
        assert len(set(run_ids)) == 6
        assert AgentSession.get_by_id(target.agent_session_id).active_run_id == run_ids[-1]
        # The twin was never written through.
        assert AgentSession.get_by_id(twin.agent_session_id).active_run_id == "stale-twin-run-id"

    def test_readback_never_queries_the_session_id_index(self, _no_target_repo):
        """Structural: the bind path must not reach for ``filter(session_id=...)``.

        A behavioral-only assertion would pass on a lucky coin flip, so this
        pins the mechanism: the readback is a primary-key read.
        """
        from tools.sdlc_session_ensure import _acquire_run_lock_and_bind

        issue_number = 306502
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"
        session.owned_run_ids = None

        mock_as = MagicMock()

        def _get(*args, **kwargs):
            # Echo back whatever the call under test just bound.
            fresh = MagicMock()
            fresh.active_run_id = session.active_run_id
            return fresh

        mock_as.query.get.side_effect = _get

        with patch("models.agent_session.AgentSession", mock_as):
            run_id, error = _acquire_run_lock_and_bind(issue_number, session)

        assert error is None
        assert run_id
        mock_as.query.get.assert_called_once()
        assert mock_as.query.filter.call_count == 0


class TestReleaseGatedOnProvenance:
    """``release_issue_lock`` is correct and unchanged; only a MINTED candidate
    may ever be handed to it."""

    @staticmethod
    def _adopting_session(session_id: str, run_id: str) -> MagicMock:
        """A session whose reuse claim is corroborated by its own history."""
        session = MagicMock()
        session.session_id = session_id
        session.owned_run_ids = json.dumps([run_id])
        return session

    def test_adopted_candidate_survives_readback_mismatch(
        self, _no_target_repo, _release_issue_lock_after
    ):
        """The wedge itself: a reuse call that cannot confirm its bind must NOT
        delete the live lease it merely adopted."""
        from models.session_lifecycle import touch_issue_lock
        from tools.sdlc_session_ensure import _acquire_run_lock_and_bind

        issue_number = 306503
        session_id = f"sdlc-local-{issue_number}"
        holder_run_id = "holder-run-id-306503"

        # A live lock this call did not create, owned by the id it will reuse.
        assert touch_issue_lock(issue_number, holder_run_id, session_id=session_id).acquired
        _release_issue_lock_after(issue_number, holder_run_id)
        assert _live_lock_owner(issue_number) == holder_run_id

        session = self._adopting_session(session_id, holder_run_id)

        stale = MagicMock()
        stale.active_run_id = "some-other-run-entirely"
        mock_as = MagicMock()
        mock_as.query.get.return_value = stale

        with patch("models.agent_session.AgentSession", mock_as):
            run_id, error = _acquire_run_lock_and_bind(
                issue_number, session, reuse_run_id=holder_run_id
            )

        assert run_id is None
        assert error["error"] == "RUN_BIND_FAILED"
        assert error["reason"] == "post-save readback mismatch"
        # The lease is untouched: still live, still owned by the holder.
        assert _live_lock_owner(issue_number) == holder_run_id

    def test_adopted_candidate_survives_a_raising_readback(
        self, _no_target_repo, _release_issue_lock_after
    ):
        """Failure Path Test Strategy, ``:618``: the readback's own ``except``
        arm is a release site too, and an adopted candidate must survive it."""
        from models.session_lifecycle import touch_issue_lock
        from tools.sdlc_session_ensure import _acquire_run_lock_and_bind

        issue_number = 306504
        session_id = f"sdlc-local-{issue_number}"
        holder_run_id = "holder-run-id-306504"

        assert touch_issue_lock(issue_number, holder_run_id, session_id=session_id).acquired
        _release_issue_lock_after(issue_number, holder_run_id)

        session = self._adopting_session(session_id, holder_run_id)

        mock_as = MagicMock()
        mock_as.query.get.side_effect = RuntimeError("redis readback exploded")

        with patch("models.agent_session.AgentSession", mock_as):
            run_id, error = _acquire_run_lock_and_bind(
                issue_number, session, reuse_run_id=holder_run_id
            )

        assert run_id is None
        assert error["error"] == "RUN_BIND_FAILED"
        assert "post-save readback failed" in error["reason"]
        assert _live_lock_owner(issue_number) == holder_run_id

    def test_adopted_candidate_survives_a_save_failure(
        self, _no_target_repo, _release_issue_lock_after
    ):
        """Third release site (the ``session.save`` ``except`` arm)."""
        from models.session_lifecycle import touch_issue_lock
        from tools.sdlc_session_ensure import _acquire_run_lock_and_bind

        issue_number = 306505
        session_id = f"sdlc-local-{issue_number}"
        holder_run_id = "holder-run-id-306505"

        assert touch_issue_lock(issue_number, holder_run_id, session_id=session_id).acquired
        _release_issue_lock_after(issue_number, holder_run_id)

        session = self._adopting_session(session_id, holder_run_id)
        session.save.side_effect = RuntimeError("redis save exploded")

        run_id, error = _acquire_run_lock_and_bind(
            issue_number, session, reuse_run_id=holder_run_id
        )

        assert run_id is None
        assert error["error"] == "RUN_BIND_FAILED"
        assert _live_lock_owner(issue_number) == holder_run_id

    def test_supervised_adoption_also_survives_a_readback_mismatch(
        self, _no_target_repo, _release_issue_lock_after
    ):
        """The second adopt shape (``ADOPTED_SUPERVISED``): a BARE ensure that
        inherited the supervisor's run_id via self-recognition never minted it
        either, so it may not release it.

        This is the shape ``reuse_run_id`` cannot distinguish -- it arrives
        empty and is overwritten from the signal -- which is why provenance is
        tracked separately from the value.
        """
        from models.session_lifecycle import touch_issue_lock
        from tools.sdlc_session_ensure import _acquire_run_lock_and_bind

        issue_number = 306506
        session_id = f"sdlc-local-{issue_number}"
        supervisor_run_id = "supervisor-run-id-306506"

        assert touch_issue_lock(issue_number, supervisor_run_id, session_id=session_id).acquired
        _release_issue_lock_after(issue_number, supervisor_run_id)

        session = self._adopting_session(session_id, supervisor_run_id)

        signal = MagicMock()
        signal.live = True
        signal.run_id = supervisor_run_id
        signal.session_id = session_id

        stale = MagicMock()
        stale.active_run_id = "some-other-run-entirely"
        mock_as = MagicMock()
        mock_as.query.get.return_value = stale

        with (
            patch("agent.supervised_run.supervised_run_status", return_value=signal),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            # BARE ensure -- no reuse_run_id. The supervised-self path supplies it.
            run_id, error = _acquire_run_lock_and_bind(issue_number, session)

        assert run_id is None
        assert error["error"] == "RUN_BIND_FAILED"
        assert _live_lock_owner(issue_number) == supervisor_run_id

    def test_minted_candidate_is_still_released_on_save_failure(self, _no_target_repo):
        """The other pole: provenance gating must not turn into "never release".

        A call that minted its own candidate and then failed to bind still
        releases, so the next caller acquires immediately instead of waiting
        out the 1800s TTL (cycle-2 CONCERN 2, unchanged by #3065).
        """
        from tools.sdlc_session_ensure import _acquire_run_lock_and_bind

        issue_number = 306507
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"
        session.owned_run_ids = None
        session.save.side_effect = RuntimeError("redis save exploded")

        with patch("agent.supervised_run.supervised_run_status", return_value=None):
            run_id, error = _acquire_run_lock_and_bind(issue_number, session)

        assert run_id is None
        assert error["error"] == "RUN_BIND_FAILED"
        assert _live_lock_owner(issue_number) is None

    def test_minted_candidate_is_still_released_on_readback_mismatch(self, _no_target_repo):
        """Same pole at the readback site: an unverifiable bind of a
        self-minted id frees the lock it just took."""
        from tools.sdlc_session_ensure import _acquire_run_lock_and_bind

        issue_number = 306508
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"
        session.owned_run_ids = None

        stale = MagicMock()
        stale.active_run_id = "some-other-run-entirely"
        mock_as = MagicMock()
        mock_as.query.get.return_value = stale

        with (
            patch("agent.supervised_run.supervised_run_status", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            run_id, error = _acquire_run_lock_and_bind(issue_number, session)

        assert run_id is None
        assert error["reason"] == "post-save readback mismatch"
        assert _live_lock_owner(issue_number) is None
