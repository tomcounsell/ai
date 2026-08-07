"""Job model (Task 13, docs/plans/durability-room-job-agentrun.md).

Job = a responsibility to complete something end to end. Schema ratified
one-shot by the Task 5 schema gate: KeyField set {id, room_id}; a
low-cardinality ``status`` IndexedField (active/at-rest); recency
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


class TestDriftCoverage:
    """Risk 3: drift detection must not silently narrow — Job registers."""

    def test_job_registered_in_drift_coverage(self):
        from agent.index_drift import covered_model_names

        assert "Job" in covered_model_names()

    def test_job_exported_from_models_package(self):
        import models

        assert "Job" in models.__all__
