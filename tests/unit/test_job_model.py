"""Job model (Task 13, docs/plans/durability-room-job-agentrun.md).

Job = a responsibility to complete something end to end. Schema ratified
one-shot by the Task 5 schema gate: KeyField set {id, room_id}; two
low-cardinality IndexedFields, ``status`` (active/at-rest) and the derived
``has_open_promises`` (Schema Gate Amendment 1); recency
``SortedField(partition_by="room_id")``; ``goal`` as an append-only-versioned
plain field. Never hard-closed; rest by age; any steer revives it.

These tests hit real Redis (repo convention) under a ``test-`` room-id
prefix and delete every record via the ORM afterward.
"""

import uuid

import pytest

from models.job import (
    GOAL_PLACEHOLDER_PREFIX,
    Job,
    mint_placeholder_goal,
)


@pytest.fixture
def scratch_room_id():
    """Unique test- prefixed room id; ORM-scoped cleanup afterward."""
    rid = f"test-jobroom-{uuid.uuid4().hex[:8]}|telegram:1"
    yield rid
    for job in Job.query.filter(room_id=rid):
        job.delete()


class TestPlaceholderGoal:
    def test_placeholder_is_mechanical_first_20_chars(self):
        text = "please fix the flaky test in tests/unit/test_foo.py"
        assert mint_placeholder_goal(text) == f"handle user message '{text[:20]}…'"

    def test_short_message_gets_no_ellipsis(self):
        assert mint_placeholder_goal("hi") == "handle user message 'hi'"

    def test_empty_message_still_yields_a_goal(self):
        # goal is never null — even an empty trigger mints a valid placeholder
        assert mint_placeholder_goal("").startswith(GOAL_PLACEHOLDER_PREFIX)


class TestMint:
    def test_mint_creates_active_job_with_placeholder_goal(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "please fix the flaky test")

        assert job.room_id == scratch_room_id
        assert job.status == "active"
        assert job.current_goal() == mint_placeholder_goal("please fix the flaky test")
        assert job.goal_is_placeholder()

    def test_goal_versioning_appends_never_overwrites(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "please fix the flaky test")
        job.append_goal_version("Fix the flaky test and open a PR", author="pm")

        assert job.current_goal() == "Fix the flaky test and open a PR"
        assert not job.goal_is_placeholder()
        versions = job.goal_versions()
        assert len(versions) == 2
        assert versions[0]["author"] == "router"
        assert versions[1]["author"] == "pm"

    def test_goal_round_trips_through_redis(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "please fix the flaky test")
        job.append_goal_version("Authored goal", author="pm")

        reloaded = Job.query.filter(room_id=scratch_room_id)[0]
        assert reloaded.current_goal() == "Authored goal"
        assert len(reloaded.goal_versions()) == 2


class TestPromises:
    def test_add_promise_appends_open_entry(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        pid = job.add_promise("I'll report back after the deploy finishes")

        open_ = job.open_promises()
        assert len(open_) == 1
        assert open_[0]["id"] == pid
        assert open_[0]["text"] == "I'll report back after the deploy finishes"

    def test_remove_promise_is_append_only(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        pid = job.add_promise("I'll report back")
        assert job.remove_promise(pid) is True

        assert job.open_promises() == []
        # Append-only: the discharged entry stays, with removed_ts set.
        all_promises = job.all_promises()
        assert len(all_promises) == 1
        assert all_promises[0]["removed_ts"] is not None

    def test_remove_unknown_promise_returns_false(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        assert job.remove_promise("nope") is False


class TestLifecycle:
    def test_rest_and_revive_never_hard_closed(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        job.mark_at_rest()
        assert job.status == "at-rest"

        job.revive()
        assert job.status == "active"

    def test_sweep_to_rest_transitions_stale_active_jobs(self, scratch_room_id):
        import time
        from datetime import UTC, datetime

        from models.job import JOB_AT_REST_AGE_SECONDS

        stale = Job.mint(scratch_room_id, "old thread")
        stale.last_active_at = datetime.fromtimestamp(
            time.time() - JOB_AT_REST_AGE_SECONDS - 60, tz=UTC
        )
        stale.save()
        fresh = Job.mint(scratch_room_id, "current thread")

        rested = Job.sweep_to_rest()

        assert rested >= 1
        by_id = {j.job_id: j for j in Job.query.filter(room_id=scratch_room_id)}
        assert by_id[stale.job_id].status == "at-rest"
        assert by_id[fresh.job_id].status == "active"

    def test_at_rest_with_open_promises_query(self, scratch_room_id):
        resting = Job.mint(scratch_room_id, "check the deploy")
        resting.add_promise("I'll report back")
        resting.mark_at_rest()

        clean = Job.mint(scratch_room_id, "another thing")
        clean.mark_at_rest()

        flagged = Job.at_rest_with_open_promises()
        flagged_ids = {j.job_id for j in flagged}
        assert resting.job_id in flagged_ids
        assert clean.job_id not in flagged_ids


class TestOpenPromiseIndex:
    """The derived ``has_open_promises`` flag that bounds the at-rest backstop.

    The flag is a projection of ``goal``, so what matters is that it tracks
    every promise mutation and that a stale flag can never change the answer.
    """

    def test_flag_tracks_promise_lifecycle(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        assert job.has_open_promises is False

        promise_id = job.add_promise("I'll report back")
        assert job.has_open_promises is True
        assert Job.query.get(id=job.id, room_id=scratch_room_id).has_open_promises is True

        job.remove_promise(promise_id)
        assert job.has_open_promises is False
        assert Job.query.get(id=job.id, room_id=scratch_room_id).has_open_promises is False

    def test_flag_stays_true_while_any_promise_is_open(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "two things")
        first = job.add_promise("one")
        job.add_promise("two")

        job.remove_promise(first)

        assert job.has_open_promises is True
        assert job.open_promises()

    def test_flag_is_a_real_bool_not_a_truthy_string(self, scratch_room_id):
        """``IndexedField(type=bool)`` is load-bearing.

        Without the declared type the value round-trips as the string
        ``"False"``, which is truthy, so every Job would look promise-owing.
        """
        job = Job.mint(scratch_room_id, "no promises here")
        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.has_open_promises is False
        assert not reloaded.has_open_promises

    def test_backstop_hydrates_only_the_flagged_set(self, scratch_room_id):
        """The bound is the point: work must not scale with the at-rest set."""
        owing = Job.mint(scratch_room_id, "owes a reply")
        owing.add_promise("I'll report back")
        owing.mark_at_rest()
        for i in range(12):
            noise = Job.mint(scratch_room_id, f"settled {i}")
            noise.mark_at_rest()

        import json as _json

        calls = {"n": 0}
        real_loads = _json.loads

        def counting_loads(*args, **kwargs):
            calls["n"] += 1
            return real_loads(*args, **kwargs)

        _json.loads = counting_loads
        try:
            flagged = Job.at_rest_with_open_promises()
        finally:
            _json.loads = real_loads

        assert {j.job_id for j in flagged} >= {owing.job_id}
        assert calls["n"] < 12, (
            f"backstop parsed {calls['n']} goals; it must not scan the at-rest set"
        )

    def test_stale_flag_never_produces_a_wrong_answer(self, scratch_room_id):
        """A flag that says True with no open promise must be filtered out.

        ``goal`` stays authoritative, so the re-verification against
        ``open_promises()`` is what keeps a drifted projection harmless.
        """
        job = Job.mint(scratch_room_id, "settled but mislabelled")
        job.mark_at_rest()
        job.has_open_promises = True
        job.save()

        flagged = Job.at_rest_with_open_promises()

        assert job.job_id not in {j.job_id for j in flagged}

    def test_backfill_stamps_a_row_the_flag_missed(self, scratch_room_id):
        """Legacy rows predate the field and index as nothing until stamped."""
        job = Job.mint(scratch_room_id, "owes a reply")
        job.add_promise("I'll report back")
        job.mark_at_rest()
        goal_before = job.goal

        # Simulate the legacy shape: promise in the goal, flag never derived.
        job.has_open_promises = False
        job.save()
        assert job.job_id not in {j.job_id for j in Job.at_rest_with_open_promises()}

        stamped = Job.backfill_open_promises_index()

        assert stamped >= 1
        assert job.job_id in {j.job_id for j in Job.at_rest_with_open_promises()}
        # Write scope: the backfill's write must never touch goal.
        reloaded = Job.query.get(id=job.id, room_id=job.room_id)
        assert reloaded.goal == goal_before

    def test_backfill_is_idempotent(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "owes a reply")
        job.add_promise("I'll report back")
        job.mark_at_rest()

        Job.backfill_open_promises_index()
        second = Job.backfill_open_promises_index()

        assert second == 0, "a settled population must need no rewrites"

    def test_backfill_does_not_clobber_a_concurrent_promise(self, scratch_room_id, monkeypatch):
        """Red-state proof (#2647): a bare save() in the backfill loop clobbers a
        concurrent promise write. This must fail on main with promises_survived=0
        and pass on the fix with promises_survived=1.

        A sequential "mutate, then call the method" test cannot reproduce the
        race, because QueryBuilder.__iter__ hydrates at call time — so the
        enumeration must be forced stale via monkeypatch to hold a snapshot
        across the concurrent mutation.
        """
        job = Job.mint(scratch_room_id, "owes a reply")
        snap = Job.query.get(id=job.id, room_id=job.room_id)
        snap.has_open_promises = True
        snap.save()  # creates the flag-vs-goal disagreement that makes the write fire

        live = Job.query.get(id=job.id, room_id=job.room_id)
        live.add_promise("promise A")

        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [snap])
        Job.backfill_open_promises_index()

        reloaded = Job.query.get(id=job.id, room_id=job.room_id)
        promises_survived = 1 if reloaded.open_promises() else 0
        assert promises_survived == 1, "the concurrent promise must survive the backfill"

    def test_backfill_write_is_scoped_to_the_flag(self, scratch_room_id, monkeypatch):
        """Write-scope pin: the backfill's save() must list only
        ``has_open_promises``, never a bare save(). Pinned directly with a
        save-spy rather than inferred from side effects, so a future edit that
        drops update_fields= or widens the list is caught here rather than only
        by a one-shot manual Verification grep.
        """
        snap = Job.mint(scratch_room_id, "owes a reply")  # hydrated before any promise exists
        live = Job.query.get(id=snap.id, room_id=snap.room_id)
        live.add_promise("promise A")

        # Drift the stored flag with a scoped write so the backfill's write fires.
        drift = Job.query.get(id=snap.id, room_id=snap.room_id)
        drift.has_open_promises = False
        drift.save(update_fields=["has_open_promises"])

        last_active_before = Job.query.get(id=snap.id, room_id=snap.room_id).last_active_at

        calls = []
        orig_save = Job.save

        def spy(self, *args, **kwargs):
            calls.append(kwargs.get("update_fields"))
            return orig_save(self, *args, **kwargs)

        monkeypatch.setattr(Job, "save", spy)
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [snap])
        stamped = Job.backfill_open_promises_index()

        assert stamped == 1
        assert calls == [["has_open_promises"]]
        reloaded = Job.query.get(id=snap.id, room_id=snap.room_id)
        assert reloaded.open_promises()
        assert reloaded.last_active_at == last_active_before

    def test_backfill_per_row_failure_is_logged_and_siblings_still_stamped(
        self, scratch_room_id, monkeypatch
    ):
        """Fail-open contract: a re-fetch that raises for one row must not stop
        the sweep from stamping its siblings, and must not raise into the
        maintenance path. This pins the re-fetch's placement inside the
        per-row try.
        """
        j1 = Job.mint(scratch_room_id, "first")
        j1.add_promise("promise A")
        j2 = Job.mint(scratch_room_id, "second")
        j2.add_promise("promise B")

        # Drift both rows into flag-vs-goal disagreement so the write fires.
        for j in (j1, j2):
            d = Job.query.get(id=j.id, room_id=j.room_id)
            d.has_open_promises = False
            d.save(update_fields=["has_open_promises"])

        real_get = Job.query.get

        def boom(*args, **kwargs):
            if kwargs.get("id") == j1.id:
                raise RuntimeError("boom")
            return real_get(*args, **kwargs)

        monkeypatch.setattr(Job.query, "get", boom)
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [j1, j2])

        stamped = Job.backfill_open_promises_index()

        assert stamped == 1
        assert Job.query.get(id=j2.id, room_id=j2.room_id).has_open_promises is True

    def test_backfill_whole_loop_failure_returns_zero(self, scratch_room_id, monkeypatch):
        """Fail-open contract: a broken enumeration returns 0 rather than
        raising into repair_indexes()."""

        def boom(*args, **kwargs):
            raise RuntimeError("redis is unhappy")

        monkeypatch.setattr(Job.query, "filter", boom)

        assert Job.backfill_open_promises_index() == 0

    def test_backstop_never_raises_into_the_health_cycle(self, scratch_room_id, monkeypatch):
        """Fail-open contract: a broken query returns [] rather than raising."""

        def boom(*args, **kwargs):
            raise RuntimeError("redis is unhappy")

        monkeypatch.setattr(Job.query, "filter", boom)

        assert Job.at_rest_with_open_promises() == []


class TestRecencyLookup:
    def test_recent_for_room_returns_newest_first_capped(self, scratch_room_id):
        jobs = [Job.mint(scratch_room_id, f"task {i}") for i in range(7)]

        recent = Job.recent_for_room(scratch_room_id, limit=5)
        assert len(recent) == 5
        # Newest first: the last-minted job leads.
        assert recent[0].job_id == jobs[-1].job_id

    def test_recent_for_room_empty_room_is_empty(self):
        assert Job.recent_for_room("test-jobroom-none|telegram:0") == []


class TestGuardedRepair:
    """Risk 2 (#2207): Job never enters the generic rebuild_indexes sweep."""

    def test_job_listed_in_guarded_elsewhere(self):
        from scripts.popoto_index_cleanup import _GUARDED_ELSEWHERE

        assert "Job" in _GUARDED_ELSEWHERE

    def test_repair_quarantines_identity_less_hash_and_its_index_member(self, scratch_room_id):
        """Leg 1 deletes the phantom hash; leg 2 must clear its $IndexF
        membership too — the raw delete bypasses on_delete's SREM and
        popoto's rebuild_indexes() never enumerates $IndexF sets, so
        without leg 2 the member leaks permanently."""
        from popoto.redis_db import POPOTO_REDIS_DB

        status_index_key = "$IndexF:Job:status:active"
        job = Job.mint(scratch_room_id, "real job")
        phantom_key = f"Job:test-phantom-{uuid.uuid4().hex[:8]}"
        POPOTO_REDIS_DB.hset(phantom_key, mapping={"status": "active"})
        # Simulate what on_save would have done for the phantom: index it.
        POPOTO_REDIS_DB.sadd(status_index_key, phantom_key)
        try:
            quarantined, _rebuilt = Job.repair_indexes()
            assert quarantined >= 1
            assert POPOTO_REDIS_DB.exists(phantom_key) == 0
            # The leak: the phantom's index membership must be gone too.
            assert not POPOTO_REDIS_DB.sismember(status_index_key, phantom_key)
            # The real record survives — reachable AND re-indexed.
            assert any(j.job_id == job.job_id for j in Job.query.filter(room_id=scratch_room_id))
            fresh = Job.query.filter(room_id=scratch_room_id)[0]
            assert fresh.status == "active"
            assert list(Job.query.filter(status="active"))  # index serves queries again
        finally:
            POPOTO_REDIS_DB.delete(phantom_key)
            POPOTO_REDIS_DB.srem(status_index_key, phantom_key)

    def test_stale_member_scan_is_pipelined_across_batch_boundary(
        self, scratch_room_id, monkeypatch, caplog
    ):
        """Leg 2's existence checks must be pipelined, not one round trip per
        member: a bloated index (this repo has seen millions of stale
        pointers) turns the daily maintenance path into a multi-hour hang.

        Mirrors ``AgentSession.repair_indexes``' batching. The member count
        straddles the 5000 batch boundary so the multi-batch loop is exercised,
        and every member is stale (no backing hash) so the count is exact.
        """
        import logging

        from popoto.redis_db import POPOTO_REDIS_DB

        status_index_key = "$IndexF:Job:status:active"
        Job.mint(scratch_room_id, "real job")
        member_count = 5001  # one past a single batch
        stale_members = [f"Job:test-stale-{uuid.uuid4().hex[:8]}-{i}" for i in range(member_count)]
        POPOTO_REDIS_DB.sadd(status_index_key, *stale_members)

        direct_exists_calls = 0
        real_exists = POPOTO_REDIS_DB.exists

        def counting_exists(*args, **kwargs):
            nonlocal direct_exists_calls
            direct_exists_calls += 1
            return real_exists(*args, **kwargs)

        monkeypatch.setattr(POPOTO_REDIS_DB, "exists", counting_exists)
        try:
            with caplog.at_level(logging.WARNING, logger="models.job"):
                Job.repair_indexes()

            # Behavior is identical to the unbatched scan: every stale member
            # is counted, and the whole index key is cleared and rebuilt.
            assert f"cleared {member_count} stale $IndexF member(s)" in caplog.text
            for member in stale_members[:3]:
                assert not POPOTO_REDIS_DB.sismember(status_index_key, member)
            # ...but no per-member round trip: the scan issues zero direct
            # exists() calls, it batches them into pipelines instead.
            assert direct_exists_calls < member_count // 10
        finally:
            monkeypatch.setattr(POPOTO_REDIS_DB, "exists", real_exists)
            POPOTO_REDIS_DB.srem(status_index_key, *stale_members)


class TestDriftCoverage:
    """Risk 3: drift detection must not silently narrow — Job registers."""

    def test_job_registered_in_drift_coverage(self):
        from agent.index_drift import covered_model_names

        assert "Job" in covered_model_names()

    def test_job_exported_from_models_package(self):
        import models

        assert "Job" in models.__all__
