"""Job model (Task 13, docs/plans/durability-room-job-agentrun.md).

Job = a responsibility to complete something end to end. Schema ratified
one-shot by the Task 5 schema gate: KeyField set {id, room_id}; two
low-cardinality IndexedFields, ``status`` (active/at-rest) and the derived
``has_open_expectations`` (Schema Gate Amendment 2); recency
``SortedField(partition_by="room_id")``; ``goal`` as an append-only-versioned
plain field carrying the expectation entries. Never hard-closed; rest by age
unless an expectation is open; any steer revives it.

These tests hit real Redis (repo convention) under a ``test-`` room-id
prefix and delete every record via the ORM afterward.
"""

import json
import uuid

import pytest

from models.job import (
    GOAL_PLACEHOLDER_PREFIX,
    JOB_AT_REST_AGE_SECONDS,
    JOB_RECENT_OVERFETCH,
    CorruptGoalError,
    Job,
    mint_placeholder_goal,
)

# Spike-2's measured rebuild skew on a UTC+07 host: 7 hours in seconds.
UTC_PLUS_7_REBUILD_SKEW_SECONDS = 25200.0


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


class TestExpectations:
    """The single obligation primitive: (direction, holder, owner, what)."""

    def test_inbound_expectation_defaults_to_the_requester_shape(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        eid = job.add_expectation("I'll report back after the deploy finishes")

        open_ = job.open_expectations()
        assert len(open_) == 1
        entry = open_[0]
        assert entry["id"] == eid
        assert entry["what"] == "I'll report back after the deploy finishes"
        assert entry["direction"] == "inbound"
        assert entry["holder"] == "requester"
        assert entry["owner"] == "pm"

    def test_outbound_expectation_records_holder_and_owner(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "ship the lane")
        job.add_expectation(
            "deliver the migration PR",
            direction="outbound",
            holder="sdlc-local-7",
            owner="session/job-expectations",
        )

        entry = job.open_expectations()[0]
        assert entry["direction"] == "outbound"
        assert entry["holder"] == "sdlc-local-7"
        assert entry["owner"] == "session/job-expectations"

    def test_placeholder_marker_round_trips(self, scratch_room_id):
        """The spawn-time null-fallback marks its mechanical entry so the PM
        prime's refine nudge can key on it (provenance-derived, #2708)."""
        job = Job.mint(scratch_room_id, "ship the lane")
        job.add_expectation(
            "handle spawn instruction",
            direction="outbound",
            holder="pm",
            owner="lane-1",
            placeholder=True,
        )

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.open_expectations()[0]["placeholder"] is True

    def test_discharge_is_append_only(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        eid = job.add_expectation("I'll report back")
        assert job.discharge_expectation(eid) is True

        assert job.open_expectations() == []
        # Append-only: the discharged entry stays, with removed_ts set.
        history = job.all_expectations()
        assert len(history) == 1
        assert history[0]["removed_ts"] is not None

    def test_discharge_unknown_expectation_returns_false(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        assert job.discharge_expectation("nope") is False

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"what": "  "}, "what"),
            ({"what": "deliver", "direction": "outbound", "holder": "pm", "owner": ""}, "owner"),
            ({"what": "deliver", "direction": "sideways"}, "direction"),
        ],
    )
    def test_unownable_or_unaddressed_expectation_is_rejected(
        self, scratch_room_id, kwargs, fragment
    ):
        """An expectation with no owner or no text is unreconcilable — worse
        than none, so it is refused at the write rather than stored."""
        job = Job.mint(scratch_room_id, "check the deploy")

        with pytest.raises(ValueError, match=fragment):
            job.add_expectation(**kwargs)

        assert job.open_expectations() == []


class TestStatusProjection:
    """``status`` is chokepoint-maintained: an open expectation ⇒ active."""

    def test_recording_an_expectation_forces_active(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        job.mark_at_rest()
        assert job.status == "at-rest"

        job.add_expectation("I'll report back")

        assert job.status == "active"
        assert Job.query.get(id=job.id, room_id=scratch_room_id).status == "active"

    def test_discharge_does_not_force_rest(self, scratch_room_id):
        """Rest is age-derived, never an instant consequence of discharge."""
        job = Job.mint(scratch_room_id, "check the deploy")
        eid = job.add_expectation("I'll report back")
        job.discharge_expectation(eid)

        assert job.status == "active"


class TestLifecycle:
    def test_rest_and_revive_never_hard_closed(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        job.mark_at_rest()
        assert job.status == "at-rest"

        job.revive()
        assert job.status == "active"

    def test_sweep_to_rest_transitions_stale_active_jobs(self, scratch_room_id):
        stale = _age_out(Job.mint(scratch_room_id, "old thread"))
        fresh = Job.mint(scratch_room_id, "current thread")

        rested = Job.sweep_to_rest()

        assert rested >= 1
        by_id = {j.job_id: j for j in Job.query.filter(room_id=scratch_room_id)}
        assert by_id[stale.job_id].status == "at-rest"
        assert by_id[fresh.job_id].status == "active"

    def test_open_expectation_blocks_rest_but_an_empty_one_still_ages_out(self, scratch_room_id):
        """Under-recording degrades to today's behavior (rest by age), never
        to a false "done": only a *recorded* open expectation pins a Job open.
        """
        owing = Job.mint(scratch_room_id, "owes a lane")
        owing.add_expectation("deliver the PR", direction="outbound", holder="pm", owner="lane-1")
        _age_out(owing)
        idle = _age_out(Job.mint(scratch_room_id, "nothing recorded"))

        Job.sweep_to_rest()

        by_id = {j.job_id: j for j in Job.query.filter(room_id=scratch_room_id)}
        assert by_id[owing.job_id].status == "active"
        assert by_id[idle.job_id].status == "at-rest"

    def test_at_rest_with_open_expectations_query(self, scratch_room_id):
        resting = Job.mint(scratch_room_id, "check the deploy")
        resting.add_expectation("I'll report back")
        # Reaching at-rest with an open expectation is an invariant violation
        # by construction now — force it to prove the alarm still fires.
        resting.mark_at_rest()

        clean = Job.mint(scratch_room_id, "another thing")
        clean.mark_at_rest()

        flagged = Job.at_rest_with_open_expectations()
        flagged_ids = {j.job_id for j in flagged}
        assert resting.job_id in flagged_ids
        assert clean.job_id not in flagged_ids


def _age_out(job: Job) -> Job:
    """Backdate ``last_active_at`` past the rest threshold."""
    import time
    from datetime import UTC, datetime

    from models.job import JOB_AT_REST_AGE_SECONDS

    job.last_active_at = datetime.fromtimestamp(time.time() - JOB_AT_REST_AGE_SECONDS - 60, tz=UTC)
    job.save()
    return job


def _status_index_key(status: str) -> str:
    """The ``status`` IndexedField's Set key for a value — derived via the
    field API (``IndexedFieldMixin.filter_query``'s own key-build pattern),
    never hand-built with an f-string."""
    from popoto.models.db_key import DB_key

    field = Job._meta.fields["status"]
    prefix = field.get_special_use_field_db_key(Job, "status")
    return DB_key(prefix, status).redis_key


class TestFieldScopedLifecycleSaves:
    """#2860: ``touch``/``mark_at_rest``/``revive`` must scope their save to
    only the field(s) they mutate, so a concurrent ``goal`` write (an
    expectation add/discharge on a second in-memory instance) is never
    clobbered by the whole-hash rewrite a bare ``save()`` would perform."""

    def test_touch_preserves_a_concurrent_goal_write(self, scratch_room_id):
        from utils.utc import to_unix_ts

        a = Job.mint(scratch_room_id, "check the deploy")
        b = Job.query.get(id=a.id, room_id=scratch_room_id)
        eid = b.add_expectation("I'll report back")
        before = to_unix_ts(a.last_active_at)

        a.touch()

        reloaded = Job.query.get(id=a.id, room_id=scratch_room_id)
        assert eid in {e["id"] for e in reloaded.open_expectations()}
        assert to_unix_ts(reloaded.last_active_at) > before

    def test_revive_preserves_a_concurrent_goal_write(self, scratch_room_id):
        from utils.utc import to_unix_ts

        a = Job.mint(scratch_room_id, "check the deploy")
        a.mark_at_rest()
        b = Job.query.get(id=a.id, room_id=scratch_room_id)
        eid = b.add_expectation("I'll report back")
        before = to_unix_ts(a.last_active_at)

        a.revive()

        reloaded = Job.query.get(id=a.id, room_id=scratch_room_id)
        assert reloaded.status == "active"
        assert to_unix_ts(reloaded.last_active_at) > before
        assert eid in {e["id"] for e in reloaded.open_expectations()}

    def test_mark_at_rest_preserves_a_concurrent_goal_write(self, scratch_room_id):
        a = Job.mint(scratch_room_id, "check the deploy")
        b = Job.query.get(id=a.id, room_id=scratch_room_id)
        eid = b.add_expectation("I'll report back")

        a.mark_at_rest()

        reloaded = Job.query.get(id=a.id, room_id=scratch_room_id)
        assert reloaded.status == "at-rest"
        assert eid in {e["id"] for e in reloaded.open_expectations()}

    def test_mark_at_rest_does_not_refresh_recency(self, scratch_room_id):
        from utils.utc import to_unix_ts

        job = Job.mint(scratch_room_id, "check the deploy")
        before_ts = to_unix_ts(job.last_active_at)
        [(member, score_before)] = _scores(scratch_room_id)

        job.mark_at_rest()

        assert to_unix_ts(job.last_active_at) == before_ts
        assert _scores(scratch_room_id) == [(member, score_before)]
        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert to_unix_ts(reloaded.last_active_at) == before_ts

    @pytest.mark.parametrize("method", ["touch", "revive"])
    def test_touch_and_revive_still_score_correctly(self, scratch_room_id, method):
        from utils.utc import to_unix_ts

        job = Job.mint(scratch_room_id, "check the deploy")
        getattr(job, method)()

        [(_member, score)] = _scores(scratch_room_id)
        assert score == pytest.approx(to_unix_ts(job.last_active_at), abs=1.0)

    def test_mark_at_rest_updates_the_raw_status_index_set(self, scratch_room_id):
        """Index maintenance under a scoped save, asserted at the Redis layer
        (not merely inferred from an ORM round trip)."""
        from popoto.redis_db import POPOTO_REDIS_DB

        job = Job.mint(scratch_room_id, "check the deploy")
        member_key = job.db_key.redis_key
        assert POPOTO_REDIS_DB.sismember(_status_index_key("active"), member_key)

        job.mark_at_rest()

        assert not POPOTO_REDIS_DB.sismember(_status_index_key("active"), member_key)
        assert POPOTO_REDIS_DB.sismember(_status_index_key("at-rest"), member_key)


class TestGoalSelfHeal:
    """``_goal_data()`` is total: no goal shape can make a READ raise.

    The shapes here are ones this system's own writer can plausibly leave
    behind (a null field, a non-object value, a wrong-typed key). They coerce
    to empty AND stay writable. Bytes that do not decode at all are a
    different category, covered by :class:`TestCorruptGoal`.
    """

    @pytest.mark.parametrize("raw", [None, "", "[]", '{"versions": null}'])
    def test_malformed_goal_reads_as_empty(self, scratch_room_id, raw):
        job = Job.mint(scratch_room_id, "check the deploy")
        job.goal = raw
        job.save()

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.open_expectations() == []
        assert reloaded.all_expectations() == []
        assert reloaded.goal_versions() == []
        assert reloaded.current_goal() == ""

    @pytest.mark.parametrize("raw", [None, "", "[]", '{"versions": null}'])
    def test_benign_shapes_are_not_corrupt_and_stay_writable(self, scratch_room_id, raw):
        """The permissive coercion is kept for our own writer's shapes: they
        are not flagged as corrupt, and a mutation proceeds normally."""
        job = Job.mint(scratch_room_id, "check the deploy")
        job.goal = raw
        job.save()

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.goal_is_corrupt() is False
        eid = reloaded.add_expectation("I'll report back")

        fresh = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert [e["id"] for e in fresh.open_expectations()] == [eid]

    def test_malformed_entries_cannot_lose_the_goal_write(self, scratch_room_id):
        """Derivation happens before the save, so a junk entry costs a wrong
        flag at worst — never the goal bytes."""
        job = Job.mint(scratch_room_id, "check the deploy")
        job._write_goal_data({"versions": [], "expectations": ["junk", None, 7]})

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert json.loads(reloaded.goal)["expectations"] == ["junk", None, 7]
        assert reloaded.has_open_expectations is False

    def test_stray_promises_key_is_absorbed_on_every_read(self, scratch_room_id):
        """Race 4: old code that lands a ``promises`` write after the migration
        must not become invisible. The merge is per-read, not one-shot, and
        preserves ids, timestamps, and discharge history."""
        job = Job.mint(scratch_room_id, "check the deploy")
        data = json.loads(job.goal)
        data["promises"] = [
            {
                "id": "legacy1",
                "ts": "2026-08-01T00:00:00+00:00",
                "text": "I'll report back",
                "removed_ts": None,
            },
            {
                "id": "legacy2",
                "ts": "2026-08-02T00:00:00+00:00",
                "text": "already delivered",
                "removed_ts": "2026-08-03T00:00:00+00:00",
            },
        ]
        job.goal = json.dumps(data)
        job.save()

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        history = reloaded.all_expectations()

        assert [e["id"] for e in history] == ["legacy1", "legacy2"]
        assert [e["what"] for e in history] == ["I'll report back", "already delivered"]
        assert {e["direction"] for e in history} == {"inbound"}
        assert history[0]["holder"] == "requester"
        assert history[0]["owner"] == "pm"
        assert history[0]["ts"] == "2026-08-01T00:00:00+00:00"
        assert history[1]["removed_ts"] == "2026-08-03T00:00:00+00:00"
        assert [e["id"] for e in reloaded.open_expectations()] == ["legacy1"]

    def test_absorption_converges_without_duplicating(self, scratch_room_id):
        """Absorbed entries survive as expectations and are never doubled —
        neither by repeated reads nor by the write that persists the merge."""
        job = Job.mint(scratch_room_id, "check the deploy")
        data = json.loads(job.goal)
        data["promises"] = [
            {"id": "legacy1", "ts": "2026-08-01T00:00:00+00:00", "text": "x", "removed_ts": None}
        ]
        job.goal = json.dumps(data)
        job.save()

        # Repeated reads before any write must not accumulate copies.
        job = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert len(job.all_expectations()) == 1
        assert len(job.all_expectations()) == 1

        job.add_expectation("a second obligation")

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        ids = [e["id"] for e in reloaded.all_expectations()]
        assert ids.count("legacy1") == 1
        assert len(ids) == 2
        # The obligation survived the conversion; only the retired key is gone.
        assert "legacy1" in ids
        assert "promises" not in json.loads(reloaded.goal)


# Bytes a truncated write leaves behind: mostly-valid JSON that no longer
# decodes. Whatever is recoverable from it lives only in these bytes.
CORRUPT_GOAL = '{"versions": [{"ts": "2026-08-01T00:00:00+00:00", "author": "pm", "text": "Ship'


def _corrupt(job: Job) -> Job:
    """Replace the stored goal with undecodable bytes, keeping the projection
    the instance already carries (a truncated write never touches the flag)."""
    job.goal = CORRUPT_GOAL
    job.save()
    return Job.query.get(id=job.id, room_id=job.room_id)


@pytest.fixture
def sentry_captures(monkeypatch):
    import sentry_sdk

    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sentry_sdk, "capture_message", lambda msg, level="info", **_: captured.append((msg, level))
    )
    return captured


class TestCorruptGoal:
    """#2862 Part 2: undecodable goal bytes fail CLOSED on every write.

    Reads stay tolerant (empty, so no unrelated caller crashes) but loud
    (ERROR log + Sentry). The read-modify-write pair is where the data loss
    lived: ``_goal_data()`` parsed corruption as ``{}`` and the next
    ``_write_goal_data`` persisted that emptiness over the only copy of the
    original bytes. Every assertion here is on the stored bytes, not the
    accessor, because the accessor is exactly what used to lie.
    """

    def test_corrupt_goal_is_a_distinct_condition_from_shape_coercion(self, scratch_room_id):
        job = _corrupt(Job.mint(scratch_room_id, "check the deploy"))

        assert job.goal_is_corrupt() is True
        # Reads still answer (tolerant), so unrelated callers keep working.
        assert job.open_expectations() == []
        assert job.current_goal() == ""

    def test_read_is_loud_error_log_and_one_sentry_event(
        self, scratch_room_id, caplog, sentry_captures
    ):
        import logging

        job = _corrupt(Job.mint(scratch_room_id, "check the deploy"))

        with caplog.at_level(logging.ERROR, logger="models.job"):
            job.open_expectations()
            job.current_goal()
            job.goal_versions()

        errors = [
            r for r in caplog.records if r.levelno == logging.ERROR and "CORRUPT" in r.message
        ]
        assert len(errors) == 3, "every read logs; the log is the signal of record"
        assert all(job.job_id in r.message for r in errors)
        # Sentry is deduplicated per process per Job so cadence readers cannot
        # flood one bad row into thousands of events.
        assert len(sentry_captures) == 1
        msg, level = sentry_captures[0]
        assert job.job_id in msg
        assert level == "error"

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda j: j.add_expectation("I'll report back"),
            lambda j: j.append_goal_version("Real goal v2", author="pm"),
            lambda j: j.discharge_expectation("anything"),
            lambda j: j._write_goal_data({"versions": [], "expectations": []}),
        ],
        ids=["add_expectation", "append_goal_version", "discharge_expectation", "chokepoint"],
    )
    def test_mutation_after_corruption_preserves_the_original_bytes(
        self, scratch_room_id, sentry_captures, mutate
    ):
        """The read-modify-write sequence specifically: corrupt, then mutate,
        and the stored bytes are byte-identical afterwards."""
        job = Job.mint(scratch_room_id, "check the deploy")
        job.add_expectation("I'll report back")  # projection True before corruption
        job = _corrupt(job)

        with pytest.raises(CorruptGoalError) as excinfo:
            mutate(job)
        assert job.job_id in str(excinfo.value)

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.goal == CORRUPT_GOAL
        assert reloaded.has_open_expectations is True
        assert reloaded.status == "active"

    def test_backfill_never_rederives_the_flag_from_an_empty_parse(self, scratch_room_id):
        """The second destruction path: the daily backfill derives the flag
        from ``_goal_data()``, which reads corruption as empty. Without the
        skip it would stamp ``False`` and drop the Job out of the reconciler's
        index; the stored flag is the last known truth and stays."""
        job = Job.mint(scratch_room_id, "check the deploy")
        job.add_expectation("I'll report back")
        job = _corrupt(job)
        assert job.has_open_expectations is True

        Job.backfill_open_expectations_index()

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.has_open_expectations is True
        assert reloaded.goal == CORRUPT_GOAL

    def test_corrupt_job_stays_in_the_reconciler_scan_root(self, scratch_room_id):
        """``with_open_expectations()`` re-verifies each flagged row against
        the goal. A corrupt goal cannot disprove the flag, so the Job is
        retained rather than silently presenting as obligation-free."""
        job = Job.mint(scratch_room_id, "check the deploy")
        job.add_expectation("I'll report back")
        job = _corrupt(job)

        assert job.job_id in {j.job_id for j in Job.with_open_expectations()}

        job.mark_at_rest()
        assert job.job_id in {j.job_id for j in Job.at_rest_with_open_expectations()}

    def test_corrupt_job_never_rests_by_age(self, scratch_room_id):
        """Rest-by-age skips a Job with open expectations. A corrupt goal
        cannot prove its obligations are met, so it is pinned active until a
        human repairs it (corruption is the case that most needs one)."""
        import time

        job = _corrupt(Job.mint(scratch_room_id, "check the deploy"))

        far_future = time.time() + JOB_AT_REST_AGE_SECONDS * 10
        Job.sweep_to_rest(now=far_future)

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.status == "active"
        assert reloaded.goal == CORRUPT_GOAL

    def test_non_string_goal_value_is_corrupt(self, scratch_room_id):
        """A non-string in the field is also not something this system wrote
        intact (the ``TypeError`` branch of the decode)."""
        job = Job.mint(scratch_room_id, "check the deploy")
        job.goal = 12345
        assert job.goal_is_corrupt() is True
        with pytest.raises(CorruptGoalError):
            job.add_expectation("I'll report back")


class TestOpenExpectationIndex:
    """The derived ``has_open_expectations`` flag that bounds the at-rest alarm.

    The flag is a projection of ``goal``, so what matters is that it tracks
    every expectation mutation and that a stale flag can never change the answer.
    """

    def test_flag_tracks_expectation_lifecycle(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        assert job.has_open_expectations is False

        expectation_id = job.add_expectation("I'll report back")
        assert job.has_open_expectations is True
        assert Job.query.get(id=job.id, room_id=scratch_room_id).has_open_expectations is True

        job.discharge_expectation(expectation_id)
        assert job.has_open_expectations is False
        assert Job.query.get(id=job.id, room_id=scratch_room_id).has_open_expectations is False

    def test_flag_stays_true_while_any_expectation_is_open(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "two things")
        first = job.add_expectation("one")
        job.add_expectation("two")

        job.discharge_expectation(first)

        assert job.has_open_expectations is True
        assert job.open_expectations()

    def test_flag_is_a_real_bool_not_a_truthy_string(self, scratch_room_id):
        """``IndexedField(type=bool)`` is load-bearing.

        Without the declared type the value round-trips as the string
        ``"False"``, which is truthy, so every Job would look obligation-owing.
        """
        job = Job.mint(scratch_room_id, "no expectations here")
        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.has_open_expectations is False
        assert not reloaded.has_open_expectations

    def test_alarm_hydrates_only_the_flagged_set(self, scratch_room_id):
        """The bound is the point: work must not scale with the at-rest set."""
        owing = Job.mint(scratch_room_id, "owes a reply")
        owing.add_expectation("I'll report back")
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
            flagged = Job.at_rest_with_open_expectations()
        finally:
            _json.loads = real_loads

        assert {j.job_id for j in flagged} >= {owing.job_id}
        assert calls["n"] < 12, f"alarm parsed {calls['n']} goals; it must not scan the at-rest set"

    def test_stale_flag_never_produces_a_wrong_answer(self, scratch_room_id):
        """A flag that says True with no open expectation must be filtered out.

        ``goal`` stays authoritative, so the re-verification against
        ``open_expectations()`` is what keeps a drifted projection harmless.
        """
        job = Job.mint(scratch_room_id, "settled but mislabelled")
        job.mark_at_rest()
        job.has_open_expectations = True
        job.save()

        flagged = Job.at_rest_with_open_expectations()

        assert job.job_id not in {j.job_id for j in flagged}

    def test_backfill_stamps_a_row_the_flag_missed(self, scratch_room_id):
        """Legacy rows predate the field and index as nothing until stamped."""
        job = Job.mint(scratch_room_id, "owes a reply")
        job.add_expectation("I'll report back")
        job.mark_at_rest()
        goal_before = job.goal

        # Simulate the legacy shape: expectation in the goal, flag never derived.
        job.has_open_expectations = False
        job.save()
        assert job.job_id not in {j.job_id for j in Job.at_rest_with_open_expectations()}

        stamped = Job.backfill_open_expectations_index()

        assert stamped >= 1
        assert job.job_id in {j.job_id for j in Job.at_rest_with_open_expectations()}
        # Write scope: the backfill's write must never touch goal.
        reloaded = Job.query.get(id=job.id, room_id=job.room_id)
        assert reloaded.goal == goal_before

    def test_backfill_is_idempotent(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "owes a reply")
        job.add_expectation("I'll report back")
        job.mark_at_rest()

        Job.backfill_open_expectations_index()
        second = Job.backfill_open_expectations_index()

        assert second == 0, "a settled population must need no rewrites"

    def test_backfill_does_not_clobber_a_concurrent_expectation(self, scratch_room_id, monkeypatch):
        """Red-state proof (#2647): a bare save() in the backfill loop clobbers a
        concurrent expectation write. This must fail with expectations_survived=0
        on a bare save() and pass on the scoped one.

        A sequential "mutate, then call the method" test cannot reproduce the
        race, because QueryBuilder.__iter__ hydrates at call time — so the
        enumeration must be forced stale via monkeypatch to hold a snapshot
        across the concurrent mutation.
        """
        job = Job.mint(scratch_room_id, "owes a reply")
        snap = Job.query.get(id=job.id, room_id=job.room_id)
        snap.has_open_expectations = True
        snap.save()  # creates the flag-vs-goal disagreement that makes the write fire

        live = Job.query.get(id=job.id, room_id=job.room_id)
        live.add_expectation("expectation A")

        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [snap])
        Job.backfill_open_expectations_index()

        reloaded = Job.query.get(id=job.id, room_id=job.room_id)
        survived = 1 if reloaded.open_expectations() else 0
        assert survived == 1, "the concurrent expectation must survive the backfill"

    def test_backfill_write_is_scoped_to_the_flag(self, scratch_room_id, monkeypatch):
        """Write-scope pin: the backfill's save() must list only
        ``has_open_expectations``, never a bare save(). Pinned directly with a
        save-spy rather than inferred from side effects, so a future edit that
        drops update_fields= or widens the list is caught here rather than only
        by a one-shot manual Verification grep.
        """
        snap = Job.mint(scratch_room_id, "owes a reply")  # hydrated before any entry exists
        live = Job.query.get(id=snap.id, room_id=snap.room_id)
        live.add_expectation("expectation A")

        # Drift the stored flag with a scoped write so the backfill's write fires.
        drift = Job.query.get(id=snap.id, room_id=snap.room_id)
        drift.has_open_expectations = False
        drift.save(update_fields=["has_open_expectations"])

        last_active_before = Job.query.get(id=snap.id, room_id=snap.room_id).last_active_at

        calls = []
        orig_save = Job.save

        def spy(self, *args, **kwargs):
            calls.append(kwargs.get("update_fields"))
            return orig_save(self, *args, **kwargs)

        monkeypatch.setattr(Job, "save", spy)
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [snap])
        stamped = Job.backfill_open_expectations_index()

        assert stamped == 1
        assert calls == [["has_open_expectations"]]
        reloaded = Job.query.get(id=snap.id, room_id=snap.room_id)
        assert reloaded.open_expectations()
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
        j1.add_expectation("expectation A")
        j2 = Job.mint(scratch_room_id, "second")
        j2.add_expectation("expectation B")

        # Drift both rows into flag-vs-goal disagreement so the write fires.
        for j in (j1, j2):
            d = Job.query.get(id=j.id, room_id=j.room_id)
            d.has_open_expectations = False
            d.save(update_fields=["has_open_expectations"])

        real_get = Job.query.get

        def boom(*args, **kwargs):
            if kwargs.get("id") == j1.id:
                raise RuntimeError("boom")
            return real_get(*args, **kwargs)

        monkeypatch.setattr(Job.query, "get", boom)
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [j1, j2])

        stamped = Job.backfill_open_expectations_index()

        assert stamped == 1
        assert Job.query.get(id=j2.id, room_id=j2.room_id).has_open_expectations is True

    def test_backfill_whole_loop_failure_returns_zero(self, scratch_room_id, monkeypatch):
        """Fail-open contract: a broken enumeration returns 0 rather than
        raising into repair_indexes()."""

        def boom(*args, **kwargs):
            raise RuntimeError("redis is unhappy")

        monkeypatch.setattr(Job.query, "filter", boom)

        assert Job.backfill_open_expectations_index() == 0

    def test_alarm_never_raises_into_the_health_cycle(self, scratch_room_id, monkeypatch):
        """Fail-open contract: a broken query returns [] rather than raising."""

        def boom(*args, **kwargs):
            raise RuntimeError("redis is unhappy")

        monkeypatch.setattr(Job.query, "filter", boom)

        assert Job.at_rest_with_open_expectations() == []


def _partition_key(room_id: str) -> str:
    """The ``last_active_at`` sorted-set key for a Room — derived, never built.

    ``DB_key.clean()`` escapes ``:`` and ``/``, and every real room_id contains
    a colon, so a hand-built key reads a set that does not exist (spike-3).
    """
    from popoto import SortedField

    return SortedField.get_sortedset_db_key(Job, "last_active_at", room_id).redis_key


def _scores(room_id: str) -> list[tuple[bytes, float]]:
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB.zrevrange(_partition_key(room_id), 0, -1, withscores=True)


@pytest.fixture
def count_hash_reads(monkeypatch):
    """Count hash reads (the pipelined HGETALL/HMGET hydration path).

    This is the acceptance instrument for the bound: an unbounded range read
    hydrates every Job in the Room, a bounded one hydrates ``limit`` of them,
    and only a read counter tells the two apart — a "fix" that measures
    unchanged is the named failure mode.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    counter = {"n": 0}

    def wrap(obj, name):
        real = getattr(obj, name)

        def counting(*args, **kwargs):
            counter["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(obj, name, counting)

    real_pipeline = POPOTO_REDIS_DB.pipeline

    def counting_pipeline(*args, **kwargs):
        pipe = real_pipeline(*args, **kwargs)
        for name in ("hgetall", "hmget"):
            wrap(pipe, name)
        return pipe

    monkeypatch.setattr(POPOTO_REDIS_DB, "pipeline", counting_pipeline)
    for name in ("hgetall", "hmget"):
        wrap(POPOTO_REDIS_DB, name)
    return counter


class TestRecencyLookup:
    """The bind-or-mint candidate lookup: bounded, newest-first, fail-open."""

    @pytest.mark.parametrize("population", [7, 30])
    def test_recent_for_room_returns_newest_first_capped(self, scratch_room_id, population):
        """Ordering parity with the old hydrate-all-then-sort implementation.

        Timestamps are distinct (mint stamps microsecond ``_now()``): under
        equal scores Python's stable sort and Redis' reverse-lex tiebreak pick
        different subsets at the limit boundary (spike-4), so a constant-stamped
        fixture would assert a coincidence rather than the contract.
        """
        jobs = [Job.mint(scratch_room_id, f"task {i}") for i in range(population)]

        recent = Job.recent_for_room(scratch_room_id, limit=5)

        assert len(recent) == 5
        assert [j.job_id for j in recent] == [j.job_id for j in reversed(jobs[-5:])]

    def test_hydration_scales_with_limit_not_room_size(self, scratch_room_id, count_hash_reads):
        """The bound itself. At 30 Jobs the old path cost 2N=60 hash reads for a
        top-5 answer (popoto's QueryBuilder hydrates twice, #2639); the bounded
        path costs at most one per over-fetched member."""
        for i in range(30):
            Job.mint(scratch_room_id, f"task {i}")
        limit = 5
        count_hash_reads["n"] = 0

        recent = Job.recent_for_room(scratch_room_id, limit=limit)

        assert len(recent) == limit
        # This bound subsumes the mutation ceiling (2·limit + overfetch, far
        # below the old path's 2N=60): an unbounded range read fails it first.
        assert count_hash_reads["n"] <= limit + JOB_RECENT_OVERFETCH, (
            f"{count_hash_reads['n']} hash reads for a top-{limit} answer over 30 Jobs — "
            "the range read is unbounded"
        )

    def test_gone_hash_member_is_dropped_and_the_window_still_fills(
        self, scratch_room_id, monkeypatch
    ):
        """A member whose hash is gone (transient orphan, reaped by the
        guarded repair) is dropped by ``skip_none``; the over-fetch window keeps
        the answer full while live rows exist."""
        from popoto.redis_db import POPOTO_REDIS_DB

        jobs = [Job.mint(scratch_room_id, f"task {i}") for i in range(8)]
        real_zrevrange = POPOTO_REDIS_DB.zrevrange
        orphan = b"Job:test-orphan-no-such-hash:nowhere"

        def with_orphan(key, start, end, **kwargs):
            """Splice a gone-hash member in at the head, honoring the caller's
            window width so the orphan displaces a live member rather than
            widening the read."""
            members = real_zrevrange(key, start, end, **kwargs)
            spliced = [orphan, *members]
            width = end - start + 1 if end >= 0 else len(spliced)
            return spliced[:width]

        monkeypatch.setattr(POPOTO_REDIS_DB, "zrevrange", with_orphan)

        recent = Job.recent_for_room(scratch_room_id, limit=5)

        assert len(recent) == 5
        assert [j.job_id for j in recent] == [j.job_id for j in reversed(jobs[-5:])]

    def test_recent_for_room_empty_room_is_empty(self):
        assert Job.recent_for_room("test-jobroom-none|telegram:0") == []

    def test_zero_limit_short_circuits_before_hydrating(self, scratch_room_id, count_hash_reads):
        for i in range(3):
            Job.mint(scratch_room_id, f"task {i}")
        count_hash_reads["n"] = 0

        assert Job.recent_for_room(scratch_room_id, limit=0) == []
        assert count_hash_reads["n"] == 0

    def test_read_failure_fails_open_with_a_warning(self, scratch_room_id, caplog, monkeypatch):
        """Fail-open contract: the candidate lookup never raises into the
        router — it returns [] and the caller mints."""
        import logging

        from popoto.redis_db import POPOTO_REDIS_DB

        Job.mint(scratch_room_id, "task")

        def boom(*args, **kwargs):
            raise ConnectionError("redis is unhappy")

        monkeypatch.setattr(POPOTO_REDIS_DB, "zrevrange", boom)
        with caplog.at_level(logging.WARNING, logger="models.job"):
            assert Job.recent_for_room(scratch_room_id, limit=5) == []

        assert "recent_for_room failed" in caplog.text


class TestScorePurity:
    """Sorted-set scores must be pure UTC epochs, on every write path.

    A reloaded Job's ``last_active_at`` used to come back naive, and the next
    save scored it as ``naive.timestamp()``, local time. On a UTC+07 host that
    buried a Job active seconds ago seven hours in the past, which is what made
    a live ``last_active_at__gte=now-1h`` filter return zero rows. popoto 1.9.0
    decodes a stored datetime as aware UTC; these tests pin the score itself,
    which must stay a wall-clock epoch whichever way the value decodes.
    """

    def test_resave_after_reload_keeps_the_score_a_utc_epoch(self, scratch_room_id):
        import time
        from datetime import UTC

        job = Job.mint(scratch_room_id, "check the deploy")
        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.last_active_at.tzinfo is UTC, "popoto >= 1.9.0 decodes aware UTC"

        reloaded.add_expectation("I'll report back")

        [(_member, score)] = _scores(scratch_room_id)
        assert abs(score - time.time()) < 60, (
            f"score {score} is {time.time() - score:.0f}s off wall clock — local-time skew is back"
        )

    def test_recency_filter_finds_a_just_resaved_job(self, scratch_room_id):
        """The user-visible half of the same defect (spike-2's live failure)."""
        from datetime import UTC, datetime, timedelta

        job = Job.mint(scratch_room_id, "check the deploy")
        Job.query.get(id=job.id, room_id=scratch_room_id).add_expectation("I'll report back")

        cutoff = datetime.now(tz=UTC) - timedelta(hours=1)
        found = Job.query.filter(room_id=scratch_room_id, last_active_at__gte=cutoff)

        assert [j.job_id for j in found] == [job.job_id]

    def test_scoped_save_excluding_the_field_leaves_the_score_untouched(self, scratch_room_id):
        """``backfill_open_expectations_index`` saves
        ``update_fields=["has_open_expectations"]`` under a docstring invariant
        that it never writes recency. popoto's own field-scoped save honors
        that scope: a save naming only ``has_open_expectations`` must not
        touch the SortedField score path for ``last_active_at`` at all."""
        job = Job.mint(scratch_room_id, "owes a reply")
        [(_member, score_before)] = _scores(scratch_room_id)

        job.last_active_at = job.last_active_at.replace(tzinfo=None)
        job.has_open_expectations = True
        job.save(update_fields=["has_open_expectations"])

        assert job.last_active_at.tzinfo is None, (
            "a field-scoped save touched an out-of-scope field"
        )
        assert _scores(scratch_room_id) == [(_member, score_before)]


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

    def test_renormalize_enumeration_failure_returns_zero(self, monkeypatch, caplog):
        """Fail-open contract: a broken enumeration returns (0, 0) rather than
        raising. The migration is still a caller of this classmethod
        directly, so its fail-open behavior stays covered on its own."""
        import logging

        from popoto.redis_db import POPOTO_REDIS_DB

        def boom(*args, **kwargs):
            raise ConnectionError("redis is unhappy")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(POPOTO_REDIS_DB, "sscan", boom)
            with caplog.at_level(logging.WARNING, logger="models.job"):
                assert Job.renormalize_last_active_scores() == (0, 0)
        # The guard's WARNING proves (0, 0) came from the failure branch, not
        # from an empty db satisfying the assertion vacuously.
        assert "score renormalization SKIPPED -- enumeration failed" in caplog.text

    def test_repair_indexes_reaches_backfill_after_rebuild(self, scratch_room_id, monkeypatch):
        """``repair_indexes()`` wraps its whole body in a single bare
        ``try:`` with no ``except`` before ``backfill_open_expectations_index()``,
        so nothing between the rebuild and the backfill can swallow a failure
        and still reach it — there is no hazard to anchor a fail-open
        assertion to. This is a plain happy-path spy: it fails under the
        Failure Path table's mutation (delete the
        ``cls.backfill_open_expectations_index()`` call), which is the whole
        proof that repair_indexes reaches its final step."""
        job = Job.mint(scratch_room_id, "stamp me")
        job.add_expectation("expectation A")
        drifted = Job.query.get(id=job.id, room_id=scratch_room_id)
        drifted.has_open_expectations = False
        drifted.save(update_fields=["has_open_expectations"])

        backfill_calls = []
        real_backfill = Job.backfill_open_expectations_index

        def spying_backfill():
            backfill_calls.append(True)
            return real_backfill()

        monkeypatch.setattr(Job, "backfill_open_expectations_index", spying_backfill)

        Job.repair_indexes()

        assert backfill_calls == [True]
        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.has_open_expectations is True


class TestRenormalizeBatching:
    """#2848: the score sweep is cursored and pipelined.

    Seeded under a ``test-`` room id (ORM cleanup via ``scratch_room_id``).
    Round trips are measured by counting pipeline executes and by forbidding
    the per-row ``zscore`` the old pass issued, so a regression back to one
    round trip per Job goes red rather than merely slow.
    """

    @staticmethod
    def _count_pipeline_executes(monkeypatch):
        from popoto.redis_db import POPOTO_REDIS_DB

        executes = []
        real_pipeline = POPOTO_REDIS_DB.pipeline

        def counting_pipeline(*args, **kwargs):
            pipe = real_pipeline(*args, **kwargs)
            real_execute = pipe.execute

            def counting_execute(*a, **k):
                result = real_execute(*a, **k)
                executes.append(len(result))
                return result

            pipe.execute = counting_execute
            return pipe

        monkeypatch.setattr(POPOTO_REDIS_DB, "pipeline", counting_pipeline)
        return executes

    def test_no_per_row_zscore_and_two_pipelines_per_chunk(self, scratch_room_id, monkeypatch):
        from popoto.redis_db import POPOTO_REDIS_DB

        for n in range(7):
            Job.mint(scratch_room_id, f"healthy {n}")

        def forbidden_zscore(*args, **kwargs):
            raise AssertionError("per-row ZSCORE round trip issued by the sweep")

        monkeypatch.setattr(POPOTO_REDIS_DB, "zscore", forbidden_zscore)
        executes = self._count_pipeline_executes(monkeypatch)

        scanned, repaired = Job.renormalize_last_active_scores(batch_size=3)

        assert scanned >= 7
        assert repaired == 0
        # Every pipeline is bounded by the chunk size, and a healthy sweep
        # issues exactly two per chunk (HMGET, then ZSCORE): no repair writes.
        assert executes, "the sweep must go through pipelines"
        assert max(executes) <= 3
        chunks = -(-scanned // 3)
        assert len(executes) == 2 * chunks

    def test_skew_is_repaired_across_chunk_boundaries(self, scratch_room_id):
        from popoto.redis_db import POPOTO_REDIS_DB

        from utils.utc import to_unix_ts

        jobs = [Job.mint(scratch_room_id, f"row {n}") for n in range(5)]
        partition = _partition_key(scratch_room_id)
        skewed = [jobs[0], jobs[4]]  # first and last: different chunks at batch_size=2
        for job in skewed:
            member = job.db_key.redis_key
            true_score = POPOTO_REDIS_DB.zscore(partition, member)
            POPOTO_REDIS_DB.zadd(partition, {member: true_score - UTC_PLUS_7_REBUILD_SKEW_SECONDS})

        scanned, repaired = Job.renormalize_last_active_scores(batch_size=2)

        assert scanned >= 5
        assert repaired == 2
        for job in jobs:
            fresh = Job.query.get(id=job.id, room_id=scratch_room_id)
            assert POPOTO_REDIS_DB.zscore(partition, job.db_key.redis_key) == pytest.approx(
                to_unix_ts(fresh.last_active_at), abs=1.0
            )
        # Idempotent: the second pass costs reads alone.
        assert Job.renormalize_last_active_scores(batch_size=2)[1] == 0

    def test_class_set_member_without_a_hash_is_skipped(self, scratch_room_id, caplog):
        """A class-set pointer whose hash is gone decodes to nothing; the
        sweep skips it without raising and still repairs its neighbours."""
        import logging

        from popoto.redis_db import POPOTO_REDIS_DB

        job = Job.mint(scratch_room_id, "survivor")
        class_set_key = Job._meta.db_class_set_key.redis_key
        ghost = f"Job:test-ghost-{uuid.uuid4().hex[:8]}:{scratch_room_id}"
        POPOTO_REDIS_DB.sadd(class_set_key, ghost)
        try:
            with caplog.at_level(logging.WARNING, logger="models.job"):
                scanned, _repaired = Job.renormalize_last_active_scores(batch_size=2)
            assert scanned >= 1
            assert "SKIP" not in caplog.text
            assert Job.query.get(id=job.id, room_id=scratch_room_id) is not None
        finally:
            POPOTO_REDIS_DB.srem(class_set_key, ghost)

    def test_one_failed_hmget_pipeline_skips_that_chunk_only(
        self, scratch_room_id, monkeypatch, caplog
    ):
        import logging

        from popoto.redis_db import POPOTO_REDIS_DB

        for n in range(4):
            Job.mint(scratch_room_id, f"row {n}")

        real_pipeline = POPOTO_REDIS_DB.pipeline
        calls = {"n": 0}

        def flaky_pipeline(*args, **kwargs):
            pipe = real_pipeline(*args, **kwargs)
            calls["n"] += 1
            if calls["n"] == 1:
                real_execute = pipe.execute

                def failing_execute(*a, **k):
                    real_execute(*a, **k)
                    raise ConnectionError("first hmget pipeline lost")

                pipe.execute = failing_execute
            return pipe

        monkeypatch.setattr(POPOTO_REDIS_DB, "pipeline", flaky_pipeline)
        with caplog.at_level(logging.WARNING, logger="models.job"):
            scanned, _repaired = Job.renormalize_last_active_scores(batch_size=2)

        assert "SKIP batch of 2 -- hmget pipeline ConnectionError" in caplog.text
        # The first chunk (2 rows) was dropped; the remaining chunk(s) were swept.
        assert scanned >= 2


class TestDriftCoverage:
    """Risk 3: drift detection must not silently narrow — Job registers."""

    def test_job_registered_in_drift_coverage(self):
        from agent.index_drift import covered_model_names

        assert "Job" in covered_model_names()

    def test_job_exported_from_models_package(self):
        import models

        assert "Job" in models.__all__
