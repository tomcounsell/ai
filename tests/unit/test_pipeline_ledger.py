"""Tests for agent.pipeline_ledger.PipelineLedger (issue #2012).

Real Popoto/Redis integration -- no mocks, per this repo's testing philosophy
(see CLAUDE.md "Testing Philosophy"). Every test cleans up the records it
creates so the suite leaves no residue in the shared test Redis.
"""

import threading
import time
from unittest.mock import patch

from agent.pipeline_ledger import PipelineLedger

_TEST_REPO = "test-owner/test-repo"


def _cleanup(issue_number: int, target_repo: str = _TEST_REPO) -> None:
    for record in PipelineLedger.query.filter(ledger_key=f"{target_repo}:{issue_number}"):
        record.delete()


class TestGetOrCreate:
    def setup_method(self):
        _cleanup(100001)
        _cleanup(100002)

    def teardown_method(self):
        _cleanup(100001)
        _cleanup(100002)

    def test_returns_empty_but_valid_record_when_absent(self):
        """get_or_create on a (repo, issue) with no ledger yet creates an
        empty-but-valid record rather than erroring."""
        ledger = PipelineLedger.get_or_create(_TEST_REPO, 100001)
        assert ledger.ledger_key == f"{_TEST_REPO}:100001"
        assert ledger.target_repo == _TEST_REPO
        assert ledger.issue_number == 100001
        assert ledger.stage_states_json == "{}"
        assert ledger.pr_number is None

    def test_returns_same_record_on_repeat_call(self):
        """get_or_create is idempotent -- it does not mint a second record
        for the same (repo, issue) pair."""
        first = PipelineLedger.get_or_create(_TEST_REPO, 100002)
        first.pr_number = 4242
        first.save()

        second = PipelineLedger.get_or_create(_TEST_REPO, 100002)
        assert second.pr_number == 4242
        assert second.ledger_key == first.ledger_key

    def test_different_issue_numbers_get_distinct_records(self):
        """Two different issue numbers under the same repo never collide."""
        a = PipelineLedger.get_or_create(_TEST_REPO, 100001)
        b = PipelineLedger.get_or_create(_TEST_REPO, 100002)
        assert a.ledger_key != b.ledger_key

    def test_different_repos_same_issue_number_get_distinct_records(self):
        """The same issue number under two different repos never collides --
        the repo is part of the key, not just the issue number."""
        a = PipelineLedger.get_or_create("owner-one/repo", 100001)
        b = PipelineLedger.get_or_create("owner-two/repo", 100001)
        try:
            assert a.ledger_key != b.ledger_key
            assert a.ledger_key == "owner-one/repo:100001"
            assert b.ledger_key == "owner-two/repo:100001"
        finally:
            a.delete()
            b.delete()


class TestPersistenceSurvivesIndependentOfSession:
    """The ledger's whole reason for existing: it is not tied to any
    AgentSession's lifecycle. A write persists and round-trips with no
    session involved at all."""

    def setup_method(self):
        _cleanup(100003)

    def teardown_method(self):
        _cleanup(100003)

    def test_write_persists_and_round_trips(self):
        ledger = PipelineLedger.get_or_create(_TEST_REPO, 100003)
        ledger.stage_states_json = '{"ISSUE": "completed", "PLAN": "in_progress"}'
        ledger.pr_number = 777
        ledger.save()

        reloaded = PipelineLedger.get_or_create(_TEST_REPO, 100003)
        assert reloaded.stage_states_json == '{"ISSUE": "completed", "PLAN": "in_progress"}'
        assert reloaded.pr_number == 777

    def test_ledger_has_no_ttl(self):
        """Unlike DedupRecord's 2h TTL, the pipeline ledger must be durable
        indefinitely -- it has to outlive every AgentSession lifecycle
        event (crash, completion, takeover)."""
        assert PipelineLedger._meta.ttl is None


class TestGetOrCreateSurvivesIndexEmptyWindow:
    """Issue #2395: get_or_create's existence check must be index-independent
    so a live ledger survives a #1720-style rebuild_indexes() window where
    the class-set index (what query.filter reads) is transiently empty."""

    def setup_method(self):
        _cleanup(100010)

    def teardown_method(self):
        _cleanup(100010)

    def test_existence_check_no_longer_uses_query_filter(self, monkeypatch):
        """Populate a ledger, then make query.filter() lie and report the
        class-set index as empty (simulating the #1720 window). get_or_create
        must still find the live record, because its existence check goes
        through the index-independent cls.load(), not query.filter()."""
        live = PipelineLedger.get_or_create(_TEST_REPO, 100010)
        live.stage_states_json = '{"ISSUE": "completed", "PLAN": "in_progress"}'
        live.save()

        original_filter = PipelineLedger.query.filter

        def _lying_filter(*args, **kwargs):
            if kwargs.get("ledger_key") == f"{_TEST_REPO}:100010":
                return []
            return original_filter(*args, **kwargs)

        monkeypatch.setattr(PipelineLedger.query, "filter", _lying_filter)

        found = PipelineLedger.get_or_create(_TEST_REPO, 100010)
        assert found.stage_states_json == '{"ISSUE": "completed", "PLAN": "in_progress"}'

    def test_create_clobber_on_existing_key_is_unreachable(self):
        """A populated ledger must never be reset to "{}" by a subsequent
        get_or_create call for the same key -- the create()-clobber path
        (query.filter false-miss -> create()) is no longer reachable from
        get_or_create's normal resolution flow."""
        ledger = PipelineLedger.get_or_create(_TEST_REPO, 100010)
        ledger.stage_states_json = '{"ISSUE": "completed"}'
        ledger.pr_number = 999
        ledger.save()

        for _ in range(3):
            resolved = PipelineLedger.get_or_create(_TEST_REPO, 100010)
            assert resolved.stage_states_json == '{"ISSUE": "completed"}'
            assert resolved.pr_number == 999


class TestGet:
    """Issue #2395: PipelineLedger.get() is a non-mutating, read-only lookup
    used by the router's stage-query poll path."""

    def setup_method(self):
        _cleanup(100011)

    def teardown_method(self):
        _cleanup(100011)

    def test_returns_none_for_absent_key(self):
        """A never-seen (repo, issue) pair returns None -- and, critically,
        leaves no record behind (get() must never create)."""
        assert PipelineLedger.get(_TEST_REPO, 100011) is None
        assert PipelineLedger.query.filter(ledger_key=f"{_TEST_REPO}:100011") == []

    def test_returns_existing_record_for_present_key(self):
        """get() returns the live record when one already exists, without
        needing get_or_create."""
        created = PipelineLedger.get_or_create(_TEST_REPO, 100011)
        created.stage_states_json = '{"ISSUE": "completed"}'
        created.save()

        found = PipelineLedger.get(_TEST_REPO, 100011)
        assert found is not None
        assert found.ledger_key == created.ledger_key
        assert found.stage_states_json == '{"ISSUE": "completed"}'


class TestGetOrCreateConcurrentCreateRace:
    """Issue #2397: two concurrent get_or_create callers racing to create the
    SAME never-seen ledger must not both call create(). Without the SETNX
    create-lock, both could observe None on the final re-load and both call
    create(), the second unconditionally clobbering the first's freshly
    written stage_states_json. The lock serializes the create step so
    create() runs exactly once and both callers observe the same record."""

    _ISSUE = 100077

    def setup_method(self):
        _cleanup(self._ISSUE)

    def teardown_method(self):
        _cleanup(self._ISSUE)

    def test_concurrent_callers_create_exactly_once(self):
        """Launch two threads that enter get_or_create simultaneously (via a
        barrier) for a brand-new ledger key, with a widened create() window.
        The create-lock must ensure exactly one create() call, one persisted
        record, and a single shared result -- proving the residual create-race
        window is closed, not merely narrowed."""
        create_calls: list[str] = []
        create_calls_lock = threading.Lock()
        # Captured before patching: the real (bound) classmethod.
        original_create = PipelineLedger.create

        def _counting_slow_create(**kwargs):
            with create_calls_lock:
                create_calls.append(kwargs.get("ledger_key"))
            # Widen the create window well past a single load() so that, were
            # the lock absent, a second caller would reliably interleave here
            # and issue its own clobbering create(). Kept comfortably under
            # the loser's re-load wait budget (~0.8s at the default retry
            # constants) so the guarded second caller resolves to this record.
            time.sleep(0.2)
            return original_create(**kwargs)

        barrier = threading.Barrier(2)
        results: dict[str, PipelineLedger] = {}

        def _worker(name: str):
            barrier.wait()
            results[name] = PipelineLedger.get_or_create(_TEST_REPO, self._ISSUE)

        with patch.object(PipelineLedger, "create", staticmethod(_counting_slow_create)):
            t1 = threading.Thread(target=_worker, args=("a",))
            t2 = threading.Thread(target=_worker, args=("b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

        assert not t1.is_alive() and not t2.is_alive(), "worker thread hung"
        # The lock serialized the create step: create() ran exactly once.
        assert len(create_calls) == 1, f"expected exactly one create(), got {create_calls}"
        # Both callers resolved to the same underlying record.
        assert results["a"].ledger_key == results["b"].ledger_key
        assert results["a"].ledger_key == f"{_TEST_REPO}:{self._ISSUE}"
        # Exactly one record persisted in Redis.
        rows = list(PipelineLedger.query.filter(ledger_key=f"{_TEST_REPO}:{self._ISSUE}"))
        assert len(rows) == 1
